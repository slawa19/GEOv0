"""030 S5 (F-030-13, `T3000` item 1): a signed UPDATE or CLOSE of a trust line binds the operation, the new fields,
the state the owner saw (`expected`) and a fresh `issued_at`.

BEFORE S5 the signed bytes were `{"id"}` plus the optional new fields: the signature of an empty UPDATE was the
signature of a CLOSE, and any old UPDATE signature applied again as long as the bearer held. The two id-only cells
below are those reproducers - RED on the pre-S5 code because the server ACCEPTS them. The others state the contract
of `T3000`: cross-use and every tamper are refused by the signature; a replay after the line moved is `409` with the
line's current state; a signature older than 300 s or more than 30 s ahead of the server clock is refused; a correct
UPDATE and CLOSE pass. Everything goes over HTTP (Verification plan §4: no unit-test proof of a signature).

ACCEPTED RESIDUAL (dated in the spec, `T3000` item 1): within the window A -> B -> A lets the first A -> B signature
apply again, and an empty UPDATE is not one-shot - `test_an_empty_update_is_not_one_shot` documents the second. Mode A
except the concurrent cell, which needs sessions of its own.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import func, insert, select

from app.core.auth.canonical import canonical_json
from app.core.trustlines.service import TrustLineService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.reconciliation_tables import debt_reconciliation_results
from app.schemas.trustline import TrustLineUpdateRequest
from app.utils.exceptions import ConflictException
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_scenarios import (
    _sign_trustline_create_request,
    expected_state_of,
    register_and_login,
    signed_trustline_close,
    signed_trustline_update,
    trustline_operation_payload,
    utc_now_rfc3339,
)

STATE_CHANGED = "TRUSTLINE_STATE_CHANGED"


def _key(user: dict) -> SigningKey:
    return SigningKey(base64.b64decode(user["priv"]))


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _error(response) -> dict:
    return response.json()["error"]


async def _world(client, db_session, *, active=True, held=False):
    """One equivalent (precision 2), creditor A and debtor B registered over HTTP, the line A -> B 10.00 by POST."""

    code = "S5" + uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=code, symbol="S", precision=2, metadata_={}, is_active=active)
    db_session.add(eq)
    await db_session.flush()
    if held:
        result_id, now = uuid.uuid4(), datetime.now(timezone.utc)
        await db_session.execute(insert(debt_reconciliation_results).values(
            id=result_id, equivalent_id=eq.id, status="FAILED", fingerprint=result_id.hex * 2, detail={},
            checked_at=now, last_checked_at=now, is_latest=True))
        eq.integrity_hold_result_id = result_id
    await db_session.commit()
    a, b = await register_and_login(client, f"S5A{code}"), await register_and_login(client, f"S5B{code}")
    created = await client.post("/api/v1/trustlines", headers=a["headers"], json={
        "to": b["pid"], "equivalent": code, "limit": "10.00",
        "signature": _sign_trustline_create_request(signing_key=_key(a), to_pid=b["pid"], equivalent=code,
                                                    limit="10.00")})
    return code, a, b, created


async def _patch(client, a, line_id, body):
    return await client.patch(f"/api/v1/trustlines/{line_id}", headers=a["headers"], json=body)


async def _delete(client, a, line_id, body):
    return await client.request("DELETE", f"/api/v1/trustlines/{line_id}", headers=a["headers"], json=body)


def _id_only_signature(a, payload: dict) -> str:
    """The pre-S5 form: the signature over `{"id"}` plus the optional new fields and nothing else."""

    return base64.b64encode(_key(a).sign(canonical_json(payload)).signature).decode("utf-8")


# ------------------------------------------------------------------ the reproducers: the old form is accepted today


@pytest.mark.asyncio
async def test_f030_13_an_empty_update_signature_presented_to_close_is_refused(client, db_session) -> None:
    _code, a, _b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    body = {"signature": _id_only_signature(a, {"id": line_id})}  # a valid pre-S5 EMPTY UPDATE and CLOSE alike
    response = await _delete(client, a, line_id, body)
    assert response.status_code != 200, (
        f"an id-only signature (the pre-S5 empty UPDATE) closed the line: {response.status_code} {response.text}")
    one = await client.get(f"/api/v1/trustlines/{line_id}", headers=a["headers"])
    assert (one.json()["status"], one.json()["close_requested_at"]) == ("active", None), one.text


@pytest.mark.asyncio
async def test_f030_13_an_old_update_signature_is_refused_after_the_line_moved(client, db_session) -> None:
    _code, a, _b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    old = {"limit": "20.00", "signature": _id_only_signature(a, {"id": line_id, "limit": "20.00"})}
    first = await _patch(client, a, line_id, old)
    moved = await _patch(client, a, line_id, signed_trustline_update(
        signing_key=_key(a), trustline_id=line_id, limit="30.00",
        expected=await expected_state_of(client, a["headers"], line_id)))
    replayed = await _patch(client, a, line_id, old)
    one = await client.get(f"/api/v1/trustlines/{line_id}", headers=a["headers"])
    assert replayed.status_code != 200, (
        f"the pre-S5 UPDATE signature applied again after the line moved on: first={first.status_code}, "
        f"moved={moved.status_code}, replayed={replayed.status_code} {replayed.text}, line={one.json()}")
    assert Decimal(one.json()["limit"]) != Decimal("20.00"), one.text


# ------------------------------------------------------------------ the contract of `T3000` item 1


@pytest.mark.asyncio
async def test_a_correctly_signed_update_and_close_pass(client, db_session) -> None:
    _code, a, _b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    policy = {"auto_clearing": False}
    updated = await _patch(client, a, line_id, signed_trustline_update(
        signing_key=_key(a), trustline_id=line_id, limit="25.00", policy=policy,
        expected=await expected_state_of(client, a["headers"], line_id)))
    assert updated.status_code == 200, updated.text
    assert (updated.json()["limit"], updated.json()["policy"]["auto_clearing"]) == ("25.00", False), updated.text

    closed = await _delete(client, a, line_id, signed_trustline_close(
        signing_key=_key(a), trustline_id=line_id, expected=await expected_state_of(client, a["headers"], line_id)))
    assert closed.status_code == 200, closed.text
    assert closed.json()["trustline"]["status"] == "closed", closed.text


@pytest.mark.parametrize("tamper", ["operation", "id", "limit", "expected", "operation_in_the_body"])
@pytest.mark.asyncio
async def test_a_signature_is_bound_to_the_operation_the_line_and_every_field(client, db_session, tamper) -> None:
    """Each cell signs ONE payload and sends ANOTHER; the signature must not carry over. `operation`: the UPDATE
    signature at the CLOSE route (the cross-use F-030-13 names). `id`: a signature over another line's id. `limit`
    and `expected`: the signed bytes name one value, the body another. `operation_in_the_body`: the body says CLOSE
    at the PATCH route, signed consistently - the route decides, not the body."""

    _code, a, b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    expected = await expected_state_of(client, a["headers"], line_id)
    issued_at = utc_now_rfc3339()
    signed = trustline_operation_payload(operation="TRUST_LINE_UPDATE", trustline_id=line_id, expected=expected,
                                         issued_at=issued_at, limit="20.00")
    if tamper == "id":
        signed["id"] = str(uuid.uuid4())
    body = {**{k: v for k, v in signed.items() if k != "id"},
            "signature": base64.b64encode(_key(a).sign(canonical_json(signed)).signature).decode("utf-8")}
    if tamper == "limit":
        body["limit"] = "90.00"
    if tamper == "expected":
        body["expected"] = {**expected, "limit": "0.00"}
    if tamper == "operation":
        body = {"operation": "TRUST_LINE_CLOSE", "expected": expected, "issued_at": issued_at,
                "signature": body["signature"]}
        response = await _delete(client, a, line_id, body)
    else:
        if tamper == "operation_in_the_body":
            signed["operation"] = "TRUST_LINE_CLOSE"
            body = {**{k: v for k, v in signed.items() if k != "id"},
                    "signature": base64.b64encode(_key(a).sign(canonical_json(signed)).signature).decode("utf-8")}
        response = await _patch(client, a, line_id, body)
    assert response.status_code == 400 and _error(response)["code"] == "E005", response.text
    one = (await client.get(f"/api/v1/trustlines/{line_id}", headers=a["headers"])).json()
    assert (one["limit"], one["status"], one["close_requested_at"]) == ("10.00", "active", None), one


@pytest.mark.asyncio
async def test_a_replayed_signature_after_the_line_moved_is_a_conflict_naming_the_current_state(client, db_session):
    _code, a, _b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    to_20 = signed_trustline_update(signing_key=_key(a), trustline_id=line_id, limit="20.00",
                                    expected=await expected_state_of(client, a["headers"], line_id))
    assert (await _patch(client, a, line_id, to_20)).status_code == 200
    assert (await _patch(client, a, line_id, signed_trustline_update(
        signing_key=_key(a), trustline_id=line_id, limit="30.00",
        expected=await expected_state_of(client, a["headers"], line_id)))).status_code == 200

    replayed = await _patch(client, a, line_id, to_20)
    assert replayed.status_code == 409, replayed.text
    details = _error(replayed)["details"]
    assert details["reason"] == STATE_CHANGED and details["trustline_id"] == line_id, details
    assert Decimal(details["current"]["limit"]) == Decimal("30.00"), details
    assert (details["current"]["status"], details["current"]["close_requested_at"]) == ("active", None), details
    assert Decimal((await client.get(f"/api/v1/trustlines/{line_id}", headers=a["headers"])).json()["limit"]) == 30

    # The same rule at CLOSE: an `expected` from before the move is refused, the line stays open.
    stale_close = await _delete(client, a, line_id, signed_trustline_close(
        signing_key=_key(a), trustline_id=line_id, expected={**details["current"], "limit": "20.00"}))
    assert stale_close.status_code == 409 and _error(stale_close)["details"]["reason"] == STATE_CHANGED, stale_close.text
    assert (await client.get(f"/api/v1/trustlines/{line_id}", headers=a["headers"])).json()["status"] == "active"


@pytest.mark.parametrize("offset_seconds,outcome", [
    (-301, "signature_expired"), (31, "signature_issued_in_the_future"), (-299, "ok"), (29, "ok")])
@pytest.mark.asyncio
async def test_the_signature_window_is_300_s_back_and_30_s_ahead(client, db_session, offset_seconds, outcome) -> None:
    """The clock is the server's UTC; the two inside cells are the anti-vacuum of the two refusals."""

    _code, a, _b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    response = await _patch(client, a, line_id, signed_trustline_update(
        signing_key=_key(a), trustline_id=line_id, limit="20.00",
        expected=await expected_state_of(client, a["headers"], line_id),
        issued_at=_rfc3339(datetime.now(timezone.utc) + timedelta(seconds=offset_seconds))))
    if outcome == "ok":
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 400, response.text
        assert (_error(response)["code"], _error(response)["details"]["reason"]) == ("E005", outcome), response.text
        assert (await client.get(f"/api/v1/trustlines/{line_id}", headers=a["headers"])).json()["limit"] == "10.00"


