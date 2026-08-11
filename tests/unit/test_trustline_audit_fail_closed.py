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
from app.schemas.trustline import TrustLineCreateRequest


@pytest.mark.asyncio
async def test_create_checkpoint_failure_is_not_swallowed_or_committed(
    db_session,
    monkeypatch,
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

    async def _fail_checkpoint(*_args, **_kwargs):
        raise RuntimeError("forced checkpoint read failure")

    monkeypatch.setattr(
        trustline_service_module,
        "compute_integrity_checkpoint_for_equivalent",
        _fail_checkpoint,
    )
    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)

    with pytest.raises(RuntimeError, match="forced checkpoint read failure"):
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
