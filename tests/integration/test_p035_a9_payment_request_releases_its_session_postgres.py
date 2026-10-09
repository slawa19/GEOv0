"""035 A9 (F-035-8): `POST /payments` gives its request session back before the payment runs.

WHAT WAS WRONG (measured, `scripts/measure_payment_pool_usage.py`, 2026-10-09). The request's `get_db` session
authenticates the caller with one SELECT and then stayed in that transaction for the whole request, while
`PaymentService.pay` opens a session of its own per attempt. Every payment held TWO pool connections, one of them
`idle in transaction` and doing nothing. With the pool's 15 connections (`DB_POOL_SIZE` 5 + `DB_MAX_OVERFLOW` 10),
fifteen simultaneous payments each held one and waited for a second that could not come: none committed, and every
caller was refused when `DB_POOL_TIMEOUT_SECONDS` ran out.

THE FIX (decision `F-035-8-DECISION: RELEASE-REQUEST-SESSION`): the route takes the payer's id and CLOSES the
request session before it calls the payment. The authoritative "is this participant active" check is not the
authentication's - it is made inside the payment under the participant lock
(`MoneyBoundary.refuse_suspended_participants`) - so nothing is weakened.

THE STAND IS THE PRODUCT'S WIRING ON A REAL POOL. The tier's `client` fixture replaces `get_db` and the payment
session factory with the test's own session; this test REMOVES both overrides after the setup and gives
`app.db.session` an engine built by the product's own `_create_engine()` on the mode-B clone - the settings' pool,
15 connections. Requests then go through the real `get_db`, the real authentication and the real
`get_payment_session_factory`.

WHAT IS HELD AND READ. Fifteen payments, each from its own payer to its own payee, are sent at once and every one
is held at the entry of `PaymentService._bind_payment` (after routing, the attempt's session open). There:
* all fifteen arrived (on the base commit: none - the red);
* the pool has fifteen connections checked out, one per payment (two per payment would not fit);
* `pg_stat_activity`, read through a connection outside the pool, shows no session left `idle in transaction` on
  the authentication SELECT.
Then the gate opens: fifteen `COMMITTED`, and the pool is back to zero. The round runs twice: the first is cold
(no payment has been routed on this clone yet), the second finds whatever the first left cached.

THE TRAP ON THE REQUEST SESSION. Every ORM statement on the product engine is recorded per session. A request
session is the one whose FIRST statement is the authentication SELECT; it must have run NOTHING else and begun exactly one
transaction - so no implicit reload of the participant, no second use of the session after it was closed.

NOT SEEN HERE: Redis (absent in the tier; the per-payer lock is a no-op, which is why the payers are distinct);
several workers; the other routes that hold authentication while something else opens sessions
(`POST /clearing/auto`, the simulator's Interact payment) - recorded for the backlog, not changed by A9.
"""

from __future__ import annotations

import asyncio
import base64
import uuid

import asyncpg
import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

import app.db.session as product_db
from app.api.deps import get_db, get_payment_session_factory
from app.config import settings
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.main import app
from tests.conftest import MODE_B
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
)

PAYMENTS = 15
_AUTHENTICATION = "WHERE participants.pid ="


def _payment(payer: dict, payee: dict, amount: str = "1.00") -> dict:
    tx_id = str(uuid.uuid4())
    return {
        "tx_id": tx_id,
        "to": payee["pid"],
        "equivalent": "USD",
        "amount": amount,
        "signature": _sign_payment_request(
            signing_key=SigningKey(base64.b64decode(payer["priv"])),
            tx_id=tx_id,
            from_pid=payer["pid"],
            to_pid=payee["pid"],
            equivalent="USD",
            amount=amount,
        ),
    }


