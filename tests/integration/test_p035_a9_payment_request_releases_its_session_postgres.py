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
session is the one whose FIRST statement is the authentication SELECT; it must have run NOTHING else and begun
exactly one transaction - so no implicit reload of the participant, no second use of the session after it was
closed.

NO PRODUCT TIMER DECIDES THIS TEST, AND IT SLEEPS NOWHERE (§15 review of A9, 2026-10-09). A held payment sits inside
the product's binding timeout (`PREPARE_TIMEOUT_SECONDS`, 3 s) and the payment's total deadline (10 s); on a slow
machine the fifteenth arrival could come after the first payment had already timed out, and a correct release would
read red. The four timers a held payment lives under are therefore raised for the test to `_PRODUCT_TIMERS`
(existing settings, patched; the product is not changed), and every wait is an EVENT with the test's own budget
`_BUDGET_SECONDS`: the fifteenth arrival sets one, the pool's last check-in sets the other. The budget is how long
a broken run may take to say so; a correct run waits for nothing but the events.

NOTHING IS LEFT BEHIND ON ANY EXIT (same review). Every resource is registered with one `AsyncExitStack` the moment
it exists - the engine, the listeners on `Session` (process-wide: monkeypatch does not restore SQLAlchemy events),
the observer connection - and the first thing the stack does on the way out is open the gate, cancel every request
task that was ever started and wait for all of them, before the observer and the engine are closed. The settings,
the engine and factory of `app.db.session`, the dependency overrides and the binding hook are monkeypatch's and
are restored by it after that. The listeners are module-level functions, so "is it still registered" can be asked
from outside (`sqlalchemy.event.contains`).

