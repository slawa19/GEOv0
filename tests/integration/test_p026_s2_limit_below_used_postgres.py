"""026 S2 (`T2602`): a creditor may lower trust below the current debt (F-026-1, R-026-1, R-026-4).

THE RULE (owner, 2026-09-29; spec 026, fork 5 and fork 7): `PATCH /trustlines/{id}` below `used` is a change
of TRUST, not of debt - accepted, no debt written off or moved; `available = limit - used` goes negative and is
served signed. The debt above the lowered limit cannot grow (the S1 growth gate on the write path) and can
still be reduced - by a payment the other way and by clearing.

HOW THE STATE IS REACHED. Every debt is created by a real payment within the limit of its day; the excess is
produced only by the real signed `PATCH` of the public API. No row of `debts` or `trust_lines` is written by
hand (Verification plan §4). The clearing is the production pass (`run_clearing_pass`).

WHAT THIS DOES NOT SEE. Concurrency of the PATCH with a payment (the row lock of fork 5) - the stand is serial.
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from app.config import settings
from app.core.clearing.runner import run_clearing_pass
from app.core.invariants import InvariantChecker
from app.core.ledger.book import Book, PaymentFlow
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import GeoException, IntegrityViolationException
from tests.conftest import MODE_B, sessionmaker_of
from tests.debt_setup import writer_operation
from tests.integration.test_scenarios import (
    _sign_trustline_create_request,
    _sign_trustline_update_request,
    register_and_login,
)
from tests.p019_support import require_target


async def _world(client, db_session):
    """A, B, C with keys; one equivalent; lines A->B 100, B->C 100, C->A 100 by the signed API."""

    code = "S2" + uuid.uuid4().hex[:8].upper()
    db_session.add(Equivalent(code=code, symbol="S", precision=2, metadata_={}, is_active=True))
    await db_session.commit()
    people = {r: await register_and_login(client, f"P026S2{r}") for r in ("A", "B", "C")}
    ids = dict((await db_session.execute(select(Participant.pid, Participant.id).where(
        Participant.pid.in_([p["pid"] for p in people.values()])))).all())
    lines = {}
    for creditor, debtor in (("A", "B"), ("B", "C"), ("C", "A")):
        key = SigningKey(base64.b64decode(people[creditor]["priv"]))
        body = {"to": people[debtor]["pid"], "equivalent": code, "limit": "100"}
        r = await client.post("/api/v1/trustlines", headers=people[creditor]["headers"], json={
            **body, "signature": _sign_trustline_create_request(signing_key=key, to_pid=body["to"],
                                                                equivalent=code, limit="100")})
        assert r.status_code == 201, r.text
        lines[creditor + debtor] = r.json()["id"]
    await db_session.commit()
    for p in people.values():
        p["id"] = ids[p["pid"]]
    return code, people, lines, sessionmaker_of(db_session)


async def _pay(factory, payer, payee, code: str, amount: str) -> bool:
    """A real payment; True if it committed, False if refused."""

    request = PaymentCreateRequest(tx_id="tx-" + uuid.uuid4().hex, to=payee["pid"], equivalent=code,
                                   amount=amount, signature="__internal__")
    try:
        result = await PaymentService.pay(factory, payer["id"], request, require_signature=False)
    except GeoException:
        return False
    finally:
        PaymentRouter.invalidate_cache(code)
    return result.status == "COMMITTED"


async def _debts(factory, code: str) -> dict[tuple, Decimal]:
    async with factory() as s:
        eq_id = (await s.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one()
        rows = (await s.execute(select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
            Debt.equivalent_id == eq_id))).all()
    return {(d, c): Decimal(str(a)) for d, c, a in rows if Decimal(str(a)) != 0}


async def _patch_limit(client, creditor, line_id: str, limit: str):
    key = SigningKey(base64.b64decode(creditor["priv"]))
    return await client.patch(f"/api/v1/trustlines/{line_id}", headers=creditor["headers"], json={
        "limit": limit, "signature": _sign_trustline_update_request(signing_key=key, trustline_id=line_id, limit=limit)})


@MODE_B
@pytest.mark.asyncio
async def test_patch_below_used_is_accepted_and_changes_trust_only(client, db_session) -> None:
    code, p, lines, factory = await _world(client, db_session)
    a, b = p["A"], p["B"]
    assert await _pay(factory, b, a, code, "80")
    assert await _debts(factory, code) == {(b["id"], a["id"]): Decimal("80")}

    r = await _patch_limit(client, a, lines["AB"], "0")
    require_target(r.status_code == 200, f"PATCH 100 -> 0 below used 80 was refused: {r.status_code} {r.text}")
    body = r.json()
    assert [Decimal(body[k]) for k in ("limit", "used", "available")] == [0, 80, -80], body
    assert body["available"].startswith("-"), "available must be a signed decimal string"
    # A trust change only: the debt is neither written off nor moved.
    assert await _debts(factory, code) == {(b["id"], a["id"]): Decimal("80")}

    one = await client.get(f"/api/v1/trustlines/{lines['AB']}", headers=a["headers"])
    listed = await client.get("/api/v1/trustlines", headers=a["headers"], params={"equivalent": code})
    admin = await client.get("/api/v1/admin/trustlines", headers={"X-Admin-Token": settings.ADMIN_TOKEN},
                             params={"equivalent": code})
    for resp in (one, listed, admin):
        assert resp.status_code == 200, resp.text
    items = [one.json()] + listed.json()["items"] + admin.json()["items"]
    views = [i for i in items if i["id"] == lines["AB"]]
    assert len(views) == 3 and all(Decimal(v["available"]) == -80 for v in views), views


@MODE_B
@pytest.mark.asyncio
async def test_after_lowering_the_debt_cannot_grow_but_is_reduced_by_payment_and_clearing(client, db_session) -> None:
    code, p, lines, factory = await _world(client, db_session)
    a, b, c = p["A"], p["B"], p["C"]
    assert await _pay(factory, b, a, code, "80")
    r = await _patch_limit(client, a, lines["AB"], "0")
    require_target(r.status_code == 200, f"PATCH 100 -> 0 below used 80 was refused: {r.status_code} {r.text}")
    ba = (b["id"], a["id"])

    # R-026-4, the snapshot: the excess is reported as allowed, not raised.
    async with factory() as s:
        eq_id = (await s.execute(select(Equivalent.id).where(Equivalent.code == code))).scalar_one()
        (entry,) = await InvariantChecker(s).check_trust_limits(equivalent_id=eq_id)
    assert [Decimal(entry[k]) for k in ("debt_amount", "trust_limit", "excess")] == [80, 0, 80]

    # Growth is refused: by a payment, and by a direct production book call.
    assert not await _pay(factory, b, a, code, "0.01"), "B borrowed from A past a limit of 0"
    async with factory() as s:
        with pytest.raises(IntegrityViolationException) as refused:
            async with writer_operation(s, kind="PAYMENT", equivalent_ids=[eq_id], initiator_id=b["id"]):
                await Book.current(s).apply(PaymentFlow(b["id"], a["id"], Decimal("0.01"), eq_id))
    assert refused.value.details["invariant"] == "TRUST_LIMIT_VIOLATION"
    assert await _debts(factory, code) == {ba: Decimal("80")}

    # Reduction by a payment the other way.
    assert await _pay(factory, a, b, code, "30"), "A's payment to B that only reduces B's debt was refused"
    assert await _debts(factory, code) == {ba: Decimal("50")}
    assert not await _pay(factory, b, a, code, "0.01")

    # Reduction by clearing: a cycle B->A 50, A->C 20, C->B 20 (each made by a real payment within its limit).
    assert await _pay(factory, a, c, code, "20")
    assert await _pay(factory, c, b, code, "20")
    assert await _debts(factory, code) == {ba: Decimal("50"), (a["id"], c["id"]): Decimal("20"),
                                          (c["id"], b["id"]): Decimal("20")}
    result = await run_clearing_pass(factory, code)
    assert len(result.committed) == 1 and result.status == "complete", result
    assert await _debts(factory, code) == {ba: Decimal("30")}

    assert not await _pay(factory, b, a, code, "0.01"), "B borrowed again after the partial repayment"
    assert await _debts(factory, code) == {ba: Decimal("30")}
    line = await client.get(f"/api/v1/trustlines/{lines['AB']}", headers=a["headers"])
    assert Decimal(line.json()["available"]) == -30
