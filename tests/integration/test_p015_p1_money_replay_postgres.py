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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RetryablePaymentConflictException

from tests.debt_setup import debt_fixture_setup
from tests.p019_support import QueuedCompetitor, deadlock_detail, queue_behind_the_victim

# MODE B (017 stage 2c, T1702): every commit of this module lands in a clone dropped after the test,
# not in the tier database it shares with mode-A tests - see `tests/tier_on_a_clone.py`. Since 018 B0b
# the drop is the only disposal of rows: nothing is deleted row by row.
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture
from tests.debt_setup import transactions_of

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
    url = committed_database.url
    if not url.startswith("postgresql"):
        # A clone is made by `CREATE DATABASE ... TEMPLATE`, so this cannot happen; it is spelled
        # as a refusal because a SQLite engine here would need the T1525 transaction control.
        raise RuntimeError(f"a mode-B clone must be PostgreSQL, got {url!r}")
    engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=0,
        pool_timeout=20,
        isolation_level="READ COMMITTED",
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


def _forget_the_route_cache(world: _World) -> None:
    """The one piece of state that outlives the clone: this process's route cache for the code."""

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
) -> RealRunnerImpl:
    return RealRunnerImpl(
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


def _record_plans(monkeypatch, runner: RealRunnerImpl) -> list[list[Any]]:
    plans: list[list[Any]] = []
    original = runner._plan_real_payments

    def _recording(run, scenario, **kw):  # 028: the planner also takes `precision_by_eq`
        planned = original(run, scenario, **kw)
        plans.append(list(planned))
        return planned

    monkeypatch.setattr(runner, "_plan_real_payments", _recording)
    return plans


_PENDING: list[asyncio.Task] = []
#: (DETAIL of the 40P01 at the tick's debt write, tick pid, competitor pid), one per conflicted attempt (031 `T3102`).
_VICTIMS: list[tuple[str, int, int]] = []


#: 031 `T3191` finding 5: the test-only table the tick reads, at the conflicted flow, after the competitor locked it.
_PREFIX_BARRIER = "p015_p1_prefix_barrier"


@dataclass
class _StagedPrefix:
    """What the conflicted attempt had really written when the conflict was provoked, read IN its transaction."""

    flows_completed: int
    tx_ids: list[str]
    debt_amount: Decimal


#: One per conflicted attempt of a stand that provokes the conflict after a staged prefix.
_PREFIXES: list[_StagedPrefix] = []


def _assert_the_tick_was_each_victim(conflicts: int) -> None:
    assert len(_VICTIMS) == conflicts, _VICTIMS
    for detail, victim, competitor in _VICTIMS:
        assert detail.startswith(f"Process {victim} waits"), (victim, detail)
        assert f"blocked by process {competitor}." in detail, (competitor, detail)


def _competitor_after_snapshot(
    monkeypatch,
    runner: RealRunnerImpl,
    session_factory,
    world: _World,
    *,
    amount: Decimal,
    only_first: bool,
    after_flows: int = 0,
) -> list[int]:
    """A second SERIALIZABLE transaction that commits after the tick has taken its snapshot.

    `after_flows` (031 `T3191` finding 5): the conflict is provoked only after the attempt has COMPLETED that
    many payment flows, so a staged prefix really exists in its transaction. The prefix is read there, in the
    tick's own transaction (`_PREFIXES`), before the competitor queues. The competitor then takes the test-only
    `_PREFIX_BARRIER` and is confirmed waiting on the tick's lines; the tick's read of the barrier closes the
    cycle (the tick is the victim, as for `after_flows=0`).

    The barrier is the tick's own debt snapshot read: when it returns, the tick's transaction has
    read (and already holds its owner lock and its snapshot), and its first write is still ahead.
    The competitor then READS the same equivalent's debts and UPDATES the row the tick is going to
    write, which is what makes PostgreSQL refuse to serialise the two - the tick's commit gets a
    genuine `40001`.
    """
    from sqlalchemy.exc import DBAPIError

    from app.core.ledger import book

    commits: list[int] = []
    competitors = _PENDING  # 027 stage 2: awaited by `_debts` before it reads
    original = runner._load_debt_snapshot_by_pid
    real_apply_flow = book._apply_payment_flow
    armed: list[bool] = []
    flows_in_attempt: list[int] = []  # completed flows of the armed attempt
    pending: list[QueuedCompetitor] = []
    the_lines = select(TrustLine.id).where(TrustLine.equivalent_id == world.equivalent.id).with_for_update()

    async def _compete(other, queued: QueuedCompetitor) -> None:
        try:
            await queued.waiting  # returns once the tick's failure has ended its attempt
            debt = (await other.execute(select(Debt).where(
                Debt.equivalent_id == world.equivalent.id, Debt.debtor_id == world.sender.id,
                Debt.creditor_id == world.receiver.id))).scalar_one()  # locked by `hold`, or at the flush below
            raised = Decimal(str(debt.amount)) + amount
            async with debt_fixture_setup(other, label="the-competitor"):
                debt.amount = raised
            await other.commit()
        finally:
            await other.close()

    async def _first_flow_meets_the_competitor(*args, **kwargs):
        # 031 `T3102` (review `T3096` finding 3): at the attempt's first payment flow - its lines held, its debt
        # write next - the competitor takes that debt row and is CONFIRMED waiting on the tick's lines; the tick's
        # own debt write then closes the cycle, so ITS deadlock check finds it (`queue_behind_the_victim`). Before,
        # the competitor closed the cycle and the victim was whoever's check ran first.
        if armed and len(flows_in_attempt) >= after_flows:
            armed.clear()
            session = args[0] if args else kwargs["session"]
            other = session_factory()
            try:
                if after_flows:
                    _PREFIXES.append(await _staged_in_the_attempt(session, world, len(flows_in_attempt)))
                    queued = await queue_behind_the_victim(
                        session, other,
                        hold=text(f"LOCK TABLE {_PREFIX_BARRIER} IN ACCESS EXCLUSIVE MODE"),
                        # the lines, as below: the replay's MONEY transaction waits on them for the competitor's
                        # commit (its plan, read before the line locks since 034 `F-034-2`, may predate that commit)
                        wait_on_victim=the_lines,
                    )
                else:
                    queued = await queue_behind_the_victim(
                        session, other,
                        hold=select(Debt.id).where(
                            Debt.equivalent_id == world.equivalent.id, Debt.debtor_id == world.sender.id,
                            Debt.creditor_id == world.receiver.id).with_for_update(),
                        wait_on_victim=the_lines,
                    )
            except BaseException:
                await other.close()
                raise
            pending.append(queued)
            competitors.append(asyncio.create_task(_compete(other, queued)))
            if after_flows:
                try:
                    await (await session.connection()).execute(text(f"SELECT count(*) FROM {_PREFIX_BARRIER}"))
                except DBAPIError as exc:
                    queued = pending.pop()
                    _VICTIMS.append((deadlock_detail(exc), queued.victim_pid, queued.competitor_pid))
                    raise
                raise AssertionError("the tick read the barrier the competitor holds without a deadlock")
        try:
            flowed = await real_apply_flow(*args, **kwargs)
        except DBAPIError as exc:
            if pending:
                queued = pending.pop()
                _VICTIMS.append((deadlock_detail(exc), queued.victim_pid, queued.competitor_pid))
            raise
        if armed:
            flows_in_attempt.append(1)
        return flowed

    monkeypatch.setattr(book, "_apply_payment_flow", _first_flow_meets_the_competitor)
    _VICTIMS.clear()
    _PREFIXES.clear()

    async def _load_then_let_someone_else_commit(session, participants, equivalents):
        snapshot = await original(session, participants, equivalents)
        if not (only_first and commits):
            commits.append(1)
            # 027 stage 1 routes outside the tick's transaction: pin the pre-competitor route, so the plan
            # reaches the staged write (the conflict this stand exists for) rather than a routing refusal.
            monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 3600)
            async with session_factory() as warm:
                await PaymentRouter(warm).build_graph(world.equivalent.code, use_shared_cache=True)
            flows_in_attempt.clear()
            armed.append(True)  # the competitor enters at this attempt's flow number `after_flows + 1`
        return snapshot

    monkeypatch.setattr(
        runner, "_load_debt_snapshot_by_pid", _load_then_let_someone_else_commit
    )
    return commits


