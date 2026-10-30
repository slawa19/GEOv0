"""054 slice F: regressions of the payment router (`app/core/payments/router.py`), on real HTTP and PostgreSQL.

Each test was the review's witness of a defect (green == defect) and now asserts the intended behaviour: payments
commit (or refuse) as the protocol says, read back from the database. Mode B: a real committing session.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    expected_state_of,
    register_and_login,
    signed_trustline_update,
)


async def _world(client, db_session, names: str, prefix: str):
    code = prefix + uuid.uuid4().hex[:6].upper()
    db_session.add(Equivalent(code=code, precision=2, is_active=True))
    await db_session.commit()
    people = {n: await register_and_login(client, f"P54F_{n}_{code}") for n in names}
    ids = dict((await db_session.execute(select(Participant.pid, Participant.id).where(
        Participant.pid.in_([p["pid"] for p in people.values()])))).all())
    for person in people.values():
        person["id"] = ids[person["pid"]]
    await db_session.commit()
    return code, people, sessionmaker_of(db_session)


async def _line(client, creditor, debtor, code, limit: str, policy: dict | None = None) -> str:
    key = SigningKey(base64.b64decode(creditor["priv"]))
    body = {"to": debtor["pid"], "equivalent": code, "limit": limit,
            "signature": _sign_trustline_create_request(signing_key=key, to_pid=debtor["pid"], equivalent=code,
                                                        limit=limit, policy=policy)}
    if policy is not None:
        body["policy"] = policy
    r = await client.post("/api/v1/trustlines", headers=creditor["headers"], json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _http_pay(client, payer, payee, code, amount: str):
    key = SigningKey(base64.b64decode(payer["priv"]))
    tx_id = str(uuid.uuid4())
    body = {"tx_id": tx_id, "to": payee["pid"], "equivalent": code, "amount": amount,
            "signature": _sign_payment_request(signing_key=key, tx_id=tx_id, from_pid=payer["pid"],
                                               to_pid=payee["pid"], equivalent=code, amount=amount)}
    r = await client.post("/api/v1/payments", headers=payer["headers"], json=body)
    return tx_id, r.status_code, r.json()


async def _debts(factory, code, people) -> dict[str, str]:
    name = {p["id"]: n for n, p in people.items()}
    async with factory() as s:
        eq_id = (await s.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one()
        rows = (await s.execute(select(Debt.debtor_id, Debt.creditor_id, Debt.amount)
                                .where(Debt.equivalent_id == eq_id))).all()
    return {f"{name[d]}->{name[c]}": str(Decimal(a).quantize(Decimal("0.01"))) for d, c, a in rows if Decimal(a) != 0}


async def _tx_state(factory, tx_id):
    async with factory() as s:
        return list((await s.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))).scalars())


def _routes(body) -> list:
    return [[r["path"], r["amount"]] for r in (body.get("routes") or [])]


# 054-F-1: three thin short paths (capacity 1 each) must not use up `max_paths` while one 3-hop path carries the
# whole payment (protocol §6.3.2: the first path is the one of the largest capacity; §6.3.4: split by capacity).
@MODE_B
@pytest.mark.asyncio
async def test_f1_a_payment_one_wide_path_carries_is_not_split_over_thin_short_paths(client, db_session):
    assert settings.ROUTING_MAX_PATHS == 3 and settings.ROUTING_MAX_HOPS == 6
    assert settings.FEATURE_FLAGS_MULTIPATH_ENABLED
    code, p, factory = await _world(client, db_session, "PQABCD", "F1")
    # creditor -> debtor (a payment debtor -> creditor uses the line creditor -> debtor)
    for cred, debt, lim in (("Q", "P", "1"), ("A", "P", "1"), ("Q", "A", "1"), ("B", "P", "1"), ("Q", "B", "1"),
                            ("C", "P", "100"), ("D", "C", "100"), ("Q", "D", "100")):
        await _line(client, p[cred], p[debt], code, lim)

    cap = await client.get("/api/v1/payments/capacity", headers=p["P"]["headers"],
                           params={"to": p["Q"]["pid"], "equivalent": code, "amount": "10"})
    assert cap.status_code == 200 and cap.json()["can_pay"] is True, cap.text
    assert cap.json()["routes_count"] == 1 and cap.json()["estimated_hops"] == 3, cap.text

    tx_id, status, body = await _http_pay(client, p["P"], p["Q"], code, "10.00")
    assert status == 200 and body["status"] == "COMMITTED", body
    path = [p[n]["pid"] for n in "PCDQ"]
    assert _routes(body) == [[path, "10.00"]], body
    assert await _tx_state(factory, tx_id) == ["COMMITTED"]
    assert await _debts(factory, code, p) == {"P->C": "10.00", "C->D": "10.00", "D->Q": "10.00"}


# 054-F-3: max-flow searched augmenting paths with a node-level `visited`, while `blocked_participants` depends on the
# whole route: the first path to M (via X) was refused at M->Q (Q's line blocks X), the path via Y, Z never tried, and
# max-flow said 0 - the refusal text of a payment (`max_available`) and the simulator's targets with it.
@MODE_B
@pytest.mark.asyncio
async def test_f3_max_flow_finds_the_path_a_route_dependent_policy_leaves(client, db_session):
    code, p, factory = await _world(client, db_session, "PXYMZQ", "F3")
    for cred, debt in (("X", "P"), ("Y", "P"), ("M", "X"), ("Z", "Y"), ("M", "Z")):
        await _line(client, p[cred], p[debt], code, "10")
    await _line(client, p["Q"], p["M"], code, "10", policy={"blocked_participants": [p["X"]["pid"]]})

    mf = await client.get("/api/v1/payments/max-flow", headers=p["P"]["headers"],
                          params={"to": p["Q"]["pid"], "equivalent": code})
    assert mf.status_code == 200 and Decimal(mf.json()["max_amount"]) == Decimal("10"), mf.text

    # The refusal of 10.01 states what can be sent now (`service.py`, `max_available`): 10, not "no route".
    _, status, body = await _http_pay(client, p["P"], p["Q"], code, "10.01")
    assert status == 400 and body["error"]["code"] == "E002", body
    assert Decimal(body["error"]["details"]["max_available"]) == Decimal("10"), body

    tx_id, status, body = await _http_pay(client, p["P"], p["Q"], code, "10.00")
    assert status == 200 and body["status"] == "COMMITTED", body
    assert await _debts(factory, code, p) == {"P->Y": "10.00", "Y->Z": "10.00", "Z->M": "10.00", "M->Q": "10.00"}


def _barrier_reader(factory, read_done: asyncio.Event, release: asyncio.Event):
    """The payment's route reader (`PaymentService._build_route_graph`) with a barrier after its last read (of the
    participants): the build has read every line, and is held before it publishes."""

    @asynccontextmanager
    async def reader():
        async with factory() as session:
            original = session.execute

            async def execute(statement, *a, **k):
                result = await original(statement, *a, **k)
                if any(d.get("entity") is Participant for d in getattr(statement, "column_descriptions", [])):
                    read_done.set()
                    await asyncio.wait_for(release.wait(), 20)
                return result

            session.execute = execute  # type: ignore[method-assign]
            yield session

    return reader


# 054-F-2: a shared-cache build that read the lines BEFORE a committed policy PATCH finished AFTER the PATCH's eviction
# and published the old policy; the next payment routed over B, the core refused it on policy (E001, ABORTED, no
# re-route). Now a build older than the last eviction of its equivalent is not published; its waiter builds afresh.
@MODE_B
@pytest.mark.asyncio
async def test_f2_a_build_older_than_the_eviction_is_not_published_and_the_next_payment_commits(client, db_session):
    assert settings.ROUTING_GRAPH_CACHE_TTL_SECONDS == 2  # the production default, not raised for the stand
    code, p, factory = await _world(client, db_session, "ABTCD", "F2")
    line_ba = await _line(client, p["B"], p["A"], code, "100")
    for cred, debt in (("T", "B"), ("C", "A"), ("D", "C"), ("T", "D")):
        await _line(client, p[cred], p[debt], code, "100")

    read_done, release = asyncio.Event(), asyncio.Event()
    async with factory() as own, factory() as other:
        leader_router, waiter_router = PaymentRouter(own), PaymentRouter(other)
        leader = asyncio.create_task(leader_router.build_graph(
            code, use_shared_cache=True, reader=_barrier_reader(factory, read_done, release)))
        await asyncio.wait_for(read_done.wait(), 20)
        # A second caller of the shared cache waits for the build in flight (single-flight).
        waiter = asyncio.create_task(waiter_router.build_graph(code, use_shared_cache=True))
        await asyncio.sleep(0)
        assert not waiter.done()
        patched = await client.patch(f"/api/v1/trustlines/{line_ba}", headers=p["B"]["headers"],
                                     json=signed_trustline_update(
                                         signing_key=SigningKey(base64.b64decode(p["B"]["priv"])),
                                         trustline_id=line_ba, policy={"can_be_intermediate": False},
                                         expected=await expected_state_of(client, p["B"]["headers"], line_ba)))
        assert patched.status_code == 200, patched.text
        release.set()
        await asyncio.wait_for(leader, 20)
        await asyncio.wait_for(waiter, 20)

    a, b = p["A"]["pid"], p["B"]["pid"]
    # The leader routes on what it read (a hint, re-checked by the core) but did not publish it ...
    assert leader_router.edge_no_transit[a][b] == frozenset()
    # ... so its waiter did not take it: it built afresh and published the patched policy.
    assert waiter_router.from_cache is False and waiter_router.edge_no_transit[a][b] == frozenset({b})
    assert PaymentRouter._graph_cache[code][6][a][b] == frozenset({b})

    tx_id, status, body = await _http_pay(client, p["A"], p["T"], code, "10.00")
    assert status == 200 and body["status"] == "COMMITTED", body
    assert await _tx_state(factory, tx_id) == ["COMMITTED"]
    assert await _debts(factory, code, p) == {"A->C": "10.00", "C->D": "10.00", "D->T": "10.00"}


# 054-F-2, the same race without a waiter: nothing is published, and the payment's own build reads the patched lines.
@MODE_B
@pytest.mark.asyncio
async def test_f2_without_a_waiter_the_stale_build_leaves_the_cache_empty(client, db_session):
    code, p, factory = await _world(client, db_session, "ABTCD", "F2")
    line_ba = await _line(client, p["B"], p["A"], code, "100")
    for cred, debt in (("T", "B"), ("C", "A"), ("D", "C"), ("T", "D")):
        await _line(client, p[cred], p[debt], code, "100")

    read_done, release = asyncio.Event(), asyncio.Event()
    async with factory() as own:
        leader = asyncio.create_task(PaymentRouter(own).build_graph(
            code, use_shared_cache=True, reader=_barrier_reader(factory, read_done, release)))
        await asyncio.wait_for(read_done.wait(), 20)
        patched = await client.patch(f"/api/v1/trustlines/{line_ba}", headers=p["B"]["headers"],
                                     json=signed_trustline_update(
                                         signing_key=SigningKey(base64.b64decode(p["B"]["priv"])),
                                         trustline_id=line_ba, policy={"can_be_intermediate": False},
                                         expected=await expected_state_of(client, p["B"]["headers"], line_ba)))
        assert patched.status_code == 200, patched.text
        release.set()
        await asyncio.wait_for(leader, 20)
    assert code not in PaymentRouter._graph_cache

    tx_id, status, body = await _http_pay(client, p["A"], p["T"], code, "10.00")
    assert status == 200 and body["status"] == "COMMITTED", body
    assert await _debts(factory, code, p) == {"A->C": "10.00", "C->D": "10.00", "D->T": "10.00"}
