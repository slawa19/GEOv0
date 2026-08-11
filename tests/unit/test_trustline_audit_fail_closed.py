from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

import app.core.trustlines.service as trustline_service_module
from app.core.trustlines.service import TrustLineService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCreateRequest, TrustLineUpdateRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on_call", [1, 2])
async def test_create_checkpoint_failure_is_not_swallowed_or_committed(
    db_session,
    monkeypatch,
    fail_on_call: int,
) -> None:
    equivalent = Equivalent(code="USD", precision=2, is_active=True)
    sender = Participant(
        id=uuid.uuid4(),
        pid="audit-owner",
        display_name="Audit owner",
        public_key="test-public-key",
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid="audit-peer",
        display_name="Audit peer",
        public_key="test-peer-key",
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([equivalent, sender, receiver])
    await db_session.commit()

    monkeypatch.setattr(trustline_service_module, "verify_signature", lambda *_args: None)

    original_checkpoint = (
        trustline_service_module.compute_integrity_checkpoint_for_equivalent
    )
    checkpoint_calls = 0

    async def _fail_selected_checkpoint(*args, **kwargs):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == fail_on_call:
            raise RuntimeError(f"forced checkpoint failure {fail_on_call}")
        return await original_checkpoint(*args, **kwargs)

    monkeypatch.setattr(
        trustline_service_module,
        "compute_integrity_checkpoint_for_equivalent",
        _fail_selected_checkpoint,
    )
    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)

    with pytest.raises(RuntimeError, match=f"forced checkpoint failure {fail_on_call}"):
        await TrustLineService(db_session).create(
            sender.id,
            TrustLineCreateRequest(
                to=receiver.pid,
                equivalent=equivalent.code,
                limit=Decimal("10"),
                signature="test-signature",
            ),
        )

    commit.assert_not_awaited()
    await db_session.rollback()
    assert await db_session.scalar(select(func.count()).select_from(TrustLine)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on_call", [1, 2])
async def test_update_checkpoint_failure_is_not_swallowed_or_committed(
    db_session,
    monkeypatch,
    fail_on_call: int,
) -> None:
    equivalent = Equivalent(code="USD", precision=2, is_active=True)
    sender = Participant(
        id=uuid.uuid4(),
        pid="update-audit-owner",
        display_name="Update audit owner",
        public_key="test-public-key",
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid="update-audit-peer",
        display_name="Update audit peer",
        public_key="test-peer-key",
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([equivalent, sender, receiver])
    await db_session.flush()
    trustline = TrustLine(
        from_participant_id=sender.id,
        to_participant_id=receiver.id,
        equivalent_id=equivalent.id,
        limit=Decimal("10"),
        policy={},
        status="active",
    )
    db_session.add(trustline)
    await db_session.commit()
    trustline_id = trustline.id

    monkeypatch.setattr(trustline_service_module, "verify_signature", lambda *_args: None)
    original_checkpoint = (
        trustline_service_module.compute_integrity_checkpoint_for_equivalent
    )
    checkpoint_calls = 0

    async def _fail_selected_checkpoint(*args, **kwargs):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == fail_on_call:
            raise RuntimeError(f"forced update checkpoint failure {fail_on_call}")
        return await original_checkpoint(*args, **kwargs)

    monkeypatch.setattr(
        trustline_service_module,
        "compute_integrity_checkpoint_for_equivalent",
        _fail_selected_checkpoint,
    )
    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)

    with pytest.raises(
        RuntimeError,
        match=f"forced update checkpoint failure {fail_on_call}",
    ):
        await TrustLineService(db_session).update(
            trustline_id,
            sender.id,
            TrustLineUpdateRequest(limit=Decimal("20"), signature="test-signature"),
        )

    commit.assert_not_awaited()
    await db_session.rollback()
    persisted = await db_session.get(TrustLine, trustline_id)
    assert persisted is not None
    assert persisted.limit == Decimal("10")