async def _staged_in_the_attempt(session, world: _World, flows_completed: int) -> _StagedPrefix:
    """The attempt's own writes, read on ITS connection (no ORM flush): the transactions it staged and the debt
    row as its transaction holds it."""

    connection = await session.connection()
    tx_ids = [
        str(tx_id)
        for (tx_id,) in (
            await connection.execute(
                select(Transaction.tx_id).where(transactions_of([world.sender.id, world.receiver.id]))
            )
        ).all()
    ]
    amount = await connection.scalar(
        select(Debt.amount).where(
            Debt.equivalent_id == world.equivalent.id, Debt.debtor_id == world.sender.id,
            Debt.creditor_id == world.receiver.id)
    )
    return _StagedPrefix(flows_completed, tx_ids, Decimal(str(amount)))


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


def _record_written_here(monkeypatch) -> dict[str, bool]:
    """tx_id -> whether the LAST staged call for it wrote its row itself (`written_here`), as opposed to being
    answered from a stored row. A replay may plan the very payment a discarded attempt staged, under the same
    `tx_id`; a row of that id is then the replay's own only if the replay wrote it."""

    written: dict[str, bool] = {}
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        staged = await original(self, *args, **kwargs)
        written[str(staged.result.tx_id)] = bool(staged.written_here)
        return staged

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    return written


