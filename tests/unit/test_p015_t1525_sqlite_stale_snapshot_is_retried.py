"""T1525: a stale SQLite snapshot is a retryable conflict, and the retry sees the concurrent value.

WHAT THIS IS ABOUT. The transaction control of T1525 gives a SQLite unit of work a real read
snapshot. The price is that a transaction which has read and then writes fails at once with
SQLITE_BUSY_SNAPSHOT ("database is locked") if another connection committed in between, and waiting
cannot cure it. Before the control the same race surfaced through the ORM as `StaleDataError`, which
`PaymentEngine._apply_flow` retries - so without a dialect-aware classifier the fix silently REMOVED
a retry that used to work, and turned a recoverable conflict into a failed payment.

`PaymentEngine._is_retryable_db_error` now treats the SQLITE_BUSY family as retryable on a SQLite
bind, matched on `sqlite3`'s error CODE rather than its message (all of them read "database is
locked"). The retry wrapper rolls back before re-running (`_run_uow_with_retry`, the
`if not use_savepoint` branch), which is the only thing that can cure a stale snapshot: the second
attempt starts a new transaction and therefore a new snapshot.

THE ROLLBACK IS THE RETRY'S PRECONDITION, NOT A COURTESY (corrected 2026-09-12). A SQLite busy does
not by itself mean the transaction rolled back - a busy raised by `commit()` with a statement still
in progress leaves it open with its own rows visible - so the wrapper's rollback is what keeps the
second attempt off the first attempt's uncommitted writes, and a rollback that FAILS now stops the
retry instead of being swallowed.

These tests assert the EFFECT of that: a payment whose commit loses the snapshot race is retried and
succeeds, with the concurrent write visible; a non-retryable SQLite error is still not retried; and
the budget is finite, so a permanently losing payment ends in a refusal rather than a loop.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import settings
from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService, _classify_payment_db_error
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.sqlite_transaction_control import sqlite_busy_error_name
from app.utils.exceptions import RetryablePaymentConflictException
from tests.unit.test_p015_t1525_sqlite_savepoint_is_not_a_transaction import (
    _PAYMENT,
    _World,
    _cleanup,
    _seed_world,
    _stored_debts,
)

_CONCURRENT = Decimal("3.25")


async def _make_outsiders(factory, world: _World) -> tuple[Participant, Participant]:
    """Two participants outside the payment's route, so the concurrent write is not its own effect."""
    n = uuid.uuid4().hex[:8].upper()
    async with factory() as s:
        x = Participant(
            pid=f"T1525_X_{n}", display_name="X", public_key=f"pk_t1525_x_{n}",
            type="person", status="active", profile={},
        )
        y = Participant(
            pid=f"T1525_Y_{n}", display_name="Y", public_key=f"pk_t1525_y_{n}",
            type="person", status="active", profile={},
        )
        s.add_all([x, y])
        await s.commit()
    return x, y


def _inject_concurrent_commit(monkeypatch, factory, world: _World, x, y, *, only_first: bool):
    """Commit a debt from ANOTHER session in the middle of the payment's commit unit of work.

    The hook is `_snapshot_net_positions`: by then the committing transaction has done its reads and
    holds a snapshot, and its first write (`_apply_flow`) is still ahead. `only_first=True` lets the
    second attempt succeed; `only_first=False` keeps every attempt losing the race.
    """
    # Counts INJECTIONS, not calls: `_snapshot_net_positions` runs twice per successful unit of work
    # (once before the flows, once inside `check_payment_delta`), so calls are not attempts.
    injections: list[int] = []
    original = PaymentEngine._snapshot_net_positions

    async def _let_someone_else_commit_first(self, **kwargs):
        if not (only_first and injections):
            injections.append(1)
            async with factory() as other:
                debt = (
                    await other.execute(
                        select(Debt).where(
                            Debt.equivalent_id == world.equivalent.id,
                            Debt.debtor_id == x.id,
                            Debt.creditor_id == y.id,
                        )
                    )
                ).scalar_one_or_none()
                if debt is None:
                    # A pair of its own: `uq_debts_debtor_creditor_equivalent` allows one row per
                    # pair, so later attempts raise this one's amount instead of inserting again.
                    other.add(
                        Debt(
                            debtor_id=x.id,
                            creditor_id=y.id,
                            equivalent_id=world.equivalent.id,
                            amount=_CONCURRENT,
                        )
                    )
                else:
                    debt.amount = Decimal(str(debt.amount)) + _CONCURRENT
                await other.commit()
        return await original(self, **kwargs)

    monkeypatch.setattr(PaymentEngine, "_snapshot_net_positions", _let_someone_else_commit_first)
    return injections


