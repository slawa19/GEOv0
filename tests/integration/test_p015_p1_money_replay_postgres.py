"""Programme 015 / P1: the tick's money phase replayed on a REAL PostgreSQL `40001`.

WHY THIS STAND EXISTS ALONGSIDE THE SQLITE ONE. The defect P1 closes predates T1525 and is a defect
of BOTH backends: on PostgreSQL a `40001` has always ended the tick without a replay. SQLite cannot
stand in for it - its conflict is a stale-snapshot busy, PostgreSQL's is a serialization failure
the database itself detects, and only this backend has the SERIALIZABLE semantics the application
actually runs on.

WHERE THE CONFLICT LANDS DEPENDS ON THE COMPETITOR, AND IT WAS MEASURED RATHER THAN ASSUMED
(corrected 2026-09-12; this docstring previously claimed a `40001` "surfaces at the OUTER COMMIT",
which is false as a general statement and was never checked). The mutation protocol is what caught
it: a mutation that makes a discarded attempt publish its observations was inert against the first
test below, because that test's conflict never reaches the branch which discards them.

Measured on this backend with TWO different competitor shapes - one that updates the SAME row the
tick writes, and one that shares no row with it at all and can only be refused through a read-write
cycle - the `40001` arrives in BOTH cases inside `create_payment_internal_staged`, and never at the
tick's outer `session.commit()`. The reason is that the money phase keeps issuing SQL after the
competitor commits (the payment's own prepare/commit flow runs inside the tick's transaction), and
PostgreSQL reports the failure at the first statement where it can detect the cycle, which is one
of those. There is no window in which the tick's transaction sits idle between the competitor's
commit and its own.

WHAT THAT COSTS, recorded rather than hidden. The policy's outer-commit branch - where an attempt
owns a COMPLETE observation buffer that must be destroyed without publishing - is not reachable
from a real conflict on either backend, and is covered instead by
`tests/unit/test_p015_p1_money_phase_replay.py`, which forces a commit-time failure deliberately. A
mutation that publishes from a discarded attempt is killed by that unit test and by nothing in this
module; that was found by running the mutation, not by reasoning about it.

WHY THE STAND IS BUILT THIS WAY, and each choice is load-bearing:

* Its own engine with `isolation_level="SERIALIZABLE"`. The shared test engine runs READ COMMITTED
  while the application runs SERIALIZABLE. Under READ COMMITTED a serialization failure does not
  arise BY DEFINITION, so a stand built on it would assert the replay never happens and would stay
  green even if the replay did not exist. `test_the_stand_is_serializable` holds that in place -
  this is the volume-010 lesson about a measuring instrument that is blind to the outcome it was
  built to see.
* A real pool, not NullPool and not the savepoint-wrapped `db_session` fixture. Under `db_session`
  an outer transaction survives every "commit", so the competitor's commit would never become
  visible to anyone and the conflict could not arise.
* The conflict is produced by a genuine concurrent update of a row both transactions write, with
  the competitor committing AFTER the tick's transaction has taken its snapshot. Nothing is
  injected into the driver and no exception is constructed.

The debt row is seeded before the tick so that both writers UPDATE it rather than INSERT it: two
concurrent inserts of the same business key would collide on
`uq_debts_debtor_creditor_equivalent`, which is a unique violation and deliberately NOT a
transient conflict - it would be testing a different predicate branch than the one intended.
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
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RetryablePaymentConflictException

from tests.debt_setup import debt_fixture_setup
from tests.debt_setup import purge_test_ledger

# MODE B (017 stage 2c, T1702): every commit of this module lands in a clone dropped after the test,
# not in the tier database it shares with mode-A tests - see `tests/tier_on_a_clone.py`.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

pytestmark = pytest.mark.postgres

_LIMIT = Decimal("1000.00")
#: Seeded before the tick so both writers UPDATE the same row instead of inserting it.
_OPENING = Decimal("100.00")
#: What the competitor adds, after the tick's snapshot. Large enough that the first attempt's
#: amount cannot fit in what is left - which is what makes the stale plan demonstrably wrong.
_COMPETITOR = Decimal("800.00")
_REMAINING = _LIMIT - _OPENING - _COMPETITOR
#: What the write-skew competitor puts on a row the tick reads but never writes.
_SKEW = Decimal("5.00")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def factory(committed_database):
    # ON THE CLONE, NOT THE TIER (017 stage 2c): this engine commits for real, and the tier database
    # is shared with mode-A tests in one process - see `tests/tier_on_a_clone.py`. The modules that
    # import this fixture import `tier_sessions_on_a_clone` too, so their `TestingSessionLocal`
    # observers read the same clone this engine writes.
    engine = create_async_engine(
        committed_database.url,
        pool_size=5,
        max_overflow=0,
        pool_timeout=20,
        isolation_level="SERIALIZABLE",
    )
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    try:
        yield session_factory
    finally:
        await engine.dispose()


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
        eq = Equivalent(code=f"P1PG{n}"[:16], precision=2, is_active=True)
        people = [
            Participant(
                pid=f"P1PG_{role}_{n}",
                display_name=role,
                public_key=f"pk_p1pg_{role}_{n}",
                type="person",
                status="active",
            )
            for role in ("S", "R", "X", "Y")
        ]
        s.add_all([eq, *people])
        await s.flush()
        sender, receiver, outsider_a, outsider_b = people
        s.add(
            TrustLine(
                from_participant_id=receiver.id,
                to_participant_id=sender.id,
                equivalent_id=eq.id,
                limit=_LIMIT,
                status="active",
            )
        )
        async with debt_fixture_setup(s, label="setup"):
            s.add(
                Debt(
                    debtor_id=sender.id,
                    creditor_id=receiver.id,
                    equivalent_id=eq.id,
                    amount=_OPENING,
                )
            )
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return _World(eq, sender, receiver, outsider_a, outsider_b)


async def _cleanup(session_factory, world: _World) -> None:
    ids = [world.sender.id, world.receiver.id, world.outsider_a.id, world.outsider_b.id]
    async with session_factory() as s:
        # The debts AND the journal rows that describe them, through the driver and BEFORE the
        # deletes below: `session.execute(delete(Debt))` is Core DML the write guard refuses
        # (that is `C2`), and `debt_operations.tx_id` RESTRICTs `transactions.tx_id`, so an
        # envelope still standing would block the transaction delete above it.
        await purge_test_ledger(s, equivalent_ids=[world.equivalent.id])
        tx_ids = list(
            (
                await s.execute(
                    select(Transaction.tx_id).where(Transaction.initiator_id.in_(ids))
                )
            ).scalars()
        )
        if tx_ids:
            await s.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
            await s.execute(delete(Transaction).where(Transaction.tx_id.in_(tx_ids)))
        await s.execute(delete(TrustLine).where(TrustLine.equivalent_id == world.equivalent.id))
        await s.execute(delete(Participant).where(Participant.id.in_(ids)))
        await s.execute(delete(Equivalent).where(Equivalent.id == world.equivalent.id))
        await s.commit()
    PaymentRouter.invalidate_cache(world.equivalent.code)


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
    run = RunRecord(run_id=run_id, scenario_id="p1-pg", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1
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


def _runner(
    run: RunRecord, scenario: dict[str, Any], sse: _Sse, *, actions_per_tick_max: int = 1
) -> RealRunner:
    return RealRunner(
        lock=threading.RLock(),
        get_run=lambda _rid: run,
        get_scenario_raw=lambda _sid: scenario,
        sse=sse,
        artifacts=_Artifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: True,
        actions_per_tick_max=actions_per_tick_max,
        clearing_every_n_ticks=10_000,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("tests.p015.p1.postgres"),
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
    plans: list[list[Any]] = []
    original = runner._plan_real_payments

    def _recording(run, scenario, *, debt_snapshot=None):
        planned = original(run, scenario, debt_snapshot=debt_snapshot)
        plans.append(list(planned))
        return planned

    monkeypatch.setattr(runner, "_plan_real_payments", _recording)
    return plans


def _competitor_after_snapshot(
    monkeypatch,
    runner: RealRunner,
    session_factory,
    world: _World,
    *,
    amount: Decimal,
    only_first: bool,
) -> list[int]:
    """A second SERIALIZABLE transaction that commits after the tick has taken its snapshot.

    The barrier is the tick's own debt snapshot read: when it returns, the tick's transaction has
    read (and already holds its owner lock and its snapshot), and its first write is still ahead.
    The competitor then READS the same equivalent's debts and UPDATES the row the tick is going to
    write, which is what makes PostgreSQL refuse to serialise the two - the tick's commit gets a
    genuine `40001`.
    """
    commits: list[int] = []
    original = runner._load_debt_snapshot_by_pid

    async def _load_then_let_someone_else_commit(session, participants, equivalents):
        snapshot = await original(session, participants, equivalents)
        if not (only_first and commits):
            commits.append(1)
            async with session_factory() as other:
                # A predicate read over the same region the tick read and is about to write.
                await other.execute(
                    select(Debt.id).where(Debt.equivalent_id == world.equivalent.id)
                )
                debt = (
                    await other.execute(
                        select(Debt).where(
                            Debt.equivalent_id == world.equivalent.id,
                            Debt.debtor_id == world.sender.id,
                            Debt.creditor_id == world.receiver.id,
                        )
                    )
                ).scalar_one()
                # Declared, because it IS a movement of money: the journal asks every writer to
                # name the operation that moved it, a competitor's included.
                raised = Decimal(str(debt.amount)) + amount
                async with debt_fixture_setup(other, label="the-competitor"):
                    debt.amount = raised
                await other.commit()
        return snapshot

    monkeypatch.setattr(
        runner, "_load_debt_snapshot_by_pid", _load_then_let_someone_else_commit
    )
    return commits


def _run_record_with_outsiders(world: _World, run_id: str) -> RunRecord:
    """The run's participant set widened so its debt snapshot READS the outsider pair.

    That read is what the write-skew below depends on: PostgreSQL can only find a cycle between
    two transactions if each has read what the other writes, and the tick only reads debts among
    `run._real_participants`. The outsiders have no trust line, so widening the set does not give
    the planner anything new to plan - the plan is unchanged and only the read predicate grows.
    """
    run = _run_record(world, run_id)
    run._real_participants = [
        (world.sender.id, world.sender.pid),
        (world.receiver.id, world.receiver.pid),
        (world.outsider_a.id, world.outsider_a.pid),
        (world.outsider_b.id, world.outsider_b.pid),
    ]
    return run


def _write_skew_competitor(
    monkeypatch,
    runner: RealRunner,
    session_factory,
    world: _World,
    *,
    only_first: bool,
) -> list[int]:
    """A competitor that builds a READ-WRITE CYCLE instead of colliding on a row.

    It reads the row the tick is going to write, and writes a row the tick only reads. Neither
    transaction touches the other's row, so nothing can fail early: PostgreSQL discovers the cycle
    only when the tick commits, which is precisely the outer-commit conflict this stand needs.
    """
    commits: list[int] = []
    original = runner._load_debt_snapshot_by_pid

    async def _load_then_skew(session, participants, equivalents):
        snapshot = await original(session, participants, equivalents)
        if not (only_first and commits):
            commits.append(1)
            async with session_factory() as other:
                # Reads the row the TICK will write.
                await other.execute(
                    select(Debt.amount).where(
                        Debt.equivalent_id == world.equivalent.id,
                        Debt.debtor_id == world.sender.id,
                        Debt.creditor_id == world.receiver.id,
                    )
                )
                # Writes a row the tick only READ and never writes.
                existing = (
                    await other.execute(
                        select(Debt).where(
                            Debt.equivalent_id == world.equivalent.id,
                            Debt.debtor_id == world.outsider_a.id,
                            Debt.creditor_id == world.outsider_b.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    async with debt_fixture_setup(other, label="setup"):
                        other.add(
                            Debt(
                                debtor_id=world.outsider_a.id,
                                creditor_id=world.outsider_b.id,
                                equivalent_id=world.equivalent.id,
                                amount=_SKEW,
                            )
                        )
                else:
                    existing.amount = Decimal(str(existing.amount)) + _SKEW
                await other.commit()
        return snapshot

    monkeypatch.setattr(runner, "_load_debt_snapshot_by_pid", _load_then_skew)
    return commits


def _record_staged_conflicts(monkeypatch) -> list[str]:
    """Conflicts raised by STAGING, so a commit-time conflict can be told from a flush-time one."""
    conflicts: list[str] = []
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except RetryablePaymentConflictException as exc:
            conflicts.append(type(exc).__name__)
            raise

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    return conflicts


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


async def _transactions(session_factory, world: _World) -> dict[str, str]:
    ids = [world.sender.id, world.receiver.id]
    async with session_factory() as fresh:
        rows = (
            await fresh.execute(
                select(Transaction.tx_id, Transaction.state).where(
                    Transaction.initiator_id.in_(ids)
                )
            )
        ).all()
    return {str(tx_id): str(state) for tx_id, state in rows}


async def _prepare_locks(session_factory, world: _World) -> int:
    async with session_factory() as fresh:
        tx_ids = list(
            (
                await fresh.execute(
                    select(Transaction.tx_id).where(
                        Transaction.initiator_id.in_([world.sender.id, world.receiver.id])
                    )
                )
            ).scalars()
        )
        if not tx_ids:
            return 0
        return len(
            (
                await fresh.execute(
                    select(PrepareLock.tx_id).where(PrepareLock.tx_id.in_(tx_ids))
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_the_stand_is_serializable(factory) -> None:
    """The stand must be able to SEE a serialization failure, or its verdicts mean nothing.

    Volume 010's lesson, applied here: a `40001` cannot occur under READ COMMITTED at all, so a
    stand that quietly ran there would assert "no replay happened" and stay green whether or not
    the replay exists.
    """
    async with factory() as session:
        level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
        await session.rollback()
    assert str(level).lower() == "serializable", level


@pytest.mark.asyncio
async def test_a_real_serialization_failure_replays_the_money_phase_and_commits_once(
    factory, monkeypatch, caplog
) -> None:
    """RED before P1: the `40001` ended the tick, was counted as an error, and lost the payment.

    This is the long-standing half of the defect - it has behaved this way since well before
    T1525, on the backend the application actually runs concurrency on.
    """
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p1-pg-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        commits = _competitor_after_snapshot(
            monkeypatch, runner, factory, world, amount=_COMPETITOR, only_first=True
        )

        with caplog.at_level(logging.WARNING, logger="tests.p015.p1.postgres"):
            await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        # ── The conflict was real, and it was the one this stand exists for ───────────
        replays = [
            record.getMessage()
            for record in caplog.records
            if "simulator.real.money_phase_replay " in record.getMessage()
        ]
        assert len(replays) == 1, replays
        assert "conflict=40001" in replays[0] or "conflict=RETRYABLE_PAYMENT_CONFLICT" in replays[0], (
            f"the replay did not run on a serialization failure: {replays}"
        )
        assert len(commits) == 1, commits

        # ── The plan was recomputed against a snapshot that includes the competitor ───
        assert len(plans) == 2, f"the money phase was not replanned: {plans}"
        first = Decimal(plans[0][0].amount)
        second = Decimal(plans[1][0].amount)
        assert first > _REMAINING, (
            f"stand is vacuous: the first plan ({first}) already fitted the capacity the "
            f"competitor left ({_REMAINING})"
        )
        assert second <= _REMAINING and second < first

        # ── The money, and the competitor's change, read on an independent session ────
        debts = await _debts(factory, world)
        assert debts == {
            (world.sender.pid, world.receiver.pid): _OPENING + _COMPETITOR + second
        }, (
            f"expected the opening {_OPENING} plus the competitor's {_COMPETITOR} plus the "
            f"REPLANNED {second}; got {debts}"
        )

        transactions = await _transactions(factory, world)
        assert list(transactions.values()) == ["COMMITTED"], transactions
        assert len(transactions) == 1, (
            f"the discarded attempt left a transaction behind: {transactions}"
        )
        assert await _prepare_locks(factory, world) == 0

        # ── Published once, counted once ──────────────────────────────────────────────
        assert sse.published("tx.updated") == 1
        assert sse.published("tx.failed") == 0
        assert run.committed_total == 1
        assert run.attempts_total == 1

        # ── A transient conflict is not an error ──────────────────────────────────────
        assert run.errors_total == 0
        assert run._real_consec_tick_failures == 0
        assert run.state == "running"
        assert run._real_money_conflicts_total == 1
        assert run._real_money_replays_total == 1
        assert run._real_money_committed_ticks_total == 1
    finally:
        await _cleanup(factory, world)


@pytest.mark.parametrize("shape", ["same-row", "write-skew"])
@pytest.mark.asyncio
async def test_a_genuine_40001_is_raised_by_the_staged_write_on_this_backend(
    factory, monkeypatch, shape: str
) -> None:
    """WHERE a real conflict lands, measured - because the answer decides what a stand can cover.

    Two competitor shapes are put against the same tick. "same-row" updates the very row the tick
    is about to write, so there is a direct collision. "write-skew" shares NO row with the tick: it
    reads the row the tick writes and writes a row the tick only reads, which PostgreSQL can refuse
    only by detecting a read-write cycle. The second shape is the textbook way to obtain a
    commit-time serialization failure.

    Both are nevertheless refused inside `create_payment_internal_staged`. This test pins that
    measurement so it cannot rot into an assumption: the docstring at the top of this module
    explains the mechanism, and this is the evidence for it.

    The consequence is a real limit on this module, stated plainly: the branch of the replay that
    handles an attempt with a COMPLETE observation buffer cannot be reached from a real conflict
    here, and lives in the unit stand instead.
    """
    world = await _seed(factory)
    try:
        sse = _Sse()
        if shape == "same-row":
            run = _run_record(world, f"p1-pg-where-{uuid.uuid4().hex[:8]}")
        else:
            run = _run_record_with_outsiders(world, f"p1-pg-where-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        staged_conflicts = _record_staged_conflicts(monkeypatch)
        if shape == "same-row":
            commits = _competitor_after_snapshot(
                monkeypatch, runner, factory, world, amount=_COMPETITOR, only_first=True
            )
        else:
            commits = _write_skew_competitor(
                monkeypatch, runner, factory, world, only_first=True
            )

        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        # The conflict was real, and it was raised while STAGING - not by the outer commit.
        assert len(commits) == 1, commits
        assert len(plans) == 2, f"the money phase was not replayed: {plans}"
        assert staged_conflicts == ["RetryablePaymentConflictException"], staged_conflicts

        # Whichever shape produced it, the replay leaves exactly one payment behind.
        transactions = await _transactions(factory, world)
        assert list(transactions.values()) == ["COMMITTED"], transactions
        assert len(transactions) == 1, (
            f"the discarded attempt left a transaction behind: {transactions}"
        )
        assert await _prepare_locks(factory, world) == 0
        assert sse.published("tx.updated") == 1
        assert run.committed_total == 1
        assert run.errors_total == 0
        assert run._real_money_replays_total == 1

        # The idempotency key is derived from the amount and `seq`, so a replan that CHANGES the
        # amount changes the key while a replan that does not keeps it. Either way the tick ends
        # with one transaction: re-planning regenerates load that was never accepted, and cannot
        # duplicate a payment.
        replanned_amount_changed = Decimal(plans[0][0].amount) != Decimal(plans[1][0].amount)
        assert replanned_amount_changed is (shape == "same-row"), (
            f"{shape}: expected the plan to change only when the competitor consumed capacity on "
            f"the planned edge; got {plans[0][0].amount} then {plans[1][0].amount}"
        )
    finally:
        await _cleanup(factory, world)


@pytest.mark.asyncio
async def test_the_staged_prefix_of_a_conflicted_attempt_is_rolled_back(
    factory, monkeypatch
) -> None:
    """Two payments are staged before the conflict; neither survives, and neither is duplicated.

    The discarded attempt wrote real rows into its transaction. If any of that prefix survived, the
    replay would apply its payments on top of writes that the tick reports as never having
    happened - the exact double-spend shape the boundary exists to prevent.
    """
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p1-pg-prefix-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse, actions_per_tick_max=2)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        _competitor_after_snapshot(
            monkeypatch, runner, factory, world, amount=_COMPETITOR, only_first=True
        )

        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        assert len(plans) == 2, plans
        staged_in_discarded_attempt = len(plans[0])
        assert staged_in_discarded_attempt >= 1, (
            "stand is vacuous: the discarded attempt planned nothing, so there was no prefix"
        )

        replanned_total = sum(Decimal(a.amount) for a in plans[1])
        debts = await _debts(factory, world)
        assert debts == {
            (world.sender.pid, world.receiver.pid): _OPENING + _COMPETITOR + replanned_total
        }, f"the discarded attempt's prefix survived: {debts}"

        transactions = await _transactions(factory, world)
        assert len(transactions) == len(plans[1]), (
            f"expected one transaction per REPLANNED payment and nothing from the discarded "
            f"attempt; got {transactions}"
        )
        assert set(transactions.values()) == {"COMMITTED"}, transactions
        assert await _prepare_locks(factory, world) == 0
        assert sse.published("tx.updated") == len(plans[1])
        assert run.committed_total == len(plans[1])
    finally:
        await _cleanup(factory, world)


@pytest.mark.asyncio
async def test_permanent_contention_exhausts_the_budget_without_spending_the_error_budget(
    factory, monkeypatch, caplog
) -> None:
    """Every attempt loses. The tick ends with no money, and the run is not called broken."""
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p1-pg-budget-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        _record_plans(monkeypatch, runner)
        commits = _competitor_after_snapshot(
            monkeypatch, runner, factory, world, amount=Decimal("1.00"), only_first=False
        )

        with caplog.at_level(logging.WARNING, logger="tests.p015.p1.postgres"):
            await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        assert len(commits) == 3, commits
        exhausted = [
            record.getMessage()
            for record in caplog.records
            if "simulator.real.money_phase_replay_exhausted" in record.getMessage()
        ]
        assert len(exhausted) == 1, exhausted

        # Only the competitor's three increments are in the database.
        debts = await _debts(factory, world)
        assert debts == {
            (world.sender.pid, world.receiver.pid): _OPENING + Decimal("3.00")
        }, debts
        assert await _transactions(factory, world) == {}
        assert sse.published("tx.updated") == 0

        assert run.errors_total == 0
        assert run._real_consec_tick_failures == 0
        assert run.state == "running"
        assert run.last_error["code"] == "REAL_MODE_MONEY_CONFLICT_UNRESOLVED"
        assert run._real_money_replay_exhausted_total == 1
        assert run._real_consec_money_no_progress_ticks == 1
    finally:
        await _cleanup(factory, world)


@pytest.mark.asyncio
async def test_a_tail_failure_after_the_money_commit_never_replays_money(
    factory, monkeypatch
) -> None:
    """The hard right edge, on the backend that has real concurrency.

    The money commits at its boundary; the tail then fails. The payment must stay durable, stay
    published exactly once, and must not be attempted a second time - a tail error may cost the
    tail and nothing else.
    """
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p1-pg-tail-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)

        async def _fail_in_the_tail(**_kwargs):
            raise RuntimeError("P1 stand: a failure in the tick's tail, after the money commit")

        monkeypatch.setattr(
            runner._real_tick_clearing_coordinator, "maybe_run_clearing", _fail_in_the_tail
        )

        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        # The money phase ran exactly once: a tail failure is not a reason to replay money.
        assert len(plans) == 1, plans
        amount = Decimal(plans[0][0].amount)
        assert run._real_money_replays_total == 0

        debts = await _debts(factory, world)
        assert debts == {(world.sender.pid, world.receiver.pid): _OPENING + amount}, debts
        transactions = await _transactions(factory, world)
        assert list(transactions.values()) == ["COMMITTED"], transactions

        assert sse.published("tx.updated") == 1
        assert run.committed_total == 1
        # The tail failure itself is an ordinary tick error, and it stops nothing about the money.
        assert run.last_error["code"] == "REAL_MODE_TICK_FAILED"
        assert run.errors_total == 1
    finally:
        await _cleanup(factory, world)