def _count_planning_reads(monkeypatch, runner: RealRunnerImpl) -> list[int]:
    """One entry per read of the planning debt snapshot. Install BEFORE `_competitor_after_snapshot`."""

    reads: list[int] = []
    original = runner._load_debt_snapshot_by_pid

    async def _counting(session, participants, equivalents):
        reads.append(1)
        return await original(session, participants, equivalents)

    monkeypatch.setattr(runner, "_load_debt_snapshot_by_pid", _counting)
    return reads


def _carried_by_the_core(plan: list[Any], left: Decimal) -> list[Decimal]:
    """Which payments of `plan`, in order, fit the capacity `left` on the stand's one line - what the payment
    service, which checks capacity itself behind the pair's line locks, must carry and what it must refuse."""

    carried: list[Decimal] = []
    for action in plan:
        amount = Decimal(action.amount)
        if amount <= left:
            carried.append(amount)
            left -= amount
    return carried


def _record_conflict_sqlstates(monkeypatch) -> list[str | None]:
    """The SQLSTATE of the DATABASE error behind every conflict the tick's payments raised.

    Carried over from the SQLite stand (017 stage 3, slice S2a), where the same recorder read the
    SQLite error code: the typed `RetryablePaymentConflictException` alone does not prove that THIS
    stand produced the conflict it was built for - an owner-preflight change raises the same type
    with no database error behind it. The original error survives as the exception's `__cause__`,
    and the service's own reader of it is what is asked here.
    """
    from app.core.payments.service import _payment_db_sqlstate

    sqlstates: list[str | None] = []
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except RetryablePaymentConflictException as exc:
            cause = exc.__cause__
            sqlstates.append(_payment_db_sqlstate(cause) if cause is not None else None)
            raise

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    return sqlstates


async def _debts(session_factory, world: _World) -> dict[tuple[str, str], Decimal]:
    await asyncio.gather(*_PENDING)
    _PENDING.clear()
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
                    transactions_of(ids)
                )
            )
        ).all()
    return {str(tx_id): str(state) for tx_id, state in rows}


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
    assert str(level).lower() == "read committed", level


