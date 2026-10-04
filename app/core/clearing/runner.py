"""Programme 023: the ONE clearing runner (spec decisions 3, 4, 7, 9, 10). Built in slice (c), WIRED in slice (d).

Its product callers since the atomic switch of slice (d): `POST /clearing/auto` (`run_awaited_clearing`), the
Interact action `clearing-real` and the simulator's tick driver (`run_clearing_pass`, in the run's perimeter), and the
periodic loop in `app/main.py` (`run_periodic_clearing_pass`), started only when `CLEARING_PERIODIC_ENABLED` is set -
by a separate hub deployment, never by default (decision R1). No product caller executes clearing around it
(`tests/unit/test_p023_d_product_callers_go_through_the_runner.py`).

ONE PASS (`run_clearing_pass`), for one equivalent:

1. SNAPSHOT: one read transaction - the caller's `snapshot_guard` (the periodic isolation rule reads here, in the same
   read transaction), the equivalent id, `flow_planner.load_snapshot` (its edges are one statement) - then the transaction is rolled back and the
   session closed. The read transaction is released BEFORE any CPU work is handed over.
2. PLAN: `flow_planner.plan_clearing` runs in a separate PROCESS (a one-worker `spawn` pool by default), never on the
   event loop. Not a thread: measured 2026-09-28 on this stand, a thread running the planner starved the loop through
   the GIL - an in-process `GET /healthz` issued while a 0.71 s plan ran took 0.71 s, i.e. it completed only when the
   plan did (every loop iteration releases the GIL in its selector and waits a switch interval to get it back).
   Cancelling the await does NOT stop the worker, so a cancellation during planning reports `planner_abandoned`
   instead of pretending; the late result is discarded, it starts nothing.
3. EXECUTE: each planned cycle, in plan order, as one `ClearingOccurrence` (fresh plan UUID, ordinal, debt ids in cycle
   order, `c` in atoms) through `ClearingService.execute_occurrence` - the 019 boundary (since 027 stage 2: the
   cycle's line locks instead of the exclusive equivalent lock), retry owner, stop/hold `FOR SHARE`, authoritative consent and perimeter, commit resolver,
   `ClearingCommittedAfterCancellation`). Before EVERY cycle start: the lease (`lease.lost`) and the caller's budget
   (`deadline` on `deadline_clock`); after a loss or past the budget no new cycle starts, and the occurrence in flight is always
   finished by the boundary, never abandoned. The budget is checked BETWEEN occurrences: the first cycle of a pass
   always starts (a plan slower than the budget would otherwise start nothing, ever).
4. HANDOFF (decision 10): every durable occurrence is appended to the pass and given to `on_committed` IMMEDIATELY after
   its commit - with no `await` in between, so no later error or cancellation can overtake it - including one committed
   while the caller was being cancelled (`after_cancellation=True`).
5. RE-PLAN (decision 3): a skipped occurrence (`None`: stale plan, consent withdrawn) drops the unexecuted tail; the
   runner pauses and re-plans on a fresh snapshot. A fully executed plan is followed by one re-plan too: `complete` is
   claimed only when a plan on a fresh snapshot is EMPTY. Re-plans are bounded (`max_replans`).

OUTCOME: `ClearingPassResult` - `complete`, or `interrupted` with a reason (`lease_lost`, `budget_exhausted`,
`replan_limit`, `operational_limit` - the retry budget of one occurrence spent on conflicts, decision 4) and the
remaining work (unexecuted cycles of the current plan and their planned `V_edge`; `None` when no plan exists - not
measured, not zero). Cancellation propagates as `ClearingPassCancelled` (a `CancelledError`) carrying the result; any
other failure as `ClearingPassError` carrying the result and the original exception (`cause`): the operator stop, an
integrity hold, a perimeter violation, an unresolved commit and a planner error are their own outcomes, not skips.

WHAT IT DOES NOT DO: no plan table, no job API, no fallback, no cycle-length or search limit, no optimality under
concurrent mutation, no durable replay of a lost pass (after a restart: a new snapshot and a new plan).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import multiprocessing
from concurrent.futures import Executor, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import AbstractSet, Awaitable, Callable, Optional

from sqlalchemy import select

from app.config import settings
from app.core.clearing.flow_planner import PlannedCycle, load_snapshot, plan_clearing
from app.core.clearing.service import (
    ClearingOccurrence,
    ClearingService,
    RetryableClearingConflictException,
)
from app.db.models.equivalent import Equivalent
from app.db.models.simulator_storage import SimulatorRun
from app.utils.distributed_lock import RenewableLease, renewable_lease
from app.utils.exceptions import ConflictException, GeoException
from app.utils.validation import validate_equivalent_code

logger = logging.getLogger(__name__)

COMPLETE = "complete"
INTERRUPTED = "interrupted"

#: Re-plans after the first plan of a pass (each skip, and the confirming re-plan after a fully executed plan).
DEFAULT_MAX_REPLANS = 3
#: Pause before re-planning after a skip - the state moved under the plan; give the concurrent writer a moment.
DEFAULT_REPLAN_PAUSE_SECONDS = 0.5
#: The lease of one equivalent's pass. `renew interval + renew timeout + margin < TTL` (10 + 5 + 5 < 30), checked by
#: `RenewableLease`. Since slice (d) `/clearing/auto` and the periodic loop take THIS lease on this key, so a manual
#: and a periodic pass of one equivalent exclude each other where Redis is configured (without Redis nothing
#: distributed is claimed). Money is protected by the 019 boundary either way.
LEASE_TIMINGS = {
    "ttl_seconds": 30.0,
    "renew_interval_seconds": 10.0,
    "renew_timeout_seconds": 5.0,
    "safety_margin_seconds": 5.0,
}


def atoms_text(atoms: int) -> str:
    """Money on the wire: the exact decimal text of `atoms` at scale 8 (`Numeric(20, 8)`) - no float, no exponent."""

    return format(Decimal(int(atoms)).scaleb(-8), "f")


def lease_key(equivalent_code: str) -> str:
    return f"dlock:clearing:{equivalent_code}"


class InterruptReason(str, Enum):
    CANCELLED = "cancelled"
    LEASE_LOST = "lease_lost"
    BUDGET_EXHAUSTED = "budget_exhausted"
    REPLAN_LIMIT = "replan_limit"
    ERROR = "error"
    OPERATIONAL_LIMIT = "operational_limit"


@dataclass(frozen=True)
class CommittedEdge:
    """One edge a durable occurrence reduced: the debt and its direction `debtor -> creditor`."""

    debt_id: uuid.UUID
    debtor_id: uuid.UUID
    creditor_id: uuid.UUID


@dataclass(frozen=True)
class CommittedOccurrence:
    """What decision 10 hands to the caller for every durable occurrence."""

    occurrence_id: str
    plan_id: uuid.UUID
    ordinal: int
    amount_atoms: int
    edges: tuple[CommittedEdge, ...]
    after_cancellation: bool = False

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_atoms).scaleb(-8)

    @property
    def amount_text(self) -> str:
        return format(self.amount, "f")


@dataclass(frozen=True)
class ClearingPassResult:
    """The outcome of one pass. `V_edge = Σ |C_i|·c_i` and `V_cyc = Σ c_i` of the COMMITTED occurrences, in atoms."""

    equivalent: str
    status: str
    reason: Optional[InterruptReason]
    committed: tuple[CommittedOccurrence, ...]
    remaining_cycles: Optional[int]
    remaining_v_edge_atoms: Optional[int]
    plans: int
    distributed_exclusive: bool
    planner_abandoned: bool = False

    @property
    def v_edge_atoms(self) -> int:
        return sum(len(o.edges) * o.amount_atoms for o in self.committed)

    @property
    def v_cyc_atoms(self) -> int:
        return sum(o.amount_atoms for o in self.committed)


class ClearingPassCancelled(asyncio.CancelledError):
    """The pass was cancelled; `result` holds every occurrence handed off before (and at) the cancellation."""

    def __init__(self, result: ClearingPassResult):
        super().__init__("clearing pass cancelled")
        self.result = result


class ClearingPassError(Exception):
    """The pass stopped on an error; `result` holds the committed progress, `cause` the original exception."""

    def __init__(self, result: ClearingPassResult, cause: BaseException):
        super().__init__(f"clearing pass interrupted by {type(cause).__name__}")
        self.result = result
        self.cause = cause


class ClearingPeriodicRefused(Exception):
    """Decision 9, form (b): the periodic runner refuses where simulator use is DETECTED (a real-mode run row, or
    run persistence off). It does not prove the database holds no simulator data: `cleanup-simulator` deletes run
    rows but not simulator debts. Separate databases are the actual guarantee (spec decision 9)."""

    def __init__(self, reason: str):
        super().__init__(f"periodic clearing refused: {reason}")
        self.reason = reason


@dataclass
class _PassState:
    equivalent: str
    distributed_exclusive: bool
    committed: list = field(default_factory=list)
    remaining: Optional[tuple[PlannedCycle, ...]] = None
    plans: int = 0
    planner_abandoned: bool = False

    def hand_off(self, occurrence: ClearingOccurrence, cycle: PlannedCycle, amount: Decimal, *, after_cancellation: bool,
                 on_committed) -> CommittedOccurrence:
        # No validation may stand between a durable commit and its record: `amount` is `Numeric(20, 8)` money
        # (the declared `c`, or the durable amount of a verified replay), so it is a whole number of atoms.
        amount_atoms = int(Decimal(amount).scaleb(8))
        done = CommittedOccurrence(
            occurrence_id=occurrence.occurrence_id,
            plan_id=occurrence.plan_id,
            ordinal=occurrence.ordinal,
            amount_atoms=amount_atoms,
            edges=tuple(CommittedEdge(e.debt_id, e.debtor_id, e.creditor_id) for e in cycle.edges),
            after_cancellation=after_cancellation,
        )
        self.committed.append(done)
        if self.remaining:
            self.remaining = self.remaining[1:]
        if on_committed is not None:
            on_committed(done)
        return done

    def result(self, status: str, reason: Optional[InterruptReason]) -> ClearingPassResult:
        remaining = self.remaining
        return ClearingPassResult(
            equivalent=self.equivalent,
            status=status,
            reason=reason,
            committed=tuple(self.committed),
            remaining_cycles=None if remaining is None else len(remaining),
            remaining_v_edge_atoms=None if remaining is None else sum(len(c.edges) * c.atoms for c in remaining),
            plans=self.plans,
            distributed_exclusive=self.distributed_exclusive,
            planner_abandoned=self.planner_abandoned,
        )


_planner_executor: Optional[ProcessPoolExecutor] = None


def _default_planner_executor() -> ProcessPoolExecutor:
    """One planner process, created on first use and reused. `spawn` on every platform: forking a process that runs
    an event loop and threads is not safe, and the child needs only `flow_planner` (sqlalchemy, no settings)."""

    global _planner_executor
    if _planner_executor is None:
        _planner_executor = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _planner_executor


def _discard_broken_planner_executor(broken: Executor) -> None:
    """A dead planner process breaks its pool for good; the next pass gets a new one (this pass fails loudly)."""

    global _planner_executor
    if _planner_executor is broken:
        _planner_executor = None


async def _read_snapshot(session_factory, equivalent_code, allowed_participant_pids, snapshot_guard):
    async with session_factory() as session:
        if snapshot_guard is not None:
            await snapshot_guard(session)
        equivalent_id = (
            await session.execute(select(Equivalent.id).where(Equivalent.code == equivalent_code))
        ).scalar_one_or_none()
        edges = await load_snapshot(session, equivalent_code, allowed_participant_pids=allowed_participant_pids)
        await session.rollback()
    if equivalent_id is None:  # load_snapshot has already refused a missing equivalent; kept explicit
        raise GeoException(f"Equivalent {equivalent_code} not found")
    return equivalent_id, edges


async def _plan_off_the_loop(edges, executor: Optional[Executor], state: _PassState):
    pool = executor or _default_planner_executor()
    try:
        # Review P2-2: a worker that died while idle breaks the pool at SUBMISSION, not only at the result.
        worker = pool.submit(plan_clearing, edges)
    except BrokenProcessPool:
        _discard_broken_planner_executor(pool)
        raise
    try:
        return await asyncio.wrap_future(worker)
    except BrokenProcessPool:
        _discard_broken_planner_executor(pool)
        raise
    except asyncio.CancelledError:
        # A queued plan is cancelled; a RUNNING one cannot be - it finishes and its result is discarded.
        worker.cancel()
        state.planner_abandoned = not worker.done()
        raise


def _stop_reason(lease: Optional[RenewableLease], deadline: Optional[float], deadline_clock: Callable[[], float]):
    if lease is not None and lease.lost:
        return InterruptReason.LEASE_LOST
    if deadline is not None and deadline_clock() >= deadline:
        return InterruptReason.BUDGET_EXHAUSTED
    return None


async def _execute_one(session_factory, occurrence, cycle, allowed_participant_pids, state, on_committed):
    """Run one occurrence; hand it off before any further await. Returns False for a skip."""

    from app.core.clearing.service import ClearingCommittedAfterCancellation

    async with session_factory() as session:
        try:
            amount = await ClearingService(session).execute_occurrence(
                occurrence, allowed_participant_pids=allowed_participant_pids
            )
        except ClearingCommittedAfterCancellation as committed:
            try:
                state.hand_off(occurrence, cycle, committed.cleared_amount, after_cancellation=True, on_committed=on_committed)
            except Exception as handoff_error:  # noqa: BLE001 - the cancellation stays the outcome; the error is noted
                committed.add_note(f"committed-progress handoff failed: {type(handoff_error).__name__}: {handoff_error}")
                logger.exception("event=clearing.runner.handoff_failed after_cancellation=true")
            raise
        if amount is None:
            return False
        state.hand_off(occurrence, cycle, amount, after_cancellation=False, on_committed=on_committed)
        return True


async def run_clearing_pass(
    session_factory,
    equivalent_code: str,
    *,
    allowed_participant_pids: Optional[AbstractSet[str]] = None,
    on_committed: Optional[Callable[[CommittedOccurrence], None]] = None,
    lease: Optional[RenewableLease] = None,
    deadline: Optional[float] = None,
    deadline_clock: Optional[Callable[[], float]] = None,
    max_replans: int = DEFAULT_MAX_REPLANS,
    replan_pause_seconds: float = DEFAULT_REPLAN_PAUSE_SECONDS,
    executor: Optional[Executor] = None,
    snapshot_guard: Optional[Callable[[object], Awaitable[None]]] = None,
) -> ClearingPassResult:
    """One pass for one equivalent (module docstring). `session_factory` gives engine-bound `AsyncSession`s.

    `on_committed` is SYNCHRONOUS on purpose: it runs between the commit and the next `await`, so nothing can
    interrupt a handoff half-done. `deadline` is on `deadline_clock` (default: the event loop's clock).
    """

    now = deadline_clock or asyncio.get_running_loop().time
    state = _PassState(equivalent_code, distributed_exclusive=lease is not None and lease.distributed)
    replans = 0
    # Decision 10: the caller's deadline is checked BETWEEN occurrences - never before the first cycle of the pass.
    # Checked before the snapshot and the first cycle too, a plan slower than the caller's budget (a tick's 250 ms)
    # started nothing, and a graph whose planning always exceeds the budget never cleared (found 2026-09-28 by
    # slice (d); `test_a_budget_spent_by_planning_still_lets_the_first_cycle_run`). The lease is checked always.
    attempted = False
    try:
        while True:
            stop = _stop_reason(lease, deadline if attempted else None, now)
            if stop is not None:
                return _end(state, INTERRUPTED, stop)
            equivalent_id, edges = await _read_snapshot(
                session_factory, equivalent_code, allowed_participant_pids, snapshot_guard
            )
            plan = await _plan_off_the_loop(edges, executor, state)
            state.plans += 1
            state.remaining = tuple(plan.cycles)
            if not plan.cycles:
                return _end(state, COMPLETE, None)
            plan_id = uuid.uuid4()
            skipped = False
            for ordinal, cycle in enumerate(plan.cycles):
                stop = _stop_reason(lease, deadline if attempted else None, now)
                if stop is not None:
                    return _end(state, INTERRUPTED, stop)
                attempted = True
                occurrence = ClearingOccurrence(
                    plan_id=plan_id,
                    equivalent_id=equivalent_id,
                    ordinal=ordinal,
                    debt_ids=tuple(e.debt_id for e in cycle.edges),
                    amount_atoms=cycle.atoms,
                )
                if not await _execute_one(
                    session_factory, occurrence, cycle, allowed_participant_pids, state, on_committed
                ):
                    skipped = True
                    logger.info(
                        "event=clearing.runner.skip equivalent=%s ordinal=%s dropped=%s",
                        equivalent_code,
                        ordinal,
                        len(state.remaining or ()),
                    )
                    break
            if replans >= max_replans:
                return _end(state, INTERRUPTED, InterruptReason.REPLAN_LIMIT)
            replans += 1
            if skipped and replan_pause_seconds > 0:
                await asyncio.sleep(replan_pause_seconds)
    except asyncio.CancelledError as cancelled:
        result = _end(state, INTERRUPTED, InterruptReason.CANCELLED)
        raise ClearingPassCancelled(result) from cancelled
    except RetryableClearingConflictException:
        # Decision 4: an operational limit (the occurrence's retry budget) kept a cycle from executing.
        logger.warning("event=clearing.runner.operational_limit equivalent=%s", equivalent_code)
        return _end(state, INTERRUPTED, InterruptReason.OPERATIONAL_LIMIT)
    except Exception as error:
        result = _end(state, INTERRUPTED, InterruptReason.ERROR)
        logger.warning(
            "event=clearing.runner.error equivalent=%s error=%s committed=%s",
            equivalent_code,
            type(error).__name__,
            len(result.committed),
        )
        raise ClearingPassError(result, error) from error


def _end(state: _PassState, status: str, reason: Optional[InterruptReason]) -> ClearingPassResult:
    result = state.result(status, reason)
    logger.info(
        "event=clearing.runner.pass_end equivalent=%s status=%s reason=%s committed=%s plans=%s remaining=%s",
        result.equivalent,
        result.status,
        None if reason is None else reason.value,
        len(result.committed),
        result.plans,
        result.remaining_cycles,
    )
    return result


# ---------------------------------------------------------------------------------------------- awaited entry


async def run_awaited_clearing(
    session_factory,
    redis_client,
    equivalent_code: str,
    *,
    wait_timeout_seconds: float = 2.0,
    **pass_kwargs,
) -> ClearingPassResult:
    """The `/clearing/auto`-compatible entry (decision 7): awaited, under the equivalent's renewable lease.

    Refuses (409 `clearing_disabled`) when `CLEARING_ENABLED` is off. A lease held by another owner is
    `ConflictException` after `wait_timeout_seconds`. The entry of `POST /clearing/auto` since slice (d).
    """

    if not settings.CLEARING_ENABLED:
        # 028 `F-028-20` (decision `T1552`, 2026-09-14): a state of the server, not a bad request.
        raise ConflictException("Clearing is disabled", details={"reason": "clearing_disabled"})
    validate_equivalent_code(equivalent_code)
    async with renewable_lease(
        redis_client, lease_key(equivalent_code), wait_timeout_seconds=wait_timeout_seconds, **LEASE_TIMINGS
    ) as lease:
        return await run_clearing_pass(session_factory, equivalent_code, lease=lease, **pass_kwargs)


# ------------------------------------------------------------------------------------------------- periodic


async def check_periodic_isolation(session) -> None:
    """Decision 9, form (b): refuse where simulator use is detected - a real-mode run row, or run persistence
    off (see the spec's "Выбор формы"). Detection, not proof: a cleaned-up run leaves its debts behind.

    Run inside the snapshot's transaction, so the check and the snapshot see one state.
    """

    if not settings.SIMULATOR_DB_ENABLED:
        # This process's real runs would leave no `simulator_runs` row: the absence of rows proves nothing.
        raise ClearingPeriodicRefused("simulator_persistence_disabled")
    real_run = (
        await session.execute(select(SimulatorRun.run_id).where(SimulatorRun.mode == "real").limit(1))
    ).scalar_one_or_none()
    if real_run is not None:
        raise ClearingPeriodicRefused("simulator_real_runs_in_database")


async def run_periodic_clearing_pass(session_factory, redis_client) -> dict[str, ClearingPassResult]:
    """One periodic pass over the active equivalents, each under its lease (skipped when another owner holds it).

    `ClearingPeriodicRefused` is raised, never swallowed: the loop records it. One equivalent's error is logged and
    its interrupted result kept; the other equivalents still run.
    """

    if not settings.CLEARING_ENABLED:
        logger.info("event=clearing.periodic.disabled")
        return {}
    async with session_factory() as session:
        await check_periodic_isolation(session)
        codes = list(
            (
                await session.execute(
                    select(Equivalent.code).where(Equivalent.is_active.is_(True)).order_by(Equivalent.code)
                )
            ).scalars()
        )
        await session.rollback()
    results: dict[str, ClearingPassResult] = {}
    for code in codes:
        try:
            async with renewable_lease(
                redis_client, lease_key(code), wait_timeout_seconds=0.0, **LEASE_TIMINGS
            ) as lease:
                try:
                    results[code] = await run_clearing_pass(
                        session_factory, code, lease=lease, snapshot_guard=check_periodic_isolation
                    )
                except ClearingPassError as failed:
                    if isinstance(failed.cause, ClearingPeriodicRefused):
                        raise failed.cause from failed
                    results[code] = failed.result
                    logger.warning(
                        "event=clearing.periodic.equivalent_failed equivalent=%s error=%s",
                        code,
                        type(failed.cause).__name__,
                    )
        except ConflictException:
            logger.info("event=clearing.periodic.skipped_locked equivalent=%s", code)
    return results
