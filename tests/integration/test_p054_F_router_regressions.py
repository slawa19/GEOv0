"""054 slice F: regressions of the payment router (`app/core/payments/router.py`), on real HTTP and PostgreSQL.

Each test was the review's witness of a defect (green == defect) and now asserts the intended behaviour: payments
commit (or refuse) as the protocol says, read back from the database. Mode B: a real committing session.
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_create_request,
    register_and_login,
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
    assert settings.ROUTING_MAX_PATHS == 3 and settings.ROUTING_MAX_HOPS == 6 and settings.FEATURE_FLAGS_MULTIPATH_ENABLED
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
