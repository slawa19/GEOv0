"""026 S3 (`T2603.1`): closing a line with debt is a REQUEST; the book completes it at zero (R-026-2, R-026-3).

THE RULE (owner В1/В2, 2026-09-29; spec 026, forks 2 and 6): a close sets the limit to 0 and `close_requested_at`.
It checks only the debt the line SUPPORTS (debtor = `to`, creditor = `from`); a debt the other way belongs to the
other line and does not hold the close (protocol §5.3). Zero supported debt - closed at once. Otherwise the line
stays `active`, and `Book.operation` closes it in the transaction that brings that debt to exactly 0 - a payment
or a clearing alike. Repeating the request changes nothing; a positive PATCH is a conflict; reopening after the
close is a new line.

HOW THE STATE IS REACHED. Debts only by real payments and the production clearing pass; the close only by the
signed `DELETE /trustlines/{id}`. No row of `debts` or `trust_lines` is written by hand (Verification plan §4).
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from app.core.clearing.runner import run_clearing_pass
from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.trustline import TrustLine
from tests.conftest import MODE_B
from tests.integration.test_p026_s2_limit_below_used_postgres import _debts, _pay, _patch_limit, _world
from tests.integration.test_scenarios import (
    _sign_trustline_close_request,
    _sign_trustline_create_request,
    _sign_trustline_update_request,
)
from tests.p019_support import TargetMismatch, require_target


def target_xfail_026(what: str):
    return pytest.mark.xfail(raises=TargetMismatch, strict=True, reason=f"026 target, delivered by T2603.1: {what}")


async def _close(client, creditor, line_id: str):
    key = SigningKey(base64.b64decode(creditor["priv"]))
    return await client.request("DELETE", f"/api/v1/trustlines/{line_id}", headers=creditor["headers"], json={
        "signature": _sign_trustline_close_request(signing_key=key, trustline_id=line_id)})


async def _line(client, creditor, line_id: str) -> dict:
    r = await client.get(f"/api/v1/trustlines/{line_id}", headers=creditor["headers"])
    assert r.status_code == 200, r.text
    return r.json()


async def _audit(factory, line_id: str) -> list[tuple[str, dict]]:
    async with factory() as s:
        rows = (await s.execute(select(IntegrityAuditLog.operation_type, IntegrityAuditLog.tx_id,
                                       IntegrityAuditLog.affected_participants))).all()
    return sorted((op, {**(a or {}), "tx_id": tx}) for op, tx, a in rows
                  if isinstance(a, dict) and a.get("trustline_id") == line_id and "CLOSE" in op)


async def _create(client, creditor, debtor, code: str, limit: str = "100") -> str:
    key = SigningKey(base64.b64decode(creditor["priv"]))
    r = await client.post("/api/v1/trustlines", headers=creditor["headers"], json={
        "to": debtor["pid"], "equivalent": code, "limit": limit, "signature": _sign_trustline_create_request(
            signing_key=key, to_pid=debtor["pid"], equivalent=code, limit=limit)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _request_close_with_debt(client, factory, p, lines, code: str) -> dict:
    """B owes A 50 (a real payment); A asks to close A -> B. The target: accepted, still active, limit 0."""

    a, b = p["A"], p["B"]
    assert await _pay(factory, b, a, code, "50")
    r = await _close(client, a, lines["AB"])
    require_target(r.status_code == 200, f"close of A -> B with B's debt 50 was refused: {r.status_code} {r.text}")
    return r.json()


@target_xfail_026("a close with debt is a request; the payment that brings the supported debt to 0 completes it")
@MODE_B
@pytest.mark.asyncio
async def test_a_close_with_debt_waits_for_zero_and_a_payment_completes_it(client, db_session) -> None:
    code, p, lines, factory = await _world(client, db_session)
    a, b = p["A"], p["B"]
    body = await _request_close_with_debt(client, factory, p, lines, code)
    view = body["trustline"]
    assert (view["status"], Decimal(view["limit"]), Decimal(view["available"])) == ("active", 0, -50), body
    asked = view["close_requested_at"]
    assert asked and body["message"] == "Trustline close requested", body
    assert [op for op, _ in await _audit(factory, lines["AB"])] == ["TRUST_LINE_CLOSE_REQUEST"]

    # Repeating the request: the same state, the original timestamp, no second request row.
    again = await _close(client, a, lines["AB"])
    assert again.status_code == 200 and again.json()["trustline"]["close_requested_at"] == asked, again.text
    assert [op for op, _ in await _audit(factory, lines["AB"])] == ["TRUST_LINE_CLOSE_REQUEST"]

    # Trust is 0: no borrowing; a positive limit is a conflict and keeps the request; policy alone is allowed.
    assert not await _pay(factory, b, a, code, "0.01")
    r = await _patch_limit(client, a, lines["AB"], "10")
    assert r.status_code == 409 and r.json()["details"]["reason"] == "TRUSTLINE_CLOSE_REQUESTED", r.text
    key = SigningKey(base64.b64decode(a["priv"]))
    policy = {"auto_clearing": False}
    r = await client.patch(f"/api/v1/trustlines/{lines['AB']}", headers=a["headers"], json={
        "policy": policy, "signature": _sign_trustline_update_request(
            signing_key=key, trustline_id=lines["AB"], policy=policy)})
    assert r.status_code == 200 and r.json()["close_requested_at"] == asked, r.text

    # Partial repayment keeps the request pending; the exact zero closes in the same transaction.
    assert await _pay(factory, a, b, code, "20")
    assert (await _line(client, a, lines["AB"]))["status"] == "active"
    assert await _pay(factory, a, b, code, "30")
    assert await _debts(factory, code) == {}
    closed = await _line(client, a, lines["AB"])
    assert (closed["status"], closed["close_requested_at"]) == ("closed", asked), closed
    audit = await _audit(factory, lines["AB"])
    assert [op for op, _ in audit] == ["TRUST_LINE_CLOSE", "TRUST_LINE_CLOSE_REQUEST"], audit
    assert audit[0][1]["completed_by"] == "PAYMENT" and audit[0][1]["tx_id"], audit

    # Reopening is a new incarnation without a request.
    new_id = await _create(client, a, b, code, "10")
    fresh = await _line(client, a, new_id)
    assert new_id != lines["AB"] and fresh["close_requested_at"] is None and fresh["status"] == "active"


@target_xfail_026("the clearing that brings the supported debt to 0 completes the requested close")
@MODE_B
@pytest.mark.asyncio
async def test_a_clearing_completes_a_requested_close(client, db_session) -> None:
    code, p, lines, factory = await _world(client, db_session)
    a, b, c = p["A"], p["B"], p["C"]
    await _request_close_with_debt(client, factory, p, lines, code)
    assert await _pay(factory, a, c, code, "50") and await _pay(factory, c, b, code, "50")
    result = await run_clearing_pass(factory, code)
    assert len(result.committed) == 1 and result.status == "complete", result
    assert await _debts(factory, code) == {}
    line = await _line(client, a, lines["AB"])
    assert line["status"] == "closed" and line["policy"].get("auto_clearing", True) is not False, line
    audit = await _audit(factory, lines["AB"])
    assert [(op, x.get("completed_by")) for op, x in audit] == [
        ("TRUST_LINE_CLOSE", "CLEARING"), ("TRUST_LINE_CLOSE_REQUEST", None)], audit


@target_xfail_026("a debt the other way is the other line's: it does not hold the close (protocol §5.3)")
@MODE_B
@pytest.mark.asyncio
async def test_a_reverse_debt_does_not_hold_the_close(client, db_session) -> None:
    code, p, lines, factory = await _world(client, db_session)
    a, b = p["A"], p["B"]
    await _create(client, b, a, code)  # B trusts A: A's debt to B lives on THIS line
    assert await _pay(factory, a, b, code, "40")
    ab = (a["id"], b["id"])
    r = await _close(client, a, lines["AB"])
    require_target(r.status_code == 200, f"close of A -> B with only A's own debt 40 to B: {r.status_code} {r.text}")
    view = r.json()["trustline"]
    assert (view["status"], r.json()["message"]) == ("closed", "Trustline closed"), r.text
    assert await _debts(factory, code) == {ab: Decimal("40")}
    audit = await _audit(factory, lines["AB"])
    assert [(op, x.get("completed_by")) for op, x in audit] == [("TRUST_LINE_CLOSE", "request")], audit


@target_xfail_026("a payment that fails after zeroing the debt leaves the request pending and no completion row")
@MODE_B
@pytest.mark.asyncio
async def test_a_rolled_back_repayment_leaves_no_completion(client, db_session, monkeypatch) -> None:
    code, p, lines, factory = await _world(client, db_session)
    a, b = p["A"], p["B"]
    await _request_close_with_debt(client, factory, p, lines, code)

    async def fail(*_a, **_k):
        raise RuntimeError("injected failure after the book completed")

    monkeypatch.setattr(PaymentService, "_write_integrity_audit", fail)
    try:
        repaid = await _pay(factory, a, b, code, "50")
    except Exception:  # noqa: BLE001 - either shape of the failure is the same outcome here
        repaid = False
    assert not repaid
    monkeypatch.undo()
    assert await _debts(factory, code) == {(b["id"], a["id"]): Decimal("50")}
    assert (await _line(client, a, lines["AB"]))["status"] == "active"
    assert [op for op, _ in await _audit(factory, lines["AB"])] == ["TRUST_LINE_CLOSE_REQUEST"]


@MODE_B
@pytest.mark.asyncio
async def test_a_zero_limit_without_a_request_never_closes(client, db_session) -> None:
    # Counter-check (green before and after): a plain active line lowered to 0 is not a close request.
    code, p, lines, factory = await _world(client, db_session)
    a, b = p["A"], p["B"]
    assert await _pay(factory, b, a, code, "50")
    assert (await _patch_limit(client, a, lines["AB"], "0")).status_code == 200
    assert await _pay(factory, a, b, code, "50")
    assert await _debts(factory, code) == {}
    async with factory() as s:
        status = (await s.execute(select(TrustLine.status).where(TrustLine.id == uuid.UUID(lines["AB"])))).scalar_one()
    assert status == "active"