NOT SEEN HERE: Redis (absent in the tier; the per-payer lock is a no-op, which is why the payers are distinct);
several workers; the other routes that hold authentication while something else opens sessions
(`POST /clearing/auto`, the simulator's Interact payment) - recorded for the backlog, not changed by A9.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
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

#: How long a BROKEN run may wait before it says so. A correct run waits for events, not for this.
_BUDGET_SECONDS = 120.0

#: The product timers a payment held at the barrier lives under, raised so that none of them can end a held payment
#: before the test opens the gate. `DB_POOL_TIMEOUT_SECONDS` is deliberately NOT here: the pool is the subject.
_PRODUCT_TIMERS = {
    "PREPARE_TIMEOUT_SECONDS": 900,
    "PAYMENT_TOTAL_TIMEOUT_SECONDS": 900,
    "COMMIT_TIMEOUT_SECONDS": 900,
    "ROUTING_PATH_FINDING_TIMEOUT_MS": 900_000,
}


class _Trap:
    """Every ORM statement and every transaction begin on one engine, per session."""

    def __init__(self, engine) -> None:
        self.sync_engine = engine.sync_engine
        self.statements: dict[int, list[str]] = {}
        self.begins: dict[int, int] = {}
        self.kept: list[Session] = []  # strong references, so an id is never reused within the test


_TRAP: _Trap | None = None


def _on_execute(state) -> None:
    trap = _TRAP
    if trap is None or state.session.get_bind() is not trap.sync_engine:
        return
    if id(state.session) not in trap.statements:
        trap.kept.append(state.session)
    trap.statements.setdefault(id(state.session), []).append(" ".join(str(state.statement).split()))


def _on_begin(session, _transaction, _connection) -> None:
    trap = _TRAP
    if trap is not None and session.get_bind() is trap.sync_engine:
        trap.begins[id(session)] = trap.begins.get(id(session), 0) + 1


def _disarm_trap() -> None:
    global _TRAP
    _TRAP = None


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
    global _TRAP

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

    for name, value in _PRODUCT_TIMERS.items():
        monkeypatch.setattr(settings, name, value)  # raising: the setting must exist

    loop = asyncio.get_running_loop()
    launched: list[asyncio.Task] = []  # every request task ever started, for the unconditional drain
    gate = {"arrived": 0, "open": asyncio.Event(), "all_arrived": asyncio.Event()}
    pool_idle = asyncio.Event()

    async def drain_requests() -> None:
        """Open the gate, cancel what still runs, and wait for every request task - whatever happened."""

        gate["open"].set()
        for task in launched:
            if not task.done():
                task.cancel()
        await asyncio.gather(*launched, return_exceptions=True)

    async with contextlib.AsyncExitStack() as stack:
        # --- the product's wiring on a real pool over the clone ---------------------------------------------
        clone = db_session.info["geo_committed_database"].sessionmaker.kw["bind"].url
        monkeypatch.setattr(settings, "DATABASE_URL", clone.render_as_string(hide_password=False))
        engine = product_db._create_engine()
        stack.push_async_callback(engine.dispose)
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

        def on_checkin(_connection, _record) -> None:
            # The pool's counter moves after this event; look at it on the next turn of the loop.
            loop.call_soon(lambda: pool.checkedout() == 0 and pool_idle.set())

        event.listen(engine.sync_engine, "checkin", on_checkin)  # dies with the engine

        # --- the trap: process-wide listeners, removed by the stack on any exit ------------------------------
        trap = _TRAP = _Trap(engine)
        stack.callback(_disarm_trap)
        event.listen(Session, "do_orm_execute", _on_execute)
        stack.callback(event.remove, Session, "do_orm_execute", _on_execute)
        event.listen(Session, "after_begin", _on_begin)
        stack.callback(event.remove, Session, "after_begin", _on_begin)

        # --- the gate at the entry of `_bind_payment` --------------------------------------------------------
        original_bind = PaymentService._bind_payment

        async def held(service, *args, **kwargs):
            gate["arrived"] += 1
            if gate["arrived"] == PAYMENTS:
                gate["all_arrived"].set()
            await gate["open"].wait()
            return await original_bind(service, *args, **kwargs)

        monkeypatch.setattr(PaymentService, "_bind_payment", held)

        observer = await asyncpg.connect(
            host=clone.host, port=clone.port or 5432, user=clone.username, password=clone.password,
            database=clone.database,
        )
        stack.push_async_callback(observer.close)
        # Registered LAST, so it runs FIRST: requests are drained before the observer and the engine close.
        stack.push_async_callback(drain_requests)

        async def pool_is_idle(label: str) -> None:
            pool_idle.clear()
            if pool.checkedout() == 0:
                return
            try:
                await asyncio.wait_for(pool_idle.wait(), _BUDGET_SECONDS)
            except asyncio.TimeoutError:
                raise AssertionError(f"{label}: {pool.checkedout()} connection(s) not returned to the pool") from None

        async def round_of_fifteen(label: str) -> list[dict]:
            gate["arrived"] = 0
            gate["open"].clear()
            gate["all_arrived"].clear()
            bodies = [_payment(payer, payee) for payer, payee in pairs]
            tasks = [
                asyncio.create_task(client.post("/api/v1/payments", json=body, headers=payer["headers"]))
                for body, (payer, _payee) in zip(bodies, pairs)
            ]
            launched.extend(tasks)
            # Until the fifteenth arrival - or until any request ENDS, which before the gate opens is a failure.
            arrival = asyncio.ensure_future(gate["all_arrived"].wait())
            launched.append(arrival)
            await asyncio.wait({arrival, *tasks}, timeout=_BUDGET_SECONDS, return_when=asyncio.FIRST_COMPLETED)
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
            gate["open"].set()
            answers = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), _BUDGET_SECONDS)
            outcomes = sorted(
                (answer.status_code, answer.json().get("status")) if not isinstance(answer, BaseException)
                else (0, repr(answer))
                for answer in answers
            )
            assert outcomes == [(200, "COMMITTED")] * PAYMENTS, f"{label}: {outcomes}"
            await pool_is_idle(label)
            return bodies

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
        await pool_is_idle("after the replay and the refusals")

    # --- the trap's verdict ---------------------------------------------------------------------------------
    # A request session STARTS with the authentication SELECT; an attempt's session starts with
    # `SHOW transaction_isolation` and looks participants up by pid only later.
    request_sessions = {key: ran for key, ran in trap.statements.items() if _AUTHENTICATION in ran[0]}
    assert len(request_sessions) == 2 * PAYMENTS + 3, (
        f"premise: one authenticating request session per request, got {len(request_sessions)}"
    )
    reused = {key: ran for key, ran in request_sessions.items() if len(ran) != 1 or trap.begins.get(key) != 1}
    assert not reused, (
        "a request session ran something besides the one authentication SELECT, or began a second transaction "
        f"after it was closed: {list(reused.values())[:2]}"
    )
