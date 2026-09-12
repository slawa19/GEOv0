"""Programme 015 / P1: the tick's money phase replayed on a REAL SQLite conflict.

WHAT THIS STAND FORCES, and why every part of it is load-bearing.

The conflict is genuine, never synthesized. A file-backed WAL database with the production
transaction control (`app/db/sqlite_transaction_control.py`) gives a unit of work that has read a
real snapshot; a second connection then commits; the first staged write of the tick fails at once
with SQLITE_BUSY_SNAPSHOT, which `busy_timeout` cannot cure because waiting does not make an old
snapshot current. That is the same shape `PaymentEngine` classifies as retryable and the payment
service turns into `RetryablePaymentConflictException`.

WHERE THE COMPETITOR COMMITS is the whole design. It commits AFTER the tick's debt snapshot read
and BEFORE the first staged write. Not earlier - there would be no stale snapshot; and not after a
staged write has already succeeded - SQLite allows a single writer, so demanding a concurrent
writer there would be demanding something the database does not permit, and a stand built that way
would be measuring its own impossibility.

THE FRESHNESS PROOF IS CAUSAL, not "the plan differs". The planner's generator is seeded from
`seed` and `tick_index` alone (`real_payment_planner.py:363`), both of which are preserved across
attempts, and with no `amount_model` the amount is `0.1 + rng.random() * cap` (`:242`). The RNG
draw is therefore IDENTICAL on both attempts and the debt snapshot is the only input that changed.
So a strictly smaller amount on the replay can only have come from a cap reduced by the
competitor's commit - which is exactly the reason the design forbids reusing `planned`: the old
plan was sized against a picture the competitor had already refuted, and it does not fit any more.

EVERYTHING IS ASSERTED THROUGH AN INDEPENDENT SESSION, on a new connection, never the one that did
the work - whose identity map would answer from memory.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.db.sqlite_transaction_control import (
    install_sqlite_transaction_control,
    sqlite_busy_error_name,
)
from app.utils.exceptions import RetryablePaymentConflictException
from tests.scratch_db import (
    install_test_sqlite_pragmas,
    remove_scratch_db,
    scratch_db_url,
)

_SLUG = "p1-money-replay-sqlite"

#: The trust line's limit, and therefore the planner's cap on the first attempt.
_LIMIT = Decimal("1000.00")
#: What the competitor commits onto the very edge the tick plans on. Large enough that the first
#: attempt's amount cannot fit in what is left, which is what makes the stale plan demonstrably
#: wrong rather than merely different.
_COMPETITOR = Decimal("900.00")
_REMAINING = _LIMIT - _COMPETITOR


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def factory():
    url = scratch_db_url(_SLUG)
    engine = create_async_engine(
        url, echo=False, poolclass=NullPool, connect_args={"timeout": 10}
    )
    # The same connection pragmas AND the same transaction control as the application engine.
    # Without the pragmas this module would run in the rollback journal, where the locking is
    # different and SQLITE_BUSY_SNAPSHOT does not arise - the stand would be unable to see the
    # conflict it exists for.
    install_test_sqlite_pragmas(engine.sync_engine, url=url)
    install_sqlite_transaction_control(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    try:
        yield session_factory
    finally:
        await engine.dispose()
        remove_scratch_db(_SLUG)


@dataclass
class _World:
    equivalent: Equivalent
    sender: Participant
    receiver: Participant
    outsider_a: Participant
    outsider_b: Participant


async def _seed(session_factory) -> _World:
    n = uuid.uuid4().hex[:8].upper()
    async with session_factory() as s:
        eq = Equivalent(code=f"P1SQL{n}"[:16], precision=2, is_active=True, metadata_={})
        people = [
            Participant(
                pid=f"P1_{role}_{n}",
                display_name=role,
                public_key=f"pk_p1_{role}_{n}",
                type="person",
                status="active",
                profile={},
            )
            for role in ("S", "R", "X", "Y")
        ]
        s.add_all([eq, *people])
        await s.flush()
        sender, receiver, outsider_a, outsider_b = people
        # Trust line direction is creditor -> debtor: the receiver extends credit to the sender,
        # which is what lets the sender pay the receiver.
        s.add(
            TrustLine(
                from_participant_id=receiver.id,
                to_participant_id=sender.id,
                equivalent_id=eq.id,
                limit=_LIMIT,
                status="active",
            )
        )
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return _World(eq, sender, receiver, outsider_a, outsider_b)


class _Sse:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"e{run._event_seq}"

    def broadcast(self, _run_id: str, payload: dict[str, Any]) -> None:
        if isinstance(payload, dict):
            self.events.append(payload)

    def published(self, event_type: str) -> int:
        return sum(1 for e in self.events if str(e.get("type")) == event_type)


class _Artifacts:
    def write_real_tick_artifact(self, *a, **kw) -> None:
        return None

    def enqueue_event_artifact(self, *a, **kw) -> None:
        return None


def _run_record(world: _World, run_id: str) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="p1-sqlite", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1  # not a clearing tick
    run.sim_time_ms = 1_000
    run.intensity_percent = 100
    run._real_seeded = True
    run._real_participants = [
        (world.sender.id, world.sender.pid),
        (world.receiver.id, world.receiver.pid),
    ]
    run._real_equivalents = [world.equivalent.code]
    run._real_viz_by_eq = {}
    run._edges_by_equivalent = {}
    return run


def _scenario(world: _World) -> dict[str, Any]:
    return {
        "equivalents": [world.equivalent.code],
        "participants": [{"id": world.sender.pid}, {"id": world.receiver.pid}],
        "trustlines": [
            {
                "from": world.receiver.pid,
                "to": world.sender.pid,
                "equivalent": world.equivalent.code,
                "limit": str(_LIMIT),
                "status": "active",
            }
        ],
        "behaviorProfiles": [],
    }


def _runner(run: RunRecord, scenario: dict[str, Any], sse: _Sse) -> RealRunner:
    return RealRunner(
        lock=threading.RLock(),
        get_run=lambda _rid: run,
        get_scenario_raw=lambda _sid: scenario,
        sse=sse,
        artifacts=_Artifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: True,
        actions_per_tick_max=1,  # exactly one payment per tick keeps the money assertions exact
        clearing_every_n_ticks=10_000,  # never a clearing tick
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("tests.p015.p1.sqlite"),
    )


def _install(monkeypatch, session_factory) -> None:
    import app.core.simulator.storage as simulator_storage
    import app.db.session as app_db_session

    async def _noop(*_a, **_kw):
        return None

    for name in ("write_tick_metrics", "write_tick_bottlenecks", "sync_artifacts", "upsert_run"):
        monkeypatch.setattr(simulator_storage, name, _noop)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", session_factory)


def _record_plans(monkeypatch, runner: RealRunner) -> list[list[Any]]:
    """Every plan the tick produced, in order. One list entry per money attempt."""
    plans: list[list[Any]] = []
    original = runner._plan_real_payments

    def _recording(run, scenario, *, debt_snapshot=None):
        planned = original(run, scenario, debt_snapshot=debt_snapshot)
        plans.append(list(planned))
        return planned

    monkeypatch.setattr(runner, "_plan_real_payments", _recording)
    return plans


def _record_conflict_causes(monkeypatch) -> list[str | None]:
    """The DRIVER error behind every conflict the tick's payments raised.

    What the money boundary sees is `RetryablePaymentConflictException`, because the payment
    service classifies the database error before the executor ever propagates it
    (`app/core/payments/service.py:109`). The typed exception on its own would not prove that this
    stand produced the conflict it was built for - "State conflict" is also what an owner-preflight
    change raises, and a stand that accepted any conflict would go green for the wrong reason.

    The original error survives as the exception's `__cause__` (`service.py:884`), so this reads
    the SQLite error CODE off it. Matching on the code rather than the message is the same
    discipline `sqlite_busy_error_name` documents: SQLITE_BUSY, SQLITE_BUSY_SNAPSHOT and a
    rollback-journal deadlock all read "database is locked".
    """
    causes: list[str | None] = []
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except RetryablePaymentConflictException as conflict:
            causes.append(sqlite_busy_error_name(conflict.__cause__))
            raise

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    return causes


def _competitor_after_snapshot(
    monkeypatch,
    runner: RealRunner,
    session_factory,
    *,
    debtor: Participant,
    creditor: Participant,
    equivalent: Equivalent,
    amount: Decimal,
    only_first: bool,
) -> list[int]:
    """Commit a debt from ANOTHER connection between the tick's snapshot and its first write.

    The hook is the debt snapshot read itself: by the time it returns, the tick's transaction has
    read and therefore holds a snapshot, and its first staged write is still ahead. That is the
    only window in which a second connection can commit and make the tick's write fail with
    SQLITE_BUSY_SNAPSHOT - and it is the window the design names.
    """
    commits: list[int] = []
    original = runner._load_debt_snapshot_by_pid

    async def _load_then_let_someone_else_commit(session, participants, equivalents):
        snapshot = await original(session, participants, equivalents)
        if not (only_first and commits):
            commits.append(1)
            async with session_factory() as other:
                existing = (
                    await other.execute(
                        select(Debt).where(
                            Debt.equivalent_id == equivalent.id,
                            Debt.debtor_id == debtor.id,
                            Debt.creditor_id == creditor.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    other.add(
                        Debt(
                            debtor_id=debtor.id,
                            creditor_id=creditor.id,
                            equivalent_id=equivalent.id,
                            amount=amount,
                        )
                    )
                else:
                    existing.amount = Decimal(str(existing.amount)) + amount
                await other.commit()
        return snapshot

    monkeypatch.setattr(
        runner, "_load_debt_snapshot_by_pid", _load_then_let_someone_else_commit
    )
    return commits


async def _debts(session_factory, world: _World) -> dict[tuple[str, str], Decimal]:
    pid_by_id = {
        world.sender.id: world.sender.pid,
        world.receiver.id: world.receiver.pid,
        world.outsider_a.id: world.outsider_a.pid,
        world.outsider_b.id: world.outsider_b.pid,
    }
    async with session_factory() as fresh:
        rows = (
            await fresh.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == world.equivalent.id
                )
            )
        ).all()
    return {
        (pid_by_id.get(d, str(d)), pid_by_id.get(c, str(c))): Decimal(str(a))
        for d, c, a in rows
    }


async def _transactions(session_factory) -> dict[str, str]:
    async with session_factory() as fresh:
        rows = (await fresh.execute(select(Transaction.tx_id, Transaction.state))).all()
    return {str(tx_id): str(state) for tx_id, state in rows}


async def _prepare_locks(session_factory) -> int:
    async with session_factory() as fresh:
        return len((await fresh.execute(select(PrepareLock.tx_id))).all())


@pytest.mark.asyncio
async def test_a_real_busy_snapshot_replays_the_money_phase_and_commits_once(
    factory, monkeypatch, caplog
) -> None:
    """RED before P1: the conflict ended the tick and its payment was never made.

    Before the money boundary existed, this SQLITE_BUSY_SNAPSHOT left the payments phase, the tick
    was logged as failed with `errors_total` incremented, and the next heartbeat started a NEW tick
    - so the load this tick was supposed to generate was silently lost, and contention alone could
    stop the run. Now the phase is replayed from a fresh snapshot and the payment is made once.
    """
    world = await _seed(factory)
    sse = _Sse()
    run = _run_record(world, f"p1-sqlite-{uuid.uuid4().hex[:8]}")
    runner = _runner(run, _scenario(world), sse)
    _install(monkeypatch, factory)
    plans = _record_plans(monkeypatch, runner)
    causes = _record_conflict_causes(monkeypatch)
    commits = _competitor_after_snapshot(
        monkeypatch,
        runner,
        factory,
        debtor=world.sender,
        creditor=world.receiver,
        equivalent=world.equivalent,
        amount=_COMPETITOR,
        only_first=True,
    )

    with caplog.at_level(logging.WARNING, logger="tests.p015.p1.sqlite"):
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=60.0)

    # ── The stand saw the conflict it was built for, and only that one ────────────────
    replays = [
        record.getMessage()
        for record in caplog.records
        if "simulator.real.money_phase_replay " in record.getMessage()
    ]
    assert len(replays) == 1, replays
    assert "conflict=RETRYABLE_PAYMENT_CONFLICT" in replays[0], replays
    # ...and the database error behind that typed conflict was a genuine stale-snapshot busy,
    # matched by its SQLite error code, not merely something that shares the exception type.
    assert causes == ["SQLITE_BUSY_SNAPSHOT"], causes
    assert len(commits) == 1, commits

    # ── The plan was recomputed, and the old one no longer fitted ─────────────────────
    assert len(plans) == 2, f"the money phase was not replanned: {plans}"
    first = Decimal(plans[0][0].amount)
    second = Decimal(plans[1][0].amount)
    assert first > _REMAINING, (
        f"stand is vacuous: the first plan ({first}) already fitted the capacity the competitor "
        f"left ({_REMAINING}), so reusing it would not have been observably wrong"
    )
    assert second <= _REMAINING, (
        f"the replan ({second}) was not capped by the competitor's commit; it did not read a "
        f"fresh snapshot"
    )
    assert second < first

    # ── The money, read back on a new connection ──────────────────────────────────────
    debts = await _debts(factory, world)
    assert debts == {(world.sender.pid, world.receiver.pid): _COMPETITOR + second}, (
        f"expected the competitor's {_COMPETITOR} plus the REPLANNED {second}; got {debts}"
    )

    transactions = await _transactions(factory)
    assert list(transactions.values()) == ["COMMITTED"], transactions
    assert len(transactions) == 1, f"the discarded attempt left a transaction behind: {transactions}"
    assert await _prepare_locks(factory) == 0

    # ── Published exactly once, counted exactly once ──────────────────────────────────
    assert sse.published("tx.updated") == 1
    assert sse.published("tx.failed") == 0
    assert run.committed_total == 1
    assert run.attempts_total == 1
    assert run.rejected_total == 0

    # ── A transient conflict is not an error ──────────────────────────────────────────
    assert run.errors_total == 0
    assert run._real_consec_tick_failures == 0
    assert run.state == "running"
    assert run._real_money_conflicts_total == 1
    assert run._real_money_replays_total == 1
    assert run._real_money_replay_exhausted_total == 0
    assert run._real_money_committed_ticks_total == 1
    assert run._real_consec_money_no_progress_ticks == 0


@pytest.mark.asyncio
async def test_permanent_contention_exhausts_the_budget_without_spending_the_error_budget(
    factory, monkeypatch, caplog
) -> None:
    """A bounded replay promises nothing under permanent contention - and says so honestly.

    Every attempt loses, so the tick ends with no money. What must NOT happen is the run being
    recorded as broken: `errors_total` stays at zero, the consecutive-tick-failure counter stays at
    zero, and the tick is reported under its own code. The competitor writes on an UNRELATED pair
    so that capacity never runs out - otherwise the later attempts would plan nothing and the test
    would pass without ever reaching a conflict.
    """
    world = await _seed(factory)
    sse = _Sse()
    run = _run_record(world, f"p1-sqlite-budget-{uuid.uuid4().hex[:8]}")
    runner = _runner(run, _scenario(world), sse)
    _install(monkeypatch, factory)
    plans = _record_plans(monkeypatch, runner)
    causes = _record_conflict_causes(monkeypatch)
    commits = _competitor_after_snapshot(
        monkeypatch,
        runner,
        factory,
        debtor=world.outsider_a,
        creditor=world.outsider_b,
        equivalent=world.equivalent,
        amount=Decimal("1.00"),
        only_first=False,
    )

    with caplog.at_level(logging.WARNING, logger="tests.p015.p1.sqlite"):
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=60.0)

    # Every attempt was made, and every one of them really conflicted.
    assert len(commits) == 3, commits
    assert len(plans) == 3, plans
    exhausted = [
        record.getMessage()
        for record in caplog.records
        if "simulator.real.money_phase_replay_exhausted" in record.getMessage()
    ]
    assert len(exhausted) == 1, exhausted
    assert "conflict=RETRYABLE_PAYMENT_CONFLICT" in exhausted[0], exhausted
    assert causes == ["SQLITE_BUSY_SNAPSHOT"] * 3, causes

    # No money, and nothing published.
    debts = await _debts(factory, world)
    assert debts == {(world.outsider_a.pid, world.outsider_b.pid): Decimal("3.00")}, debts
    assert await _transactions(factory) == {}
    assert await _prepare_locks(factory) == 0
    assert sse.published("tx.updated") == 0
    assert run.committed_total == 0

    # The error budget is untouched; the tick is recorded under its own code.
    assert run.errors_total == 0
    assert run._real_consec_tick_failures == 0
    assert run.state == "running"
    assert run.last_error is not None
    assert run.last_error["code"] == "REAL_MODE_MONEY_CONFLICT_UNRESOLVED"
    assert run._real_money_replay_exhausted_total == 1
    assert run._real_money_conflicts_total == 3
    assert run._real_money_replays_total == 2
    assert run._real_consec_money_no_progress_ticks == 1


@pytest.mark.asyncio
async def test_the_competitors_own_change_survives_the_replay(factory, monkeypatch) -> None:
    """Anti-vacuum for the rollback: the replay must discard ITS attempt, not the competitor's work.

    A rollback that reached too far would look identical in every assertion about the tick's own
    payment, and would be a silent data-loss bug for everyone else on the database.
    """
    world = await _seed(factory)
    sse = _Sse()
    run = _run_record(world, f"p1-sqlite-preserve-{uuid.uuid4().hex[:8]}")
    runner = _runner(run, _scenario(world), sse)
    _install(monkeypatch, factory)
    _record_plans(monkeypatch, runner)
    _competitor_after_snapshot(
        monkeypatch,
        runner,
        factory,
        debtor=world.outsider_a,
        creditor=world.outsider_b,
        equivalent=world.equivalent,
        amount=Decimal("7.50"),
        only_first=True,
    )

    await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=60.0)

    debts = await _debts(factory, world)
    assert debts[(world.outsider_a.pid, world.outsider_b.pid)] == Decimal("7.50"), (
        f"the replay rolled back a change that was not its own: {debts}"
    )
    assert sse.published("tx.updated") == 1
    assert run._real_money_replays_total == 1


@pytest.mark.asyncio
async def test_the_stand_runs_in_wal_with_the_production_transaction_control(factory) -> None:
    """Holds the stand's own preconditions in place.

    Both are what make a genuine SQLITE_BUSY_SNAPSHOT possible: WAL for the locking behaviour the
    application has, and the transaction control so a transaction that has only read still holds a
    real snapshot. If either were lost, the conflict tests above would stop conflicting and would
    go green for the wrong reason.
    """
    from sqlalchemy import text

    async with factory() as session:
        mode = (await session.execute(text("PRAGMA journal_mode"))).scalar_one()
        foreign_keys = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        in_transaction = raw.driver_connection.in_transaction
        await session.rollback()

    assert str(mode).lower() == "wal", mode
    assert int(foreign_keys) == 1, foreign_keys
    assert in_transaction is True, (
        "a read-only transaction held no database transaction: the T1525 control is not in effect "
        "and no snapshot exists to go stale"
    )
