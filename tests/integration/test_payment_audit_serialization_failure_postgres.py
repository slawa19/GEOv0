"""A REAL `40001` inside the payment's audit write reaches the owner of the retries as `40001`.

T401 (programme 004; AGENTS §9 "проглоченный 40001 отравляет транзакцию"): a serialization failure raised
while the payment writes its integrity audit must not be swallowed - PostgreSQL has aborted the
transaction, and a swallowed `40001` shows up only later as a misleading `25P02` that no retry predicate
accepts. Until programme 019 stage 4 this was proved on `PaymentEngine.commit` of a seeded `PREPARED`
row (the engine retried its unit of work). Since stage 4 the audit row is written inside the payment
operation of `PaymentService` (`_write_integrity_audit`: a database error propagates, any other failure
is best-effort), and `pay()` owns the retries: the whole attempt re-runs on a fresh session. This module
holds the same property on that path (manifest `t1901`, 5.1, rows of the dropped engine module).

THE CONFLICT IS REAL. On the second checkpoint call (the "after" checkpoint of the first attempt) the
payment reads the sender's participant row, a competitor commits an update of that row, and the payment
updates it too: PostgreSQL raises `40001`; no DBAPI error is fabricated. The competitor's wait is
bounded: if a future change row-locks that participant, the test goes red on `competitor_timed_out`
instead of hanging. The countercheck: the first checkpoint of the RE-RUN raises a non-database error,
which stays best-effort - the payment still commits.

MUTATION that must redden this: in `_write_integrity_audit`, swallow `DBAPIError` like any other failure
(the transaction is then poisoned and the payment fails with 25P02 / a safe 500 instead of retrying).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.payments.service as service_module
from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.schemas.payment import PaymentCreateRequest
from tests.integration.test_p015_p1_money_replay_postgres import (
    _OPENING,
    _debts,
    _forget_the_route_cache,
    _seed,
)

#: How long the competitor may wait for its row. A healthy run commits it in milliseconds; a wait
#: this long means it is queued behind a lock the payment itself holds (T1544 retarget).
_COMPETITOR_TIMEOUT_S = 10.0


@pytest_asyncio.fixture
async def factory(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=5, max_overflow=0, isolation_level="SERIALIZABLE"
    )
    try:
        yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_serialization_failure_is_retried_by_pay_before_the_transaction_is_poisoned(
    factory, monkeypatch, caplog
) -> None:
    world = await _seed(factory)
    sender_id = world.sender.id
    checkpoint_calls = 0
    competitor_timed_out = False

    async def _competitor_updates_the_contended_row() -> None:
        async with factory() as competitor:
            await competitor.execute(
                update(Participant).where(Participant.id == sender_id).values(display_name="competitor")
            )
            await competitor.commit()

    async def conflicting_checkpoint(session, *, equivalent_id):
        nonlocal checkpoint_calls, competitor_timed_out
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            await session.execute(select(Participant.display_name).where(Participant.id == sender_id))
            competitor_task = asyncio.create_task(_competitor_updates_the_contended_row())
            done, _pending = await asyncio.wait({competitor_task}, timeout=_COMPETITOR_TIMEOUT_S)
            if not done:
                competitor_timed_out = True
                competitor_task.cancel()
                await asyncio.wait({competitor_task}, timeout=5.0)
                raise AssertionError("the competitor waited on a lock the payment holds")
            competitor_task.result()
            await session.execute(
                update(Participant).where(Participant.id == sender_id).values(display_name="payment")
            )
        if checkpoint_calls == 3:
            # Countercheck: on the re-run, a non-database diagnostics failure stays best-effort.
            raise ValueError("non-database audit diagnostics failure")
        return SimpleNamespace(checksum=f"c{checkpoint_calls}", invariants_status={"passed": True, "checks": []})

    monkeypatch.setattr(service_module, "compute_integrity_checkpoint_for_equivalent", conflicting_checkpoint)

    request = PaymentCreateRequest(
        tx_id=str(uuid.uuid4()),
        to=world.receiver.pid,
        equivalent=world.equivalent.code,
        amount="7.00",
        signature="__internal__",
    )
    try:
        with caplog.at_level(logging.WARNING):
            result = await PaymentService.pay(factory, sender_id, request, require_signature=False)
    finally:
        _forget_the_route_cache(world)

    assert not competitor_timed_out, (
        f"the competitor could not update its row within {_COMPETITOR_TIMEOUT_S} s: it is queued behind "
        "a lock the payment holds, so no serialization failure was produced and this test measured nothing"
    )
    # PREMISE: the retry was a genuine 40001 met in the payment's money phase, not a pass with no conflict.
    retries = [r.getMessage() for r in caplog.records if "event=payment.attempt_retry" in r.getMessage()]
    assert any("pgcode=40001" in message for message in retries), retries
    assert checkpoint_calls >= 4, checkpoint_calls

    assert result.status == "COMMITTED", result
    assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING + Decimal("7.00")}
    async with factory() as fresh:
        state = await fresh.scalar(select(Transaction.state).where(Transaction.tx_id == request.tx_id))
        audits = (
            await fresh.execute(select(IntegrityAuditLog).where(IntegrityAuditLog.tx_id == request.tx_id))
        ).scalars().all()
        name = await fresh.scalar(select(Participant.display_name).where(Participant.id == sender_id))
    assert state == "COMMITTED"
    assert len(audits) == 1, "the re-run attempt writes exactly one audit row; the first attempt's is gone"
    # The failed first attempt's update of the contended row was rolled back with it.
    assert name == "competitor", name