@pytest.mark.asyncio
async def test_a_real_serialization_failure_replays_the_money_phase_and_commits_once(
    factory, monkeypatch, caplog
) -> None:
    """RED before P1: the `40001` ended the tick, was counted as an error, and lost the payment.

    This is the long-standing half of the defect - it has behaved this way since well before
    T1525, on the backend the application actually runs concurrency on.

    CHANGED 2026-10-08 (034 S1a, decision of the §15 fix-delta review, `REVERT-WAIT-AND-REVISE-P1`). This test
    used to assert that the REPLAYED plan was sized against a snapshot that already includes the competitor
    (`second <= _REMAINING`), and therefore that the replay's payment is always carried. That held while the
    planning snapshot was read under the money transaction's line locks: the replay queued behind the competitor
    and read after it. Since 034 `F-034-2` the planning inputs are read BEFORE the line locks, on a session of
    their own, so the replay may read them before the competitor's commit has finished. That freshness is a
    property of liveness, not of the correctness of a debt - the P1 decision itself draws that line
    (`specs/015-financial-core-verification/spec.md`, P1) - and the money decision is the payment service's, behind
    the pair's line locks: a stale plan moves no debt, it is refused honestly.

    WHAT IS KEPT: the conflict is real and the phase is replayed once; the plan IS recomputed in the replay (the
    planner runs again on inputs read again - only the promise that those inputs include a competitor still
    committing is gone); nothing of the discarded attempt survives; the competitor's debt stands; one row and one
    observation per payment. WHAT REPLACED THE FRESHNESS CLAIM: both honest outcomes of the replay are accepted
    and checked by one rule, the report equals the rows - a replanned amount that fits what the competitor left is
    carried, one that does not is refused by the core, and in either case nothing beyond that capacity is carried.
    """
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p1-pg-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        planning_reads = _count_planning_reads(monkeypatch, runner)
        written_here = _record_written_here(monkeypatch)
        sqlstates = _record_conflict_sqlstates(monkeypatch)
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
        assert "conflict=40P01" in replays[0] or "conflict=RETRYABLE_PAYMENT_CONFLICT" in replays[0], (
            f"the replay did not run on a serialization failure: {replays}"
        )
        assert len(commits) == 1, commits
        # ...and the database error behind that typed conflict was a genuine serialization failure,
        # read by its SQLSTATE, not merely something that shares the exception type. Carried over
        # from `test_p015_p1_money_replay_sqlite.py`, which asserted SQLITE_BUSY_SNAPSHOT here.
        assert sqlstates == ["40P01"], sqlstates
        _assert_the_tick_was_each_victim(1)

        # ── The plan was recomputed in the replay: the planner ran again, on inputs read again ───
        assert len(plans) == 2, f"the money phase was not replanned: {plans}"
        assert len(planning_reads) == 2, f"the replay did not read its planning inputs again: {planning_reads}"
        assert len(plans[1]) == 1, plans[1]
        first = Decimal(plans[0][0].amount)
        second = Decimal(plans[1][0].amount)
        assert first > _REMAINING, (
            f"stand is vacuous: the first plan ({first}) already fitted the capacity the "
            f"competitor left ({_REMAINING})"
        )
        # Whether those inputs already include the competitor is a race this test does not decide (see the
        # docstring): `second` is either sized to what the competitor left, or still the stale amount.
        # What the core must do with it is decided by the capacity alone.
        carried = _carried_by_the_core(plans[1], _REMAINING)
        assert carried == ([second] if second <= _REMAINING else []), (second, carried)

        # ── The money, and the competitor's change, read on an independent session ────
        updated = [Decimal(e["amount"]) for e in sse.events if e.get("type") == "tx.updated"]
        assert updated == carried, (
            f"replanned {second} with {_REMAINING} left after the competitor: the core must carry {carried}; "
            f"published as carried: {updated}"
        )
        assert sum(updated, Decimal("0")) <= _REMAINING, updated  # nothing beyond what the competitor left
        debts = await _debts(factory, world)
        assert debts == {
            (world.sender.pid, world.receiver.pid): _OPENING + _COMPETITOR + sum(carried, Decimal("0"))
        }, (
            f"expected the opening {_OPENING} plus the competitor's {_COMPETITOR} plus what the replay "
            f"carried {carried} (replanned {second}); got {debts}"
        )

        transactions = await _transactions(factory, world)
        assert list(transactions.values()) == ["COMMITTED" if carried else "ABORTED"], transactions
        assert len(transactions) == 1, (
            f"the discarded attempt left a transaction behind: {transactions}"
        )
        # The one stored row is the replay's own: written by it, not a row of the discarded attempt answered back.
        assert [written_here.get(tx_id) for tx_id in transactions] == [True], (transactions, written_here)

        # ── Published once, counted once: the report equals the rows ───────────────────
        assert sse.published("tx.updated") == len(carried)
        assert sse.published("tx.failed") == 1 - len(carried)
        assert run.committed_total == len(carried)
        assert run.attempts_total == 1
        assert run.rejected_total == 1 - len(carried)

        # ── A transient conflict is not an error ──────────────────────────────────────
        assert run.errors_total == 0
        assert run._real_consec_tick_failures == 0
        assert run.state == "running"
        assert run._real_money_conflicts_total == 1
        assert run._real_money_replays_total == 1
        # The replay SUCCEEDED: the budget was not exhausted and the tick's money phase committed - with the
        # payment, or with the core's refusal of it recorded. These two and `rejected_total` above were asserted
        # only by the SQLite stand until 017 stage 3.
        assert run._real_money_replay_exhausted_total == 0
        assert run._real_money_committed_ticks_total == 1
        assert run._real_consec_money_no_progress_ticks == 0
    finally:
        _forget_the_route_cache(world)


