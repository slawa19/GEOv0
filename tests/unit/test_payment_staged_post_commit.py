import asyncio
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.event_bus import event_bus


class _MetricRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, str]] = []

    def labels(self, **labels):
        recorder = self

        class _BoundMetric:
            def inc(self) -> None:
                recorder.records.append(dict(labels))

        return _BoundMetric()


async def _seed_direct_route(db_session, suffix: str):
    sender = Participant(
        id=uuid.uuid4(),
        pid=f"STAGED_SENDER_{suffix}",
        display_name="Staged sender",
        public_key=("a" * 56) + suffix[:8],
    )
    receiver = Participant(
        id=uuid.uuid4(),
        pid=f"STAGED_RECEIVER_{suffix}",
        display_name="Staged receiver",
        public_key=("b" * 56) + suffix[:8],
    )
    equivalent = Equivalent(
        id=uuid.uuid4(),
        code=f"S{suffix[:5].upper()}",
        description="Staged test equivalent",
        precision=2,
    )
    db_session.add_all([sender, receiver, equivalent])
    db_session.add(
        TrustLine(
            id=uuid.uuid4(),
            from_participant_id=receiver.id,
            to_participant_id=sender.id,
            equivalent_id=equivalent.id,
            limit=Decimal("100.00"),
            status="active",
        )
    )
    await db_session.commit()
    return sender, receiver, equivalent


@pytest.mark.asyncio
async def test_staged_payment_rollback_has_no_rows_or_effects(db_session):
    suffix = uuid.uuid4().hex[:8]
    sender, receiver, equivalent = await _seed_direct_route(db_session, suffix)
    tx_id = f"staged-rollback-{suffix}"
    subscription = await event_bus.subscribe(
        pid=receiver.pid,
        events=["payment.received"],
    )
    PaymentRouter._graph_cache[equivalent.code] = object()  # type: ignore[assignment]

    try:
        savepoint = await db_session.begin_nested()
        staged = await PaymentService(db_session).create_payment_internal_staged(
            sender.id,
            to_pid=receiver.pid,
            equivalent=equivalent.code,
            amount="5.00",
            idempotency_key=tx_id,
        )
        assert staged.post_commit_effects is not None
        assert equivalent.code in PaymentRouter._graph_cache

        await savepoint.rollback()
        await asyncio.sleep(0)

        tx_count = await db_session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.tx_id == tx_id)
        )
        debt_count = await db_session.scalar(select(func.count()).select_from(Debt))
        assert tx_count == 0
        assert debt_count == 0
        assert subscription.queue.empty()
        assert equivalent.code in PaymentRouter._graph_cache
    finally:
        PaymentRouter.invalidate_cache(equivalent.code)
        await event_bus.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_staged_payment_effects_apply_once_after_outer_commit(
    db_session,
    monkeypatch,
):
    suffix = uuid.uuid4().hex[:8]
    sender, receiver, equivalent = await _seed_direct_route(db_session, suffix)
    tx_id = f"staged-commit-{suffix}"
    subscription = await event_bus.subscribe(
        pid=receiver.pid,
        events=["payment.received"],
    )
    monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 60)
    metric_recorder = _MetricRecorder()
    monkeypatch.setattr(
        "app.core.payments.engine.PAYMENT_EVENTS_TOTAL",
        metric_recorder,
    )
    monkeypatch.setattr(
        "app.utils.metrics.PAYMENT_EVENTS_TOTAL",
        metric_recorder,
    )
    stale_cache_entry = (time.time(), {}, {}, {}, {}, {})
    PaymentRouter._graph_cache[equivalent.code] = stale_cache_entry
    invalidations: list[str | None] = []
    original_invalidate = PaymentRouter.invalidate_cache.__func__

    def _track_invalidation(cls, equivalent_code=None):
        invalidations.append(equivalent_code)
        original_invalidate(cls, equivalent_code)

    monkeypatch.setattr(
        PaymentRouter,
        "invalidate_cache",
        classmethod(_track_invalidation),
    )

    try:
        async with db_session.begin_nested():
            staged = await PaymentService(db_session).create_payment_internal_staged(
                sender.id,
                to_pid=receiver.pid,
                equivalent=equivalent.code,
                amount="5.00",
                idempotency_key=tx_id,
            )

        assert staged.post_commit_effects is not None
        assert subscription.queue.empty()
        assert PaymentRouter._graph_cache[equivalent.code] is stale_cache_entry
        assert invalidations == []
        assert [
            record for record in metric_recorder.records if record.get("result") == "success"
        ] == []

        await db_session.commit()
        assert staged.post_commit_effects.apply_once() is True
        assert staged.post_commit_effects.apply_once() is False
        await asyncio.sleep(0)

        assert subscription.queue.qsize() == 1
        assert equivalent.code not in PaymentRouter._graph_cache
        assert invalidations == [equivalent.code]
        assert [
            record for record in metric_recorder.records if record.get("result") == "success"
        ] == [
            {"event": "create", "result": "success"},
            {"event": "prepare", "result": "success"},
            {"event": "commit", "result": "success"},
        ]
        tx_count = await db_session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.tx_id == tx_id)
        )
        debt_count = await db_session.scalar(select(func.count()).select_from(Debt))
        assert tx_count == 1
        assert debt_count == 1

        async with db_session.begin_nested():
            retry = await PaymentService(db_session).create_payment_internal_staged(
                sender.id,
                to_pid=receiver.pid,
                equivalent=equivalent.code,
                amount="5.00",
                idempotency_key=tx_id,
            )
        assert retry.post_commit_effects is None
        await asyncio.sleep(0)
        assert subscription.queue.qsize() == 1
        assert invalidations == [equivalent.code]
    finally:
        PaymentRouter.invalidate_cache(equivalent.code)
        await event_bus.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_staged_payment_cancellation_rolls_back_without_effects(db_session):
    suffix = uuid.uuid4().hex[:8]
    sender, receiver, equivalent = await _seed_direct_route(db_session, suffix)
    tx_id = f"staged-cancel-{suffix}"
    subscription = await event_bus.subscribe(
        pid=receiver.pid,
        events=["payment.received"],
    )

    try:
        with pytest.raises(asyncio.CancelledError):
            async with db_session.begin_nested():
                staged = await PaymentService(
                    db_session
                ).create_payment_internal_staged(
                    sender.id,
                    to_pid=receiver.pid,
                    equivalent=equivalent.code,
                    amount="5.00",
                    idempotency_key=tx_id,
                )
                assert staged.post_commit_effects is not None
                raise asyncio.CancelledError

        await asyncio.sleep(0)
        tx_count = await db_session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.tx_id == tx_id)
        )
        debt_count = await db_session.scalar(select(func.count()).select_from(Debt))
        assert tx_count == 0
        assert debt_count == 0
        assert subscription.queue.empty()
    finally:
        PaymentRouter.invalidate_cache(equivalent.code)
        await event_bus.unsubscribe(subscription)
