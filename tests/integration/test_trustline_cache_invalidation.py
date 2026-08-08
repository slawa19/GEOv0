from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.core.trustlines.service as trustline_service_module
from app.core.payments.router import PaymentRouter
from app.core.trustlines.service import TrustLineService
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import (
    TrustLineCloseRequest,
    TrustLineCreateRequest,
    TrustLineUpdateRequest,
)


@pytest.fixture(autouse=True)
def _isolate_payment_router_caches():
    graph_cache = PaymentRouter._graph_cache.copy()
    topology_cache = PaymentRouter._topology_cache.copy()
    PaymentRouter._graph_cache.clear()
    PaymentRouter._topology_cache.clear()
    try:
        yield
    finally:
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache.update(graph_cache)
        PaymentRouter._topology_cache.clear()
        PaymentRouter._topology_cache.update(topology_cache)


async def _seed_trustline_subjects(db_session, *, with_trustline: bool):
    equivalent = Equivalent(code="USD", precision=2, is_active=True)
    sender = Participant(
        id=uuid.uuid4(),
        pid="cache-owner",
        display_name="Cache owner",
        public_key="test-public-key",
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid="cache-peer",
        display_name="Cache peer",
        public_key="test-peer-key",
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([equivalent, sender, receiver])
    await db_session.flush()

    trustline = None
    if with_trustline:
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
    return equivalent, sender, receiver, trustline


def _prime_both_caches(equivalent_code: str) -> tuple[object, object]:
    graph_entry = object()
    topology_entry = object()
    PaymentRouter._graph_cache[equivalent_code] = graph_entry  # type: ignore[assignment]
    PaymentRouter._topology_cache[equivalent_code] = topology_entry  # type: ignore[assignment]
    PaymentRouter._graph_cache["OTHER"] = object()  # type: ignore[assignment]
    PaymentRouter._topology_cache["OTHER"] = {}
    return graph_entry, topology_entry


async def _run_operation(
    db_session,
    operation: str,
    *,
    sender: Participant,
    receiver: Participant,
    trustline: TrustLine | None,
):
    service = TrustLineService(db_session)
    if operation == "create":
        return await service.create(
            sender.id,
            TrustLineCreateRequest(
                to=receiver.pid,
                equivalent="USD",
                limit=Decimal("10"),
                signature="test-signature",
            ),
        )
    if operation == "update":
        assert trustline is not None
        return await service.update(
            trustline.id,
            sender.id,
            TrustLineUpdateRequest(limit=Decimal("20"), signature="test-signature"),
        )
    if operation == "close":
        assert trustline is not None
        return await service.close(
            trustline.id,
            sender.id,
            TrustLineCloseRequest(signature="test-signature"),
        )
    raise AssertionError(f"Unsupported operation: {operation}")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "close"])
async def test_public_trustline_success_evicts_only_equivalent_caches_after_commit(
    db_session,
    monkeypatch,
    operation,
):
    equivalent, sender, receiver, trustline = await _seed_trustline_subjects(
        db_session,
        with_trustline=operation != "create",
    )
    monkeypatch.setattr(trustline_service_module, "verify_signature", lambda *_args: None)
    _prime_both_caches(equivalent.code)
    original_commit = db_session.commit

    async def _commit_while_caches_are_still_present():
        assert equivalent.code in PaymentRouter._graph_cache
        assert equivalent.code in PaymentRouter._topology_cache
        await original_commit()

    monkeypatch.setattr(db_session, "commit", _commit_while_caches_are_still_present)

    await _run_operation(
        db_session,
        operation,
        sender=sender,
        receiver=receiver,
        trustline=trustline,
    )

    assert equivalent.code not in PaymentRouter._graph_cache
    assert equivalent.code not in PaymentRouter._topology_cache
    assert "OTHER" in PaymentRouter._graph_cache
    assert "OTHER" in PaymentRouter._topology_cache


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "close"])
async def test_public_trustline_commit_failure_retains_caches_after_rollback(
    db_session,
    monkeypatch,
    operation,
):
    equivalent, sender, receiver, trustline = await _seed_trustline_subjects(
        db_session,
        with_trustline=operation != "create",
    )
    equivalent_code = equivalent.code
    equivalent_id = equivalent.id
    monkeypatch.setattr(trustline_service_module, "verify_signature", lambda *_args: None)
    graph_entry, topology_entry = _prime_both_caches(equivalent_code)

    async def _fail_commit():
        raise RuntimeError("forced commit failure")

    monkeypatch.setattr(db_session, "commit", _fail_commit)
    with pytest.raises(RuntimeError, match="forced commit failure"):
        await _run_operation(
            db_session,
            operation,
            sender=sender,
            receiver=receiver,
            trustline=trustline,
        )
    await db_session.rollback()

    assert PaymentRouter._graph_cache[equivalent_code] is graph_entry
    assert PaymentRouter._topology_cache[equivalent_code] is topology_entry

    persisted = (
        await db_session.execute(
            select(TrustLine).where(TrustLine.equivalent_id == equivalent_id)
        )
    ).scalar_one_or_none()
    if operation == "create":
        assert persisted is None
    else:
        assert persisted is not None
        assert persisted.limit == Decimal("10")
        assert persisted.status == "active"