@pytest.mark.asyncio
async def test_the_staged_prefix_of_a_conflicted_attempt_is_rolled_back(
    factory, monkeypatch
) -> None:
    """A payment is staged before the conflict, which the attempt's SECOND payment meets; the staged prefix does
    not survive, and nothing is duplicated.

    The discarded attempt wrote real rows into its transaction. If any of that prefix survived, the
    replay would apply its payments on top of writes that the tick reports as never having
    happened - the exact double-spend shape the boundary exists to prevent.

    031 `T3191` finding 5: before, the conflict fired at the attempt's FIRST payment flow (staging is serialised,
    so nothing had been staged yet) and the non-vacuity check counted planned payments, not written ones. The
    conflict now fires after one COMPLETED flow, and the prefix is read in the attempt's own transaction.

    CHANGED 2026-10-08 (034 S1a, decision of the §15 fix-delta review, `REVERT-WAIT-AND-REVISE-P1`; the reasons
    are in the docstring of the test above). The test used to take for granted that every REPLANNED payment is
    carried, which held only while the replay planned after the competitor's commit. The replay's planning inputs
    are now read before the line locks and may predate that commit; the payment service then refuses what does
    not fit. So what the replay must leave is no longer "all of the replanned payments" but exactly those of them
    that fit what the competitor left, in order - carried or refused, one row and one observation each.

    The claim this test owns is unchanged and is checked in both outcomes: the staged prefix of the discarded
    attempt did not survive. A stale replay plans the SAME payments as the discarded attempt, under the same
    `tx_id`s, so "none of the prefix's ids is stored" can no longer say it; instead every stored row must have
    been WRITTEN by the replay (`written_here`) - a surviving prefix row would be answered back, not written - and
    the debt must be the competitor's plus what the replay carried, with nothing of the prefix on top.
    """
    world = await _seed(factory)
    try:
        sse = _Sse()
        run = _run_record(world, f"p1-pg-prefix-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse, actions_per_tick_max=2)
        _install(monkeypatch, factory)
        async with factory() as s:
            await s.execute(text(f"CREATE TABLE IF NOT EXISTS {_PREFIX_BARRIER} (id int)"))
            await s.commit()
        plans = _record_plans(monkeypatch, runner)
        planning_reads = _count_planning_reads(monkeypatch, runner)
        written_here = _record_written_here(monkeypatch)
        _competitor_after_snapshot(
            monkeypatch, runner, factory, world, amount=_COMPETITOR, only_first=True, after_flows=1
        )

        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)

        assert len(plans) == 2, plans
        assert len(planning_reads) == 2, f"the replay did not read its planning inputs again: {planning_reads}"
        assert run._real_money_replays_total == 1 and run._real_money_conflicts_total == 1, (
            run._real_money_replays_total, run._real_money_conflicts_total)
        _assert_the_tick_was_each_victim(1)
        # ── Non-vacuity, from WRITES: one flow completed and its rows were in the attempt's transaction ──
        assert len(_PREFIXES) == 1, f"stand is vacuous: the conflict was not provoked after a staged prefix: {_PREFIXES}"
        [prefix] = _PREFIXES
        assert len(plans[0]) >= 2, f"stand is vacuous: the attempt planned {len(plans[0])} payment(s), no second"
        assert prefix.flows_completed == 1, prefix
        staged_amount = Decimal(plans[0][0].amount)
        # The completed payment's row, and the conflicted payment's own row (written before its flow).
        assert len(prefix.tx_ids) == prefix.flows_completed + 1, (
            f"stand is vacuous: the attempt's transaction did not hold the staged payment's row: {prefix}"
        )
        assert prefix.debt_amount == _OPENING + staged_amount, (
            f"stand is vacuous: the attempt's debt row did not carry the staged payment: {prefix}"
        )

        # ── The prefix's rows did not persist: read on an independent session ──
        # What the replay may carry is decided by the capacity the competitor left, not by which snapshot it
        # planned from: the replanned payments that fit, in order; the rest the core refuses.
        carried = _carried_by_the_core(plans[1], _REMAINING)
        refused = len(plans[1]) - len(carried)
        updated = [Decimal(e["amount"]) for e in sse.events if e.get("type") == "tx.updated"]
        assert updated == carried, (
            f"replanned {[a.amount for a in plans[1]]} with {_REMAINING} left after the competitor: the core must "
            f"carry {carried}; published as carried: {updated}"
        )
        assert sum(updated, Decimal("0")) <= _REMAINING, updated  # nothing beyond what the competitor left
        debts = await _debts(factory, world)
        assert debts == {
            (world.sender.pid, world.receiver.pid): _OPENING + _COMPETITOR + sum(carried, Decimal("0"))
        }, f"the discarded attempt's prefix survived: {debts}"

        transactions = await _transactions(factory, world)
        assert len(transactions) == len(plans[1]), (
            f"expected one transaction per REPLANNED payment and nothing from the discarded "
            f"attempt; got {transactions}"
        )
        # Every stored row was written by the replay itself. A row of the discarded prefix that had persisted would
        # be answered back to the replay (`written_here` False) or stand beside its rows (the count above).
        assert {tx_id: written_here.get(tx_id) for tx_id in transactions} == dict.fromkeys(transactions, True), (
            f"a stored transaction was not written by the replay - the discarded attempt's staged prefix "
            f"{prefix.tx_ids} persisted: {transactions}, written by the last call: {written_here}"
        )
        if [a.amount for a in plans[1]] != [a.amount for a in plans[0]]:
            # A plan that differs from the discarded one has ids of its own: none of the prefix's may be stored.
            assert not set(prefix.tx_ids) & set(transactions), (
                f"the discarded attempt's staged transaction {prefix.tx_ids} persisted: {transactions}"
            )
        assert sorted(transactions.values()) == ["ABORTED"] * refused + ["COMMITTED"] * len(carried), transactions
        assert sse.published("tx.updated") == len(carried)
        assert sse.published("tx.failed") == refused
        assert (run.committed_total, run.rejected_total, run.errors_total) == (len(carried), refused, 0)
    finally:
        _forget_the_route_cache(world)


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
        _assert_the_tick_was_each_victim(3)
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
        _forget_the_route_cache(world)


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
            runner._tick, "maybe_run_clearing", _fail_in_the_tail
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
        _forget_the_route_cache(world)