async def _concurrent_debts(factory, world: _World, x, y) -> list[Decimal]:
    async with factory() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.amount).where(
                    Debt.equivalent_id == world.equivalent.id,
                    Debt.debtor_id == x.id,
                    Debt.creditor_id == y.id,
                )
            )
        ).all()
    return [Decimal(str(a)) for (a,) in rows]


@pytest.mark.asyncio
async def test_a_payment_that_loses_the_snapshot_race_is_retried_and_commits(
    db_session, monkeypatch, caplog
) -> None:
    """RED without the dialect-aware classifier: the payment dies of "database is locked".

    The concurrent commit lands after the payment's commit unit of work has read and before it
    writes, so its first attempt hits SQLITE_BUSY_SNAPSHOT. The second attempt must start from a
    fresh snapshot - which is why the concurrent debt has to be visible to it - and commit.
    """
    from tests.conftest import TestingSessionLocal, engine

    assert engine.dialect.name == "sqlite", engine.dialect.name
    world = await _seed_world(TestingSessionLocal)
    x, y = await _make_outsiders(TestingSessionLocal, world)
    injections = _inject_concurrent_commit(
        monkeypatch, TestingSessionLocal, world, x, y, only_first=True
    )
    tx_id = f"t1525-retry-{uuid.uuid4().hex[:12]}"
    world.tx_ids.add(tx_id)
    try:
        with caplog.at_level("WARNING", logger="app.core.payments.engine"):
            async with TestingSessionLocal() as session:
                result = await PaymentService(session).create_payment_internal(
                    world.sender.id,
                    to_pid=world.receiver.pid,
                    equivalent=world.equivalent.code,
                    amount=str(_PAYMENT),
                    idempotency_key=tx_id,
                )

        assert result.status == "COMMITTED", result
        # One concurrent commit, and exactly one retry - taken for the snapshot conflict and for
        # nothing else. Without the dialect-aware classifier there is no retry line at all and the
        # payment dies of "database is locked".
        assert len(injections) == 1, injections
        retries = [
            record.getMessage()
            for record in caplog.records
            if "event=payment.uow_retry" in record.getMessage()
        ]
        assert len(retries) == 1, retries
        assert "SQLITE_BUSY_SNAPSHOT" in retries[0], retries

        # The effect, read back through a new session: the payment is stored with its exact amount,
        # and so is the write that beat it - the second attempt read the new snapshot, it did not
        # resurrect the old one.
        debts = await _stored_debts(TestingSessionLocal, world)
        assert debts[(world.sender.pid, world.receiver.pid)] == _PAYMENT, debts
        assert await _concurrent_debts(TestingSessionLocal, world, x, y) == [_CONCURRENT]
        async with TestingSessionLocal() as fresh:
            state = await fresh.scalar(
                select(Transaction.state).where(Transaction.tx_id == tx_id)
            )
        assert state == "COMMITTED"
    finally:
        async with TestingSessionLocal() as s:
            for participant in (x, y):
                await s.execute(
                    Debt.__table__.delete().where(Debt.debtor_id == participant.id)
                )
            await s.execute(
                Participant.__table__.delete().where(Participant.id.in_([x.id, y.id]))
            )
            await s.commit()
        await _cleanup(TestingSessionLocal, world)