@pytest.mark.asyncio
async def test_a_retry_after_a_lost_response_gets_409_whose_current_state_is_the_intended_one(client, db_session):
    """The protocol's rule for a client: `409 TRUSTLINE_STATE_CHANGED` with `current` equal to what it intended means
    the intent is in force (its own request or an equal one applied). UPDATE and CLOSE alike."""

    _code, a, _b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    body = signed_trustline_update(signing_key=_key(a), trustline_id=line_id, limit="20.00",
                                   policy={"auto_clearing": False},
                                   expected=await expected_state_of(client, a["headers"], line_id))
    assert (await _patch(client, a, line_id, body)).status_code == 200
    retried = await _patch(client, a, line_id, body)
    assert retried.status_code == 409, retried.text
    current = _error(retried)["details"]["current"]
    assert Decimal(current["limit"]) == Decimal("20.00") and current["policy"]["auto_clearing"] is False, current
    assert (current["status"], current["close_requested_at"]) == ("active", None), current

    close = signed_trustline_close(signing_key=_key(a), trustline_id=line_id,
                                   expected=await expected_state_of(client, a["headers"], line_id))
    assert (await _delete(client, a, line_id, close)).json()["trustline"]["status"] == "closed"
    retried = await _delete(client, a, line_id, close)
    assert retried.status_code == 409, retried.text
    assert _error(retried)["details"]["current"]["status"] == "closed", retried.text


