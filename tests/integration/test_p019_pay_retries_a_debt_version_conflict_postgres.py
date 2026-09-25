"""Programme 019 stage 3 (`T1904`): `pay()` owns the retries of the API path, and the commit metric is
counted once per confirmed commit.

THE SCHEDULE IS REAL. A payment S -> R grows S's existing debt to R. Between the book reading that debt
and flushing its new amount, another session commits a change to the same debt row (a `TEST_FIXTURE`
operation of its own). The payment's `UPDATE ... WHERE version = n` then matches no row: a genuine
`StaleDataError` from the ORM's optimistic lock (`app/db/models/debt.py`, `version_id_col`). READ
COMMITTED is what lets the payment's own statements see the newer row; under SERIALIZABLE the same
race is a `40001`, which `test_p019_retryable_conflict_is_not_stored_aborted_postgres.py` covers.

WHAT IS ASSERTED
* the book raised the narrow `DebtVersionConflict` once and did NOT retry it from the same snapshot
  (`FORK-1`): one `event=apply_flow.debt_version_conflict`;
* `pay()` classified it as retryable and retried the WHOLE attempt on a fresh session: one
  `event=payment.attempt_retry` naming it, and the second attempt paid on top of the concurrent value
  (`100 + 5 + 10`) - the first attempt's work is gone, not doubled;
* one `COMMITTED` row, one completed envelope, one integrity audit row, one `payment.received`
  publication;
* `PAYMENT_EVENTS_TOTAL{commit,success}` went up by exactly ONE for the payment - not once per attempt
  (`FORK-9`) - and a replay of the same request (a stored result) adds nothing.

MUTATIONS THIS CATCHES: the book retrying in place again (no conflict reaches `pay()`, no attempt
retry); `pay()` not retrying `DebtVersionConflict` (the payment fails `409`); applying the post-commit
effects per attempt or before the commit (the metric goes up by two, or the publication doubles).
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.ledger.book as book_module
from app.config import settings
from app.core.payments.service import PaymentService, _payment_db_sqlstate
from app.db.journal_tables import debt_operations
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from app.schemas.payment import PaymentCreateRequest
from app.utils.event_bus import event_bus
from app.utils.metrics import PAYMENT_EVENTS_TOTAL
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p015_p1_money_replay_postgres import (
    _OPENING,
    _debts,
    _forget_the_route_cache,
    _seed,
)


@pytest_asyncio.fixture
async def rc_factory(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=5, max_overflow=0, isolation_level="READ COMMITTED"
    )
    try:
        yield async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
    finally:
        await engine.dispose()


def _commit_success() -> float:
    return PAYMENT_EVENTS_TOTAL.labels(event="commit", result="success")._value.get()


@pytest.mark.asyncio
async def test_pay_retries_a_debt_version_conflict_on_a_fresh_attempt_and_counts_one_commit(
    rc_factory, monkeypatch, caplog
) -> None:
    world = await _seed(rc_factory)
    bump = Decimal("5.00")
    bumped: list[int] = []
    original_get_debt = book_module._get_debt

    async def get_debt_then_a_concurrent_writer_commits(session, debtor_id, creditor_id, eq_id):
        debt = await original_get_debt(session, debtor_id, creditor_id, eq_id)
        if not bumped and debt is not None and (debtor_id, creditor_id) == (
            world.sender.id,
            world.receiver.id,
        ):
            bumped.append(1)
            async with rc_factory() as other:
                row = (await other.execute(select(Debt).where(Debt.id == debt.id))).scalar_one()
                new_amount = row.amount + bump
                async with debt_fixture_setup(other, label="concurrent-writer"):
                    row.amount = new_amount
                await other.commit()
        return debt

    publications: list[dict] = []
    monkeypatch.setattr(event_bus, "publish", lambda **kw: publications.append(dict(kw)))
    monkeypatch.setattr(book_module, "_get_debt", get_debt_then_a_concurrent_writer_commits)

    tx_id = str(uuid.uuid4())
    request = PaymentCreateRequest(
        tx_id=tx_id,
        to=world.receiver.pid,
        equivalent=world.equivalent.code,
        amount="10.00",
        signature="__internal__",
    )
    before = _commit_success()
    try:
        with caplog.at_level(logging.WARNING):
            result = await PaymentService.pay(
                rc_factory, world.sender.id, request, require_signature=False
            )
        after_payment = _commit_success()
        replay = await PaymentService.pay(
            rc_factory, world.sender.id, request, require_signature=False
        )
        after_replay = _commit_success()
    finally:
        _forget_the_route_cache(world)

    # ── the schedule happened: one real stale version, raised once, retried by pay() ─────────
    assert bumped == [1], "premise: the concurrent writer never committed inside the flow"
    messages = [r.getMessage() for r in caplog.records]
    conflicts = [m for m in messages if "event=apply_flow.debt_version_conflict" in m]
    retries = [m for m in messages if "event=payment.attempt_retry " in m]
    assert len(conflicts) == 1, messages
    assert len(retries) == 1 and "pgcode=DebtVersionConflict" in retries[0], retries
    assert "where=execute" in retries[0], retries

    # ── the payment: once, on top of the concurrent value ─────────────────────────────────────
    assert result.status == "COMMITTED", result
    assert replay.status == "COMMITTED" and replay.tx_id == tx_id, replay
    assert await _debts(rc_factory, world) == {
        (world.sender.pid, world.receiver.pid): _OPENING + bump + Decimal("10.00")
    }
    async with rc_factory() as s:
        states = (
            await s.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))
        ).scalars().all()
        envelopes = (
            await s.execute(
                select(debt_operations.c.state).where(
                    debt_operations.c.kind == "PAYMENT", debt_operations.c.identity == tx_id
                )
            )
        ).scalars().all()
        audits = await s.scalar(
            select(func.count())
            .select_from(IntegrityAuditLog)
            .where(IntegrityAuditLog.tx_id == tx_id)
        )
    assert states == ["COMMITTED"], states
    assert envelopes == ["COMPLETED"], envelopes
    assert audits == 1, audits
    assert [p["event"] for p in publications] == ["payment.received"], publications

    # ── the commit metric: once per confirmed commit, not per attempt, not on the replay ──────
    assert after_payment - before == 1, (before, after_payment)
    assert after_replay == after_payment, (after_payment, after_replay)


@pytest_asyncio.fixture
async def serializable_factory(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=5, max_overflow=0, isolation_level="SERIALIZABLE"
    )
    try:
        yield async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_commit_refused_by_ssi_is_retried_and_counted_once(
    serializable_factory, monkeypatch, caplog
) -> None:
    """The payment's execute() SUCCEEDS and its one COMMIT is refused with a real `40001`.

    THE SCHEDULE. Right before the first attempt's COMMIT another SERIALIZABLE transaction reads the
    pair's debt row (the old version - the payment's write is not visible to it) and rewrites the trust
    line the payment's routing and capacity re-check read, and commits. The payment now has a
    read-write dependency in both directions with a committed transaction; PostgreSQL refuses its
    COMMIT (`40001`), and nothing of it landed. `pay()` retries the whole attempt, which commits.

    WHAT IT CATCHES that the conflict inside `execute()` cannot: effects applied between a successful
    `execute()` and a confirmed commit. Applying them there counts `commit,success` (and publishes
    `payment.received`) for the refused attempt as well - two instead of one.
    """

    from app.db.models.trustline import TrustLine

    factory = serializable_factory
    world = await _seed(factory)
    competed: list[int] = []

    real_commit = AsyncSession.commit
    real_execute = PaymentService.execute

    async def marking_execute(self, *args, **kwargs):
        staged = await real_execute(self, *args, **kwargs)
        self.session.info["p019_payment_executed"] = True
        return staged

    async def commit_after_a_competitor(self):
        if not competed and self.info.pop("p019_payment_executed", False):
            competed.append(1)
            async with factory() as other:
                await other.execute(select(Debt.amount).where(Debt.equivalent_id == world.equivalent.id))
                await other.execute(
                    update(TrustLine)
                    .where(
                        TrustLine.from_participant_id == world.receiver.id,
                        TrustLine.to_participant_id == world.sender.id,
                        TrustLine.equivalent_id == world.equivalent.id,
                    )
                    .values(limit=TrustLine.limit)
                )
                await other.commit()
        return await real_commit(self)

    publications: list[dict] = []
    monkeypatch.setattr(event_bus, "publish", lambda **kw: publications.append(dict(kw)))
    monkeypatch.setattr(AsyncSession, "commit", commit_after_a_competitor)
    monkeypatch.setattr(PaymentService, "execute", marking_execute)

    tx_id = str(uuid.uuid4())
    request = PaymentCreateRequest(
        tx_id=tx_id,
        to=world.receiver.pid,
        equivalent=world.equivalent.code,
        amount="10.00",
        signature="__internal__",
    )
    before = _commit_success()
    try:
        with caplog.at_level(logging.WARNING):
            result = await PaymentService.pay(factory, world.sender.id, request, require_signature=False)
    finally:
        _forget_the_route_cache(world)
    after = _commit_success()

    retries = [r.getMessage() for r in caplog.records if "event=payment.attempt_retry " in r.getMessage()]
    assert competed == [1], "premise: the competitor never ran before the payment's commit"
    assert len(retries) == 1 and "pgcode=40001" in retries[0] and "where=commit" in retries[0], retries
    assert result.status == "COMMITTED", result
    assert await _debts(factory, world) == {
        (world.sender.pid, world.receiver.pid): _OPENING + Decimal("10.00")
    }
    assert [p["event"] for p in publications] == ["payment.received"], publications
    assert after - before == 1, (before, after)


@pytest.mark.asyncio
async def test_a_commit_refused_by_ssi_with_no_budget_left_records_nothing(
    serializable_factory, monkeypatch, caplog
) -> None:
    """019 `T1905` (FORK-4): the same real `40001` on the payment's one COMMIT, with no attempt left
    (`COMMIT_RETRY_ATTEMPTS = 1`). An exhausted conflict is still a conflict: `409/E008` with
    `retryable: true`, NO `ABORTED` row, and the resubmission of the same `tx_id` executes. Until
    `T1905` the exhausted commit conflict was recorded `ABORTED/E008` and the resubmission answered it
    (`T1902`, confirmed on prepare and commit)."""

    from app.db.models.trustline import TrustLine
    from app.utils.exceptions import RetryablePaymentConflictException

    factory = serializable_factory
    world = await _seed(factory)
    competed: list[int] = []

    real_commit = AsyncSession.commit
    real_execute = PaymentService.execute

    async def marking_execute(self, *args, **kwargs):
        staged = await real_execute(self, *args, **kwargs)
        self.session.info["p019_payment_executed"] = True
        return staged

    async def commit_after_a_competitor(self):
        if not competed and self.info.pop("p019_payment_executed", False):
            competed.append(1)
            async with factory() as other:
                await other.execute(select(Debt.amount).where(Debt.equivalent_id == world.equivalent.id))
                await other.execute(
                    update(TrustLine)
                    .where(
                        TrustLine.from_participant_id == world.receiver.id,
                        TrustLine.to_participant_id == world.sender.id,
                        TrustLine.equivalent_id == world.equivalent.id,
                    )
                    .values(limit=TrustLine.limit)
                )
                await other.commit()
        return await real_commit(self)

    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(AsyncSession, "commit", commit_after_a_competitor)
    monkeypatch.setattr(PaymentService, "execute", marking_execute)

    tx_id = str(uuid.uuid4())
    request = PaymentCreateRequest(
        tx_id=tx_id,
        to=world.receiver.pid,
        equivalent=world.equivalent.code,
        amount="10.00",
        signature="__internal__",
    )
    try:
        with pytest.raises(RetryablePaymentConflictException) as refused:
            await PaymentService.pay(factory, world.sender.id, request, require_signature=False)
        async with factory() as s:
            stored = (
                await s.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))
            ).scalars().all()
        debts_after_conflict = await _debts(factory, world)
        resubmitted = await PaymentService.pay(factory, world.sender.id, request, require_signature=False)
    finally:
        _forget_the_route_cache(world)

    assert competed == [1], "premise: the competitor never ran before the payment's commit"
    assert refused.value.details == {"retryable": True, "conflict_kind": "database_concurrency"}
    assert _payment_db_sqlstate(refused.value.__cause__) == "40001", refused.value.__cause__
    assert debts_after_conflict == {(world.sender.pid, world.receiver.pid): _OPENING}
    assert stored == [], stored
    assert resubmitted.status == "COMMITTED", resubmitted
    assert await _debts(factory, world) == {
        (world.sender.pid, world.receiver.pid): _OPENING + Decimal("10.00")
    }