@pytest.mark.asyncio
async def test_a_payment_that_keeps_losing_the_race_is_refused_after_a_finite_budget(
    db_session, monkeypatch
) -> None:
    """Counter-proof for the retry budget: a permanently losing payment refuses, it does not loop.

    Every attempt is beaten by a concurrent commit, so every attempt raises. The wrapper must stop
    after `COMMIT_RETRY_ATTEMPTS` and the payment must not be stored.
    """
    from tests.conftest import TestingSessionLocal

    world = await _seed_world(TestingSessionLocal)
    x, y = await _make_outsiders(TestingSessionLocal, world)
    injections = _inject_concurrent_commit(
        monkeypatch, TestingSessionLocal, world, x, y, only_first=False
    )
    tx_id = f"t1525-budget-{uuid.uuid4().hex[:12]}"
    world.tx_ids.add(tx_id)
    try:
        with pytest.raises(RetryablePaymentConflictException):
            async with TestingSessionLocal() as session:
                await PaymentService(session).create_payment_internal(
                    world.sender.id,
                    to_pid=world.receiver.pid,
                    equivalent=world.equivalent.code,
                    amount=str(_PAYMENT),
                    idempotency_key=tx_id,
                )
        # Every attempt was beaten, and there were exactly as many attempts as the budget allows.
        assert len(injections) == int(settings.COMMIT_RETRY_ATTEMPTS), injections
        debts = await _stored_debts(TestingSessionLocal, world)
        assert (world.sender.pid, world.receiver.pid) not in debts, debts
        async with TestingSessionLocal() as fresh:
            state = await fresh.scalar(
                select(Transaction.state).where(Transaction.tx_id == tx_id)
            )
        assert state != "COMMITTED", state
    finally:
        async with TestingSessionLocal() as s:
            for participant in (x, y):
                await s.execute(
                    Debt.__table__.delete().where(Debt.debtor_id == participant.id)
                )
            await s.execute(
                Participant.__table__.delete().where(Participant.id.in_([x.id, y.id]))
            )
            await s.commit()
        await _cleanup(TestingSessionLocal, world)


@pytest.mark.asyncio
async def test_the_classifier_reads_the_error_code_and_refuses_everything_else(db_session) -> None:
    """Counter-proof for the predicate: busy is retryable BY CODE, an integrity error is not.

    Both errors are produced by the real driver, not constructed: the first from a genuine snapshot
    race, the second from a foreign key the database refuses. The assertion on `sqlite_errorname`
    is also the anti-vacuum for the matching rule - if a driver update stopped surfacing the code,
    the predicate would go quietly permissive and this is what notices.
    """
    from tests.conftest import TestingSessionLocal

    world = await _seed_world(TestingSessionLocal)
    try:
        # 1. A real SQLITE_BUSY_SNAPSHOT: two readers, one writes and commits, the other writes.
        async with TestingSessionLocal() as reader, TestingSessionLocal() as writer:
            await reader.execute(select(Debt.id).limit(1))
            await writer.execute(select(Debt.id).limit(1))
            writer.add(
                Debt(
                    debtor_id=world.sender.id,
                    creditor_id=world.receiver.id,
                    equivalent_id=world.equivalent.id,
                    amount=Decimal("1.00"),
                )
            )
            await writer.commit()
            reader.add(
                Debt(
                    debtor_id=world.receiver.id,
                    creditor_id=world.sender.id,
                    equivalent_id=world.equivalent.id,
                    amount=Decimal("2.00"),
                )
            )
            with pytest.raises(DBAPIError) as busy:
                await reader.flush()
            engine_on_reader = PaymentEngine(reader)
            await reader.rollback()

        assert sqlite_busy_error_name(busy.value) == "SQLITE_BUSY_SNAPSHOT"
        assert getattr(busy.value.orig, "sqlite_errorcode", None) == 517
        assert engine_on_reader._is_retryable_db_error(busy.value, op="commit") is True
        assert isinstance(_classify_payment_db_error(busy.value), RetryablePaymentConflictException)

        # 2. A real integrity error on the same backend must stay non-retryable.
        async with TestingSessionLocal() as session:
            session.add(
                Debt(
                    debtor_id=uuid.uuid4(),  # no such participant
                    creditor_id=world.receiver.id,
                    equivalent_id=world.equivalent.id,
                    amount=Decimal("1.00"),
                )
            )
            with pytest.raises(IntegrityError) as integrity:
                await session.flush()
            engine_on_session = PaymentEngine(session)
            await session.rollback()

        assert sqlite_busy_error_name(integrity.value) is None
        assert engine_on_session._is_retryable_db_error(integrity.value, op="commit") is False
        assert not isinstance(
            _classify_payment_db_error(integrity.value), RetryablePaymentConflictException
        )
    finally:
        PaymentRouter.invalidate_cache(world.equivalent.code)
        await _cleanup(TestingSessionLocal, world)
