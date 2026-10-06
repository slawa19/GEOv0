"""Programme 021, stage 1 (`T2102`): the PUBLIC trust-line operations still require a valid signature.

Stage 1 turned `TrustLineService.create/update/close` into wrappers over `execute_*`, which also serve the
simulator's unsigned internal path (`require_signature=False`). This module holds the public side: for each of
the three operations a missing signature and a wrong one are refused with `InvalidSignatureException`, and
nothing is written - no row changed, no audit row. The positive control signs correctly with the participant's
real Ed25519 key and succeeds, so the refusals are about the signature and not about a broken stand.

Before stage 1 only `create` with a bad signature had a test (`tests/unit/test_trustline_signatures.py`); a
wrapper that passed `require_signature=False` for `update` or `close` would have stayed green. Mode A.
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import pytest
from nacl.signing import SigningKey
from sqlalchemy import func, select

from app.core.auth.canonical import canonical_json
from app.core.trustlines.service import TrustLineService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest, TrustLineCreateRequest, TrustLineUpdateRequest
from app.utils.exceptions import InvalidSignatureException
from tests.integration.test_scenarios import trustline_operation_payload, utc_now_rfc3339


def _sign(key: SigningKey, payload: dict) -> str:
    return base64.b64encode(key.sign(canonical_json(payload)).signature).decode("utf-8")


async def _world(session):
    n = uuid.uuid4().hex[:6].upper()
    owner_key = SigningKey.generate()
    other_key = SigningKey.generate()
    eq = Equivalent(code=f"P21P{n}", precision=2, is_active=True, metadata_={})
    owner = Participant(
        pid=f"P21P_OWNER_{n}", display_name="owner", type="person", status="active", profile={},
        public_key=base64.b64encode(bytes(owner_key.verify_key)).decode("utf-8"),
    )
    peer = Participant(
        pid=f"P21P_PEER_{n}", display_name="peer", type="person", status="active", profile={},
        public_key=base64.b64encode(bytes(SigningKey.generate().verify_key)).decode("utf-8"),
    )
    session.add_all([eq, owner, peer])
    await session.flush()
    line = TrustLine(from_participant_id=owner.id, to_participant_id=peer.id, equivalent_id=eq.id,
                     limit=Decimal("10"), status="active", policy={})
    session.add(line)
    await session.commit()
    # Plain values: the refusal tests roll the session back, which expires every loaded row.
    return eq, owner, peer, line.id, owner_key, other_key


async def _audit_rows(session) -> int:
    return int(await session.scalar(select(func.count()).select_from(IntegrityAuditLog)))


async def _state(session, line_id):
    row = (await session.execute(select(TrustLine.limit, TrustLine.status).where(TrustLine.id == line_id))).one()
    return Decimal(str(row[0])), str(row[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["missing", "wrong_key"])
async def test_public_create_refuses_without_a_valid_signature(db_session, which) -> None:
    eq, owner, peer, _line_id, _owner_key, other_key = await _world(db_session)
    # The peer extends a new line to the owner; the peer's key has no private half here, so no request of this
    # test can carry a valid signature for it.
    payload = {"to": owner.pid, "equivalent": eq.code, "limit": "5"}
    signature = "" if which == "missing" else _sign(other_key, payload)
    audit_before = await _audit_rows(db_session)
    lines_before = int(await db_session.scalar(select(func.count()).select_from(TrustLine)))

    with pytest.raises(InvalidSignatureException):
        await TrustLineService(db_session).create(
            peer.id, TrustLineCreateRequest(to=owner.pid, equivalent=eq.code, limit="5", signature=signature)
        )
    await db_session.rollback()

    assert int(await db_session.scalar(select(func.count()).select_from(TrustLine))) == lines_before
    assert await _audit_rows(db_session) == audit_before


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["missing", "wrong_key"])
async def test_public_update_refuses_without_a_valid_signature(db_session, which) -> None:
    _eq, owner, _peer, line_id, _owner_key, other_key = await _world(db_session)
    owner_id = owner.id
    payload = {"id": str(line_id), "limit": "20"}
    signature = "" if which == "missing" else _sign(other_key, payload)
    audit_before = await _audit_rows(db_session)

    with pytest.raises(InvalidSignatureException):
        await TrustLineService(db_session).update(
            line_id, owner_id, TrustLineUpdateRequest(limit="20", signature=signature)
        )
    await db_session.rollback()

    assert await _state(db_session, line_id) == (Decimal("10"), "active")
    assert await _audit_rows(db_session) == audit_before


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["missing", "wrong_key"])
async def test_public_close_refuses_without_a_valid_signature(db_session, which) -> None:
    _eq, owner, _peer, line_id, _owner_key, other_key = await _world(db_session)
    owner_id = owner.id
    signature = "" if which == "missing" else _sign(other_key, {"id": str(line_id)})
    audit_before = await _audit_rows(db_session)

    with pytest.raises(InvalidSignatureException):
        await TrustLineService(db_session).close(line_id, owner_id, TrustLineCloseRequest(signature=signature))
    await db_session.rollback()

    assert await _state(db_session, line_id) == (Decimal("10"), "active")
    assert await _audit_rows(db_session) == audit_before


@pytest.mark.asyncio
async def test_control_a_correct_signature_is_accepted_and_audited_per_operation(db_session) -> None:
    """The positive control, and the public rows' shape: a batch of one keeps its historical keys."""

    _eq, owner, _peer, line_id, owner_key, _other = await _world(db_session)
    owner_id = owner.id
    audit_before = await _audit_rows(db_session)

    # 030 S5: the signed bytes carry the operation, the new limit, the state the owner saw and `issued_at`.
    payload = trustline_operation_payload(
        operation="TRUST_LINE_UPDATE", trustline_id=str(line_id), limit="20", issued_at=utc_now_rfc3339(),
        expected={"limit": "10", "policy": {}, "status": "active", "close_requested_at": None})
    await TrustLineService(db_session).update(
        line_id, owner_id, TrustLineUpdateRequest(**{k: v for k, v in payload.items() if k != "id"},
                                                  signature=_sign(owner_key, payload)),
    )
    assert await _state(db_session, line_id) == (Decimal("20"), "active")
    rows = (
        await db_session.execute(select(IntegrityAuditLog).order_by(IntegrityAuditLog.created_at))
    ).scalars().all()
    assert len(rows) == audit_before + 1
    last = rows[-1]
    assert last.operation_type == "TRUST_LINE_UPDATE"
    assert set(last.affected_participants) == {"from", "to", "trustline_id"}, last.affected_participants
    assert (last.state_checksum_before, last.state_checksum_after, last.verification_passed) == ("", "", None)  # 024 `T2413.2`: None = the row records the operation, no check ran