@pytest.mark.asyncio
async def test_an_empty_update_is_not_one_shot(client, db_session) -> None:
    """Documented residual (`T3000` item 1): an UPDATE that changes nothing leaves `expected` true, so its signature
    applies again within the window. Recorded, not fixed."""

    _code, a, _b, created = await _world(client, db_session)
    line_id = created.json()["id"]
    body = signed_trustline_update(signing_key=_key(a), trustline_id=line_id,
                                   expected=await expected_state_of(client, a["headers"], line_id))
    assert [(await _patch(client, a, line_id, body)).status_code for _ in range(2)] == [200, 200]


@MODE_B
@pytest.mark.asyncio
async def test_a_concurrent_double_submit_of_one_signed_update_applies_once(client, db_session) -> None:
    """Two sessions submit the same signed UPDATE 10 -> 20. The line is locked `FOR UPDATE` before the check, so the
    second reads the row the first committed (`populate_existing`) and sees `expected` false: one applies, one is the
    conflict, the limit is 20 once and the journal has one UPDATE row. The barrier holds the winner after its lock
    until the loser is about to lock, so the loser's `SELECT ... FOR UPDATE` waits on a live lock, not on history."""

    _code, a, _b, created = await _world(client, db_session)
    line_id = uuid.UUID(created.json()["id"])
    owner_id = (await db_session.execute(select(Participant.id).where(Participant.pid == a["pid"]))).scalar_one()
    body = signed_trustline_update(signing_key=_key(a), trustline_id=str(line_id), limit="20.00",
                                   expected=await expected_state_of(client, a["headers"], str(line_id)))
    await db_session.commit()
    factory = sessionmaker_of(db_session)
    audit_before = int(await db_session.scalar(select(func.count()).select_from(IntegrityAuditLog)))
    barrier = asyncio.Barrier(2)

    async def submit(hold_after_lock: bool):
        async with factory() as session:
            service = TrustLineService(session)
            if hold_after_lock:
                original = service.execute_update

                async def locked_then_wait(*args, **kwargs):
                    line = await original(*args, **kwargs)
                    await asyncio.wait_for(barrier.wait(), timeout=15)
                    return line

                service.execute_update = locked_then_wait  # type: ignore[method-assign]
            else:
                await asyncio.wait_for(barrier.wait(), timeout=15)
            try:
                await service.update(line_id, owner_id, TrustLineUpdateRequest(**body))
                return "applied"
            except ConflictException as exc:
                assert exc.details["reason"] == STATE_CHANGED and Decimal(exc.details["current"]["limit"]) == 20
                return "conflict"

    outcomes = await asyncio.gather(submit(True), submit(False))
    assert sorted(outcomes) == ["applied", "conflict"], outcomes
    async with factory() as s:
        assert Decimal(str((await s.execute(select(TrustLine.limit).where(TrustLine.id == line_id))).scalar_one())) == 20
        assert int(await s.scalar(select(func.count()).select_from(IntegrityAuditLog))) == audit_before + 1


# ------------------------------------------------------------------ `T3093` (S3, class 2): POST /trustlines 409 details


@pytest.mark.parametrize("world,reason", [({"active": False}, "equivalent_inactive"),
                                          ({"held": True}, "equivalent_integrity_hold")])
@pytest.mark.asyncio
async def test_post_trustlines_409_names_the_stopped_or_held_equivalent(client, db_session, world, reason) -> None:
    code, _a, _b, created = await _world(client, db_session, **world)
    assert created.status_code == 409, created.text
    assert _error(created)["details"] == {"reason": reason, "equivalents": [code]}, created.text