@MODE_B
@pytest.mark.asyncio
async def test_fifteen_simultaneous_payments_fit_a_pool_of_fifteen(client: AsyncClient, db_session, monkeypatch) -> None:
    db_session.add(Equivalent(code="USD", description="USD", precision=2))
    await db_session.commit()

    # --- setup through the tier's client: fifteen payer -> payee pairs, each over its own trust line ---------
    pairs = []
    for index in range(PAYMENTS):
        payer = await register_and_login(client, f"A9 Payer {index}")
        payee = await register_and_login(client, f"A9 Payee {index}")
        line = await client.post(
            "/api/v1/trustlines",
            json={
                "to": payer["pid"],
                "equivalent": "USD",
                "limit": "100.00",
                "signature": _sign_trustline_create_request(
                    signing_key=SigningKey(base64.b64decode(payee["priv"])),
                    to_pid=payer["pid"],
                    equivalent="USD",
                    limit="100.00",
                ),
            },
            headers=payee["headers"],
        )
        assert line.status_code == 201, line.text
        pairs.append((payer, payee))
    await db_session.commit()

    # --- the product's wiring on a real pool over the clone -------------------------------------------------
    clone = db_session.info["geo_committed_database"].sessionmaker.kw["bind"].url
    monkeypatch.setattr(settings, "DATABASE_URL", clone.render_as_string(hide_password=False))
    engine = product_db._create_engine()
    pool = engine.sync_engine.pool
    assert (pool.size(), settings.DB_MAX_OVERFLOW) == (5, 10), "the stand is written for the settings' pool of 15"
    monkeypatch.setattr(product_db, "engine", engine)
    monkeypatch.setattr(
        product_db,
        "AsyncSessionLocal",
        async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False),
    )
    monkeypatch.delitem(app.dependency_overrides, get_db)
    monkeypatch.delitem(app.dependency_overrides, get_payment_session_factory)

    # --- the trap: every ORM statement and every transaction begin, per session of the product engine -------
    statements: dict[int, list[str]] = {}
    begins: dict[int, int] = {}
    kept: list[Session] = []  # strong references, so an id is never reused within the test

    def on_execute(state) -> None:
        if state.session.get_bind() is engine.sync_engine:
            if id(state.session) not in statements:
                kept.append(state.session)
            statements.setdefault(id(state.session), []).append(" ".join(str(state.statement).split()))

    def on_begin(session, _transaction, _connection) -> None:
        if session.get_bind() is engine.sync_engine:
            begins[id(session)] = begins.get(id(session), 0) + 1

    event.listen(Session, "do_orm_execute", on_execute)
    event.listen(Session, "after_begin", on_begin)

    # --- the gate at the entry of `_bind_payment` -----------------------------------------------------------
    original_bind = PaymentService._bind_payment
    gate = {"arrived": 0, "open": asyncio.Event()}

    async def held(service, *args, **kwargs):
        gate["arrived"] += 1
        await gate["open"].wait()
        return await original_bind(service, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_bind_payment", held)

    observer = await asyncpg.connect(
        host=clone.host, port=clone.port or 5432, user=clone.username, password=clone.password, database=clone.database
    )

    async def round_of_fifteen(label: str) -> list[dict]:
        gate["arrived"] = 0
        gate["open"].clear()
        bodies = [_payment(payer, payee) for payer, payee in pairs]
        tasks = [
            asyncio.create_task(client.post("/api/v1/payments", json=body, headers=payer["headers"]))
            for body, (payer, _payee) in zip(bodies, pairs)
        ]
        try:
            waited = 0.0
            while gate["arrived"] < PAYMENTS and waited < 10.0 and not any(task.done() for task in tasks):
                await asyncio.sleep(0.05)
                waited += 0.05
            held_authentication = await observer.fetchval(
                "select count(*) from pg_stat_activity where datname = current_database() "
                "and pid <> pg_backend_pid() and state = 'idle in transaction' and query like $1",
                f"%{_AUTHENTICATION}%",
            )
            in_transaction = await observer.fetchval(
                "select count(*) from pg_stat_activity where datname = current_database() "
                "and pid <> pg_backend_pid() and state = 'idle in transaction'"
            )
            reading = (gate["arrived"], pool.checkedout(), held_authentication, in_transaction)
            assert reading == (PAYMENTS, PAYMENTS, 0, PAYMENTS), (
                f"{label}: (payments at the binding barrier, pool connections checked out, sessions idle in "
                f"transaction on the authentication SELECT, sessions idle in transaction) = {reading}, expected "
                f"{(PAYMENTS, PAYMENTS, 0, PAYMENTS)}: each payment must hold ONE connection - its attempt's - "
                f"and none may keep the request's authentication transaction open"
            )
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            gate["open"].set()
        answers = await asyncio.gather(*tasks)
        outcomes = sorted((answer.status_code, answer.json().get("status")) for answer in answers)
        assert outcomes == [(200, "COMMITTED")] * PAYMENTS, f"{label}: {outcomes}"
        for _ in range(100):  # an answer is sent before its request's session is closed
            if pool.checkedout() == 0:
                break
            await asyncio.sleep(0.02)
        assert pool.checkedout() == 0, f"{label}: {pool.checkedout()} connection(s) not returned to the pool"
        return bodies

    try:
        await round_of_fifteen("cold round")
        bodies = await round_of_fifteen("second round")

        # --- as before: a replay answers the stored result, a refusal before admission is still a refusal ---
        payer, payee = pairs[0]
        replay = await client.post("/api/v1/payments", json=bodies[0], headers=payer["headers"])
        assert replay.status_code == 200 and replay.json()["status"] == "COMMITTED", replay.text
        assert replay.json()["tx_id"] == bodies[0]["tx_id"]
        too_much = await client.post("/api/v1/payments", json=_payment(payer, payee, "500.00"), headers=payer["headers"])
        assert too_much.status_code == 400 and too_much.json()["error"]["details"]["reason"] == "insufficient_capacity"
        unsigned = dict(_payment(payer, payee), signature="AAAA")
        refused = await client.post("/api/v1/payments", json=unsigned, headers=payer["headers"])
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "E005", refused.text
        assert pool.checkedout() == 0
    finally:
        event.remove(Session, "do_orm_execute", on_execute)
        event.remove(Session, "after_begin", on_begin)
        await observer.close()
        await engine.dispose()

    # --- the trap's verdict ---------------------------------------------------------------------------------
    # A request session STARTS with the authentication SELECT; an attempt's session starts with
    # `SHOW transaction_isolation` and looks participants up by pid only later.
    request_sessions = {key: ran for key, ran in statements.items() if _AUTHENTICATION in ran[0]}
    assert len(request_sessions) == 2 * PAYMENTS + 3, (
        f"premise: one authenticating request session per request, got {len(request_sessions)}"
    )
    reused = {key: ran for key, ran in request_sessions.items() if len(ran) != 1 or begins.get(key) != 1}
    assert not reused, (
        "a request session ran something besides the one authentication SELECT, or began a second transaction "
        f"after it was closed: {list(reused.values())[:2]}"
    )
