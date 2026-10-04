"""A REAL `40001` inside the payment's audit write reaches the owner of the retries as `40001`.

027 `T2706` (§15 P2): restored on a real deadlock (`40P01`, `deadlock_after_the_wait`); the text is history.

T401 (programme 004; AGENTS §9 "проглоченный 40001 отравляет транзакцию"): a serialization failure raised
while the payment writes its integrity audit must not be swallowed - PostgreSQL has aborted the
transaction, and a swallowed `40001` shows up only later as a misleading `25P02` that no retry predicate
accepts. Until programme 019 stage 4 this was proved on `PaymentEngine.commit` of a seeded `PREPARED`
row (the engine retried its unit of work). Since stage 4 the audit row is written inside the payment
operation of `PaymentService` (`_write_integrity_audit`: a database error propagates, any other failure
is best-effort), and `pay()` owns the retries: the whole attempt re-runs on a fresh session. This module
holds the same property on that path (manifest `t1901`, 5.1, rows of the dropped engine module).

THE CONFLICT IS REAL. At the first statement of the first attempt's audit write (since 024 `T2413.2` the
audit computes no checkpoint; its own statement - the equivalent code lookup - is the site) a competitor holds a
bystander participant's row (not the sender's: since 028 `F-028-28` the payment holds the rows of its own
participants `FOR SHARE`), and the payment updates that row too: PostgreSQL raises `40001` inside the audit's `try`; no DBAPI error is fabricated. The
competitor's wait is bounded: if a future change row-locks that participant, the test goes red on
`competitor_timed_out` instead of hanging. The countercheck: the same site of the RE-RUN raises a
non-database error, which stays best-effort - the payment still commits, without its audit row, and the
skip is logged.

MUTATION that must redden this: in `_write_integrity_audit`, swallow `DBAPIError` like any other failure
(the transaction is then poisoned and the payment fails with 25P02 / a safe 500 instead of retrying).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.p019_support import deadlock_after_the_wait
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
        committed_database.url, pool_size=5, max_overflow=0, isolation_level="READ COMMITTED"
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
    async with factory() as s:  # the contended row: a participant the payment does not touch (028 `F-028-28`)
        bystander = Participant(pid=f"BY-{uuid.uuid4().hex[:8]}", display_name="by", public_key=f"pk-by-{uuid.uuid4()}")
        s.add(bystander)
        await s.commit()
    contended_id = bystander.id
    audit_calls = 0

    async def _competitor_updates_the_contended_row(holding) -> None:
        async with factory() as competitor:
            await competitor.execute(select(Participant.id).where(Participant.id == contended_id).with_for_update(key_share=True))
            await deadlock_after_the_wait(competitor, holding, select(TrustLine.id).where(
                TrustLine.equivalent_id == world.equivalent.id).with_for_update())
            await competitor.execute(
                update(Participant).where(Participant.id == contended_id).values(display_name="competitor")
            )
            await competitor.commit()

    async def _conflict(session) -> None:
        holding = asyncio.Event()
        competitors.append(asyncio.create_task(_competitor_updates_the_contended_row(holding)))
        await asyncio.wait_for(holding.wait(), _COMPETITOR_TIMEOUT_S)
        await session.execute(update(Participant).where(Participant.id == contended_id).values(display_name="payment"))

    competitors: list[asyncio.Task] = []
    original_audit = PaymentService._write_integrity_audit

    async def audit_with_a_conflict(self, tx_id, **kwargs):
        nonlocal audit_calls
        audit_calls += 1
        call, session = audit_calls, self.session
        real_execute = session.execute

        async def first_statement(*args, **kw):
            session.execute = real_execute  # only the audit's first statement is the site
            if call == 1:
                await _conflict(session)
            elif call == 2:
                # Countercheck: on the re-run, a non-database audit failure stays best-effort.
                raise ValueError("non-database audit failure")
            return await real_execute(*args, **kw)

        session.execute = first_statement
        try:
            return await original_audit(self, tx_id, **kwargs)
        finally:
            session.__dict__.pop("execute", None)

    monkeypatch.setattr(PaymentService, "_write_integrity_audit", audit_with_a_conflict)

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

    # PREMISE: the retry was a genuine 40001 met in the payment's money phase, not a pass with no conflict.
    retries = [r.getMessage() for r in caplog.records if "event=payment.attempt_retry" in r.getMessage()]
    await asyncio.gather(*competitors)
    assert any("pgcode=40P01" in message for message in retries), retries
    assert audit_calls == 2, audit_calls
    assert any("event=payment.audit_log_failed" in r.getMessage() for r in caplog.records), "countercheck not reached"

    assert result.status == "COMMITTED", result
    assert await _debts(factory, world) == {(world.sender.pid, world.receiver.pid): _OPENING + Decimal("7.00")}
    async with factory() as fresh:
        state = await fresh.scalar(select(Transaction.state).where(Transaction.tx_id == request.tx_id))
        audits = (
            await fresh.execute(select(IntegrityAuditLog).where(IntegrityAuditLog.tx_id == request.tx_id))
        ).scalars().all()
        name = await fresh.scalar(select(Participant.display_name).where(Participant.id == contended_id))
    assert state == "COMMITTED"
    assert audits == [], "the first attempt's row is gone, and the re-run skipped its own (best-effort)"
    # The failed first attempt's update of the contended row was rolled back with it.
    assert name == "competitor", name
