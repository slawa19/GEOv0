"""The real-mode tick of the simulator: one module (programme 021, stage 4, `T2105`).

WHAT IT REPLACED. Six `real_tick_*` modules - an orchestrator, four coordinators (payments, clearing, trust drift,
metrics) and a persistence tail - that reached back into the runner through a 19-attribute protocol
`_RealRunnerPort`, and carried three copies of the same commit-and-resolve helper. Their behaviour is here,
unchanged except for one recorded fix (the clearing volume, below).

THE TICK, IN ORDER, WITH ITS DURABILITY BOUNDARIES (spec 021, "Решения" item 8; F-021-9):

1. Before the boundary, on a session of its own: seeding (rolled back by its owner on failure), participants and
   equivalents, trust drift initialisation and the due scenario events (each inject its own unit of work).
2. THE MONEY PHASE - `run_money_phase_with_bounded_replay` (`money_replay.py`, the 019 retry owner): owner locks,
   snapshot, planning and staged payments on a fresh session, committed at its own boundary and replayed as a whole
   on a transient conflict. Called here and nowhere else.
3. Clearing - on the static cadence only, with the tick's hard timeout; the tail's transaction is committed first
   so clearing (its own sessions, one pass of the common runner `run_clearing_pass` per equivalent - 023 (d); the
   driver `RealClearingEngine` in between was removed by 021 `T2109`) does not queue behind it.
4. Trust decay - its OWN commit, before its edge patch is published; a failed decay is rolled back HERE by the
   owner of its transaction (`T2100` P2-4), so nothing of it reaches a later commit.
5. Metrics and bottlenecks - their own, later commit; a failure there does not undo the decay.
6. No post-tick audit: removed by 028 `F-028-35` (owner В-11) - every payment passed `check_payment_delta`, and the
   journal reconciliation (015) is the detector.

A failure anywhere after the money boundary costs the tail, never the money: the observation buffer has already
resolved, so the tail's rollback cannot un-publish a committed payment and nothing in it can replay money.

THE RUNNER. `RealTick` is the tick of `RealRunnerImpl` and reads the runner's collaborators and limits at CALL
time, as the orchestrator did, so a test or an operator that rebinds one on the runner is seen by the next tick.
The static intervals and budgets are captured once at construction, as the coordinators captured them.

THE ONE BEHAVIOUR CHANGE (programme 021 stage 4; `specs/BACKLOG.md`, class 2 of the 023(d) review, item 1). The
tick's clearing volume per equivalent is the sum of the occurrences the clearing runner handed over as COMMITTED
(`on_committed`), not the value the clearing task returns on success. A hard timeout after a commit used to report
zero to the `clearing_volume` metric while the database and `clearing.done` kept the committed cycle.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
import weakref
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy import func, select

import app.core.clearing.runner as clearing_runner
import app.core.simulator.storage as simulator_storage
import app.db.session as db_session
from app.config import settings
from app.core.clearing.service import OCCURRENCE_AMOUNT_NOT_IN_STEP
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.core.simulator.commit_resolution import (
    resolve_commit_under_cancellation,
    resolve_rollback_under_cancellation,
)
from app.core.simulator.models import RunRecord
from app.core.simulator.money_replay import (
    money_conflict_name,
    run_money_phase_with_bounded_replay,
)
from app.core.simulator.net_balance_utils import to_money_str
from app.core.simulator.real_payments_executor import DeferredRealPaymentEffects, RealPaymentsResult
from app.core.simulator.real_scenario_seeder import SIMULATOR_PID_TAKEN, SimulatorPidTakenError
from app.core.simulator.run_perimeter import run_perimeter_pids
from app.core.simulator.scenario_equivalent import (
    effective_equivalent,
    scenario_default_equivalent,
)
from app.core.simulator.sse_broadcast import SseEventEmitter, publish_closed_trustlines
from app.core.simulator.trust_drift_engine import commit_trust_drift
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import ConflictException, GeoException

if TYPE_CHECKING:
    from app.core.simulator.real_runner_impl import RealRunnerImpl


@dataclass(frozen=True)
class TickPaymentsPhase:
    """What one attempt of the money phase produced (was `RealTickPaymentsPhaseResult`)."""

    planned: list[Any]
    per_eq_metric_values: dict[str, dict[str, Any]]

    committed: int
    rejected: int
    errors: int
    timeouts: int

    per_eq: dict[str, Any]
    per_eq_route: dict[str, Any]
    per_eq_edge_stats: dict[str, Any]

    stall_ticks: int

    deferred_effects: DeferredRealPaymentEffects | None = None
    # Programme 015 / P1: the identifiers of the payments this attempt staged, for resolving an unknown commit
    # outcome. See `app/core/simulator/money_replay.py`.
    staged_tx_ids: frozenset[str] = frozenset()
    # 036 B1: the scripted `payment` events this attempt carried, and what to do once the phase is DURABLE: spend them.
    # An event is marked fired only when its payment is durable (committed, or landed after an unknown commit): a tick
    # that fails or is replayed first leaves it pending, and the event's own key keeps a repeat from paying twice.
    scripted_event_indexes: frozenset[int] = frozenset()
    # ... and the outcome of every scripted payment of the phase by event index (written when the phase is durable).
    scripted_progress: dict[int, dict[str, Any]] = field(default_factory=dict)
    on_durable: Callable[[frozenset[int], dict[int, dict[str, Any]]], None] | None = None

    def discard_observations(self) -> bool:
        """Destroy this attempt's observations without publishing them (a superseded attempt)."""
        if self.deferred_effects is None:
            return False
        return self.deferred_effects.discard()

    async def build_post_commit_patches(self, open_session: Callable[[], Any]) -> None:
        """034 `F-034-2`: the visual patches of the committed payments, read after the commit on `open_session()`.
        Awaited by the owner of the money phase before `apply_deferred_effects` publishes. Never raises."""
        if self.deferred_effects is not None:
            await self.deferred_effects.build_post_commit_patches(open_session)

    def apply_deferred_effects(self) -> bool:
        """The money phase is durable: spend the scripted events it carried, publish the observations once.

        Both callers (the commit's confirmation and the landing after an unknown commit) come through here, and a
        second call (the tail's `_commit_and_resolve`) finds the set already spent - adding to it again is a no-op."""
        if self.on_durable is not None and (self.scripted_event_indexes or self.scripted_progress):
            self.on_durable(self.scripted_event_indexes, self.scripted_progress)
        if self.deferred_effects is None:
            return False
        return self.deferred_effects.apply_after_commit()

    def apply_rollback_observations(self) -> bool:
        if self.deferred_effects is None:
            return False
        return self.deferred_effects.apply_after_rollback()

    def apply_unknown_transaction_observations(self) -> bool:
        if self.deferred_effects is None:
            return False
        return self.deferred_effects.apply_after_unknown_transaction_outcome()


class RealTick:
    def __init__(self, runner: "RealRunnerImpl") -> None:
        self._runner = runner
        # Captured once, exactly as the coordinators captured them at the runner's construction.
        self._clearing_every_n_ticks = int(runner._clearing_every_n_ticks)
        self._real_clearing_time_budget_ms = int(runner._real_clearing_time_budget_ms)
        self._real_db_metrics_every_n_ticks = int(runner._real_db_metrics_every_n_ticks)
        self._real_db_bottlenecks_every_n_ticks = int(runner._real_db_bottlenecks_every_n_ticks)
        self._real_last_tick_write_every_ms = int(runner._real_last_tick_write_every_ms)
        self._real_artifacts_sync_every_ms = int(runner._real_artifacts_sync_every_ms)
        # The committed-volume accumulator of each running clearing task, so a tick that finds the previous
        # tick's task still running reports what THAT task has committed.
        self._clearing_progress: "weakref.WeakKeyDictionary[asyncio.Task, dict[str, Decimal]]" = (
            weakref.WeakKeyDictionary()
        )

    # ── callbacks and commits: the one copy ───────────────────────────────────────────────────────────

    def _apply_callback(self, callback: Callable[[], Any] | None, *, kind: str) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:
            self._runner._logger.warning(
                "simulator.real.payment_%s_callback_failed",
                kind,
                exc_info=True,
            )

    async def _commit_and_resolve(
        self,
        session: Any,
        *,
        on_commit: Callable[[], Any] | None,
        on_rollback: Callable[[], Any] | None,
        on_unknown: Callable[[], Any] | None,
    ) -> None:
        await resolve_commit_under_cancellation(
            commit=session.commit,
            rollback=session.rollback,
            on_commit=lambda: self._apply_callback(on_commit, kind="post_commit"),
            on_rollback=lambda: self._apply_callback(on_rollback, kind="rollback"),
            on_unknown=lambda: self._apply_callback(on_unknown, kind="unknown"),
            logger=self._runner._logger,
        )

    @staticmethod
    def _phase_callbacks(payments_result: Any | None) -> dict[str, Callable[[], Any] | None]:
        return {
            "on_commit": getattr(payments_result, "apply_deferred_effects", None),
            "on_rollback": getattr(payments_result, "apply_rollback_observations", None),
            "on_unknown": getattr(payments_result, "apply_unknown_transaction_observations", None),
        }

    # ── run lifecycle ─────────────────────────────────────────────────────────────────────────────────

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None:
        rr = self._runner
        run = rr._get_run(run_id)
        task = None
        clearing_task = None
        with rr._lock:
            if run.state in ("stopped", "stopping", "error"):
                return

            run.state = "error"
            run.stopped_at = rr._utc_now()
            run.current_phase = None
            run.queue_depth = 0
            run._real_in_flight = 0
            run.errors_total += 1
            run._error_timestamps.append(time.time())
            cutoff = time.time() - 60.0
            while run._error_timestamps and run._error_timestamps[0] < cutoff:
                run._error_timestamps.popleft()
            run.last_error = {
                "code": code,
                "message": message,
                "at": rr._utc_now().isoformat(),
            }
            task = run._heartbeat_task
            clearing_task = run._real_clearing_task
            run._real_clearing_task = None

        rr._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)

        # Best-effort final flush for throttled tick metrics/bottlenecks.
        try:
            await self.flush_pending_storage(run_id)
        except Exception:
            rr._logger.warning(
                "simulator.real.fail_run.flush_pending_storage_failed run_id=%s",
                str(run_id),
                exc_info=True,
            )
        if task is not None and task is not asyncio.current_task():
            task.cancel()

        if clearing_task is not None:
            try:
                clearing_task.cancel()
            except Exception:
                pass
            # Best-effort: await cancellation so we don't leave a background task running after transitioning the
            # run into error.
            try:
                await asyncio.wait_for(clearing_task, timeout=1.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

    # ── the tick ──────────────────────────────────────────────────────────────────────────────────────

    async def tick(self, run_id: str) -> None:
        rr = self._runner

        run: RunRecord = rr._get_run(run_id)
        scenario = getattr(run, "_scenario_raw", None) or rr._get_scenario_raw(run.scenario_id)

        tick_t0 = time.monotonic()
        # NOTE: keep this at DEBUG to avoid log spam; WARNING logs sit around potentially blocking stages.
        rr._logger.debug(
            "simulator.real.tick_start run_id=%s tick=%s sim_time_ms=%s",
            str(run.run_id),
            int(run.tick_index or 0),
            int(run.sim_time_ms or 0),
        )

        # CRITICAL: prevent payments from racing an unfinished background clearing from the previous tick (lost
        # updates under Postgres).
        await self._await_pending_clearing(run_id, run=run)

        try:
            money = await self._open_money_phase(run_id=run_id, run=run, scenario=scenario)
            if money is None:
                return

            # The money phase has committed, or the run is stopping. `money.stack` owns the session that committed
            # it, and the tail below runs on that session - AFTER the boundary, never inside it. From here on the
            # payments are DURABLE and their observations have resolved exactly once: a tail error costs the tail,
            # never the money (programme 015 / P1).
            async with money.stack:
                session = money.session
                payments_phase = money.phase
                # The same list the boundary planned and locked against, resolved once per run.
                equivalents = run._real_equivalents or []
                try:
                    if money.should_stop:
                        return

                    # 036 B1: the scenario's scripted clearings run FIRST, so the periodic clearing of this tick cannot take
                    # the cycle an episode is about, and the periodic pass of an equivalent that has just had a scripted pass
                    # does not run on this tick. Their committed volume belongs to the tick's `clearing_volume`.
                    scripted_volume, scripted_attempted = await self.run_scripted_clearings(
                        session=session,
                        run_id=run_id,
                        run=run,
                        scenario=scenario,
                        equivalents=equivalents,
                        planned_len=len(payments_phase.planned or []),
                        tick_t0=tick_t0,
                        payments_result=payments_phase,
                    )

                    clearing_volume_by_eq = await self.maybe_run_clearing(
                        session=session,
                        run_id=run_id,
                        run=run,
                        equivalents=equivalents,
                        planned_len=len(payments_phase.planned or []),
                        tick_t0=tick_t0,
                        payments_result=payments_phase,
                        skip_equivalents=scripted_attempted,
                    )
                    for eq_code, amount in scripted_volume.items():
                        clearing_volume_by_eq[eq_code] = clearing_volume_by_eq.get(eq_code, Decimal("0")) + amount

                    await self.apply_trust_decay_and_broadcast(
                        session=session,
                        run_id=run_id,
                        run=run,
                        scenario=scenario,
                        payments_result=payments_phase,
                    )

                    await self.drop_closed_trustlines(run_id=run_id, run=run, equivalents=equivalents)

                    await self.populate_per_eq_metric_values(
                        session=session,
                        run=run,
                        scenario=scenario,
                        equivalents=equivalents,
                        per_eq_route=payments_phase.per_eq_route,
                        clearing_volume_by_eq=clearing_volume_by_eq,
                        per_eq_metric_values=payments_phase.per_eq_metric_values,
                    )

                    await self.persist_tick_tail(
                        session=session,
                        run=run,
                        equivalents=equivalents,
                        tick_t0=tick_t0,
                        planned_len=len(payments_phase.planned),
                        committed=payments_phase.committed,
                        rejected=payments_phase.rejected,
                        errors=payments_phase.errors,
                        timeouts=payments_phase.timeouts,
                        per_eq=payments_phase.per_eq,
                        per_eq_metric_values=payments_phase.per_eq_metric_values,
                        per_eq_edge_stats=payments_phase.per_eq_edge_stats,
                        payments_result=payments_phase,
                    )

                except Exception as tick_error:
                    # CRITICAL: always attempt rollback on tick failure. Otherwise the pooled connection can be
                    # returned in an invalid transaction state, and later ticks fail with "Can't reconnect until
                    # invalid transaction is rolled back".
                    original_tick_error = tick_error

                    def _log_rollback_failure(
                        rollback_error: Exception | asyncio.CancelledError,
                    ) -> None:
                        rr._logger.error(
                            "simulator.real.rollback_failed run_id=%s tick=%s "
                            "original_error=%s rollback_error=%s",
                            str(run.run_id),
                            int(run.tick_index or 0),
                            type(original_tick_error).__name__,
                            type(rollback_error).__name__,
                            exc_info=(
                                type(rollback_error),
                                rollback_error,
                                rollback_error.__traceback__,
                            ),
                        )

                    try:
                        await resolve_rollback_under_cancellation(
                            rollback=session.rollback,
                            on_rollback=(
                                payments_phase.apply_rollback_observations
                                if payments_phase is not None
                                else lambda: None
                            ),
                            on_unknown=(
                                payments_phase.apply_unknown_transaction_observations
                                if payments_phase is not None
                                else lambda: None
                            ),
                            on_failure=_log_rollback_failure,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                    raise
        except SimulatorPidTakenError as e:
            # Programme 024, F-024-4b: the scenario names a real participant. Not a transient failure a later tick
            # could cure, so the run stops now, fail-closed, with the code and the pid in `last_error`.
            rr._logger.warning(
                "simulator.real.run_refused code=%s run_id=%s pid=%s",
                SIMULATOR_PID_TAKEN,
                str(run.run_id),
                e.pid,
            )
            await rr.fail_run(run_id, code=SIMULATOR_PID_TAKEN, message=str(e))
        except Exception as e:
            conflict = money_conflict_name(e)
            rr._logger.warning(
                "simulator.real.tick_failed run_id=%s tick=%s money_conflict=%s",
                str(run.run_id),
                int(run.tick_index or 0),
                conflict or "none",
                exc_info=True,
            )

            if conflict is not None:
                # A TRANSIENT CONFLICT IS NOT A PROGRAMMATIC FAILURE (programme 015 / P1). The money boundary has
                # already replayed the phase up to its budget and rolled the last attempt back; nothing of it is
                # durable. A programmatic failure still takes the branch below and still stops the run.
                await self._record_money_conflict_tick(run_id, run=run, conflict=conflict, error=e)
                return

            with rr._lock:
                run.errors_total += 1
                run._error_timestamps.append(time.time())
                cutoff = time.time() - 60.0
                while run._error_timestamps and run._error_timestamps[0] < cutoff:
                    run._error_timestamps.popleft()
                run._real_consec_tick_failures += 1
                run.last_error = {
                    "code": "REAL_MODE_TICK_FAILED",
                    "message": str(e),
                    "at": rr._utc_now().isoformat(),
                }

            max_consec = int(rr._real_max_consec_tick_failures_limit)
            if max_consec > 0 and run._real_consec_tick_failures >= max_consec:
                await rr.fail_run(
                    run_id,
                    code="REAL_MODE_TICK_FAILED_REPEATED",
                    message=f"Real-mode tick failed {run._real_consec_tick_failures} times in a row",
                )

    # ── before the boundary, and the boundary ─────────────────────────────────────────────────────────

    async def _open_money_phase(self, *, run_id: str, run: RunRecord, scenario: dict):
        """Run everything before the tick's money boundary, then the boundary itself.

        Programme 015 / P1. Returns None when the tick has nothing to do, otherwise the outcome of the bounded
        replay - which owns the session the tail is allowed to use.

        THE BOUNDARY IS DRAWN HERE: after the due injects have completed, and BEFORE the first read and before the
        owner locks. Everything before it runs on a session of its own which is closed before the boundary opens,
        so the money phase always begins with a fresh session, transaction and snapshot - on the first attempt
        exactly as on a replayed one.
        """
        rr = self._runner

        async with db_session.AsyncSessionLocal() as setup_session:
            if not run._real_seeded:
                with rr._lock:
                    if run._real_seeding_lock is None:
                        run._real_seeding_lock = asyncio.Lock()
                    seeding_lock = run._real_seeding_lock

                async with seeding_lock:
                    if not run._real_seeded:
                        try:
                            await rr._seed_scenario_into_db(setup_session, scenario)
                            await setup_session.commit()
                        except Exception:
                            # Programme 021 (T2100 P2-4): the owner of the seeding transaction rolls it back before
                            # the failure leaves this block.
                            await setup_session.rollback()
                            raise
                        run._real_seeded = True

            if run._real_participants is None or run._real_equivalents is None:
                run._real_participants = await rr._load_real_participants(setup_session, scenario)
                eq_set: set[str] = set(str(x).strip().upper() for x in (scenario.get("equivalents") or []))
                eq_set.discard("")

                default_eq = scenario_default_equivalent(scenario)
                if default_eq:
                    eq_set.add(default_eq)

                for tl in scenario.get("trustlines") or []:
                    eq = effective_equivalent(scenario, tl)
                    if eq:
                        eq_set.add(str(eq).strip().upper())

                run._real_equivalents = sorted(eq_set)

            participants = run._real_participants or []
            equivalents = run._real_equivalents or []
            if len(participants) < 2 or not equivalents:
                return None

            # Initialize trust drift (once per run).
            if run._trust_drift_config is None:
                rr._trust_drift_engine.init_trust_drift(run, scenario)

            # Due scenario timeline events (note/stress/inject), BEFORE the owner locks of the money phase, not
            # after. Programme 015, phase B step 3: each inject event is a unit of work that takes the owner locks
            # it needs and commits them away, and the phase returns with no transaction open.
            await rr._apply_due_scenario_events(setup_session, run_id=run_id, run=run, scenario=scenario)

        async def _money_attempt(session):
            """One attempt: the planning inputs, then the boundary - owner locks, planning, staged payments.

            Everything in here is RECREATED per attempt, notably the plan: the planner sizes amounts against
            already-used debt, and a competitor's commit is exactly what invalidates that picture.

            THE PLANNING INPUTS COME FIRST, ON A SESSION OF THEIR OWN THAT IS CLOSED BEFORE `session` TAKES A
            CONNECTION (034 `F-034-2`, §15 review of `62cce627`): the tick holds one pooled connection at a time
            and never waits for another while it holds `FOR UPDATE` on the lines. See `load_planning_inputs`.
            """
            planning_inputs = await self.load_planning_inputs(
                run=run, participants=participants, equivalents=equivalents
            )
            owner_service = PaymentService(session)
            # 027 stage 2: the phase's COMPLETE line set - every non-closed line among the run's participants (its
            # routes are confined to them) in its equivalents, `FOR UPDATE` in `trust_lines.id` order - is this
            # transaction's first statement, before the first payment. A deadlock (40P01) with a later,
            # out-of-order line lock restarts the phase at this outer owner (`money_replay.py`).
            await owner_service.lock_staged_lines(equivalents, {participant_id for participant_id, _pid in participants})

            return await self.run_payments_phase(
                session=session,
                run_id=run_id,
                run=run,
                scenario=scenario,
                participants=participants,
                equivalents=equivalents,
                planning_inputs=planning_inputs,
            )

        return await run_money_phase_with_bounded_replay(
            run_id=run_id,
            run=run,
            lock=rr._lock,
            logger=rr._logger,
            max_attempts=int(rr._real_money_replay_attempts_limit),
            open_session=db_session.AsyncSessionLocal,
            run_money_attempt=_money_attempt,
        )

    async def load_planning_inputs(
        self,
        *,
        run: RunRecord,
        participants: list[tuple[Any, str]],
        equivalents: list[str],
    ) -> tuple[dict[tuple[str, str, str], Decimal], dict[str, int]]:
        """What the planner sizes amounts with - the debts and each equivalent's step - read on a session of its own.

        034 `F-034-2`. Both reads used to run on the money session, inside the money transaction, behind
        `except Exception`: a PostgreSQL error in one of them aborted that transaction and was swallowed, and the
        payments after it failed on the aborted transaction. They now run on their own session, so their failure
        can only cost the plan its inputs: the failed read's transaction is ended here, by its owner, and the
        planner falls back - without the debts to the static limits, without the steps to cents (the payment door
        refuses what is finer than an equivalent's step). Never raises.

        BEFORE THE LINE LOCKS, NOT UNDER THEM (§15 review of `62cce627`, 2026-10-08). `_money_attempt` calls this
        before the money session takes a connection, once per attempt. The first version of the fix read here while
        the money transaction already held `FOR UPDATE` on the lines; with a small pool the tick then waited for a
        second connection under those locks (measured by the review: 9.39 s held against 3.38 s before the fix).
        What this gives up is freshness, not money: a competitor may commit between this read and the locks, and
        the plan may then ask for more than is left. The plan is advisory - the payment service takes the pair's
        lines, reads the debts behind them and checks capacity itself (`PaymentService._bind_payment`,
        `._segment`), and answers such a payment with an honest refusal; planning with no snapshot at all was
        always allowed for the same reason.

        ONE WAIT FOR THE POOL: the connection is taken first, explicitly. If the pool has none, neither read is
        tried and the planner gets its fallbacks after one pool timeout, not two.
        """

        rr = self._runner
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {}
        precision_by_eq: dict[str, int] = {}

        async def _end_failed_read(session: Any, what: str) -> None:
            if rr._should_warn_this_tick(run, key=what):
                rr._logger.warning(
                    "simulator.real.%s run_id=%s tick=%s",
                    what,
                    str(run.run_id),
                    int(run.tick_index or 0),
                    exc_info=True,
                )
            await session.rollback()

        try:
            async with db_session.AsyncSessionLocal() as planning_session:
                await planning_session.connection()
                # Phase 1.4: capacity-aware payment amounts, from the debts as they are AFTER the due events.
                try:
                    debt_snapshot = await rr._load_debt_snapshot_by_pid(planning_session, participants, equivalents)
                except Exception:
                    await _end_failed_read(planning_session, "planning_debt_snapshot_failed")
                # 028 `F-028-32`: amounts in each equivalent's step.
                try:
                    precision_by_eq = {
                        str(code): int(p)
                        for code, p in (
                            await planning_session.execute(
                                select(Equivalent.code, Equivalent.precision).where(
                                    Equivalent.code.in_(list(equivalents))
                                )
                            )
                        ).all()
                    }
                except Exception:
                    await _end_failed_read(planning_session, "planning_precision_failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            rr._logger.warning(
                "simulator.real.planning_session_failed run_id=%s tick=%s",
                str(run.run_id),
                int(run.tick_index or 0),
                exc_info=True,
            )
        return debt_snapshot, precision_by_eq

    async def run_payments_phase(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        participants: list[tuple[Any, str]],
        equivalents: list[str],
        planning_inputs: tuple[dict[tuple[str, str, str], Decimal], dict[str, int]] | None = None,
    ) -> tuple[TickPaymentsPhase, bool]:
        rr = self._runner
        # The tick reads the planning inputs BEFORE `session` holds anything and hands them in (`_money_attempt`).
        # Only a caller that drives this phase directly, with no money transaction of the tick around it (the unit
        # stands), gives none and has them read here.
        if planning_inputs is None:
            planning_inputs = await self.load_planning_inputs(
                run=run, participants=participants, equivalents=equivalents
            )
        debt_snapshot, precision_by_eq = planning_inputs
        planned = rr._plan_real_payments(run, scenario, debt_snapshot=debt_snapshot, precision_by_eq=precision_by_eq)
        # 036 B1: the scenario's due `payment` events join the phase as ordinary planned payments (staged under a
        # savepoint, published after the commit, refused by the core like any other) with the event's own key.
        scripted, scripted_index_by_seq, scripted_epoch = rr.scripted_payments_due(run, scenario, first_seq=len(planned))
        planned = [*planned, *scripted]
        with rr._lock:
            run.ops_sec = float(len(planned))
            run.queue_depth = len(planned)
            run._real_in_flight = 0
            run.current_phase = "payments" if planned else None

        sender_id_by_pid = {pid: participant_id for (participant_id, pid) in participants}
        per_eq_metric_values: dict[str, dict[str, Any]] = {str(eq): {} for eq in equivalents}

        payments_res: RealPaymentsResult = await rr._real_payments_executor.execute_planned_payments(
            session=session,
            run_id=run_id,
            run=run,
            planned=planned,
            equivalents=equivalents,
            sender_id_by_pid=sender_id_by_pid,
            max_in_flight=int(run._real_max_in_flight),
            max_timeouts_per_tick=int(rr._real_max_timeouts_per_tick_limit),
            fail_run=lambda _run_id, code, message: rr.fail_run(_run_id, code=code, message=message),
        )

        stall_ticks = int(payments_res.stall_ticks)
        # Stall warning, throttled: every 5 consecutive stall ticks.
        if stall_ticks > 0 and stall_ticks % 5 == 0:
            rr._logger.warning(
                "simulator.real.all_rejected_stall run_id=%s tick=%s "
                "consec_stall_ticks=%d planned=%d rejected=%d",
                str(run.run_id),
                int(run.tick_index),
                stall_ticks,
                len(planned),
                int(payments_res.rejected),
            )

        should_stop = bool(payments_res.stop_requested)
        # THE ERROR BUDGET DOES NOT SEE TRANSIENT CONFLICTS (programme 015 / P1). `errors` counts terminal
        # per-action outcomes only; a transient conflict propagates out of `execute_planned_payments` to the money
        # boundary, which replays the phase or records a tick without progress (`money_replay.py`).
        max_errors_total = int(rr._real_max_errors_total_limit)
        projected_errors_total = int(run.errors_total) + int(payments_res.errors)
        if max_errors_total > 0 and projected_errors_total >= max_errors_total and not should_stop:
            await rr.fail_run(
                run_id,
                code="REAL_MODE_TOO_MANY_ERRORS",
                message=f"Too many total errors: {projected_errors_total}",
            )
            should_stop = True

        res = TickPaymentsPhase(
            planned=planned,
            per_eq_metric_values=per_eq_metric_values,
            committed=int(payments_res.committed),
            rejected=int(payments_res.rejected),
            errors=int(payments_res.errors),
            timeouts=int(payments_res.timeouts),
            per_eq=dict(payments_res.per_eq),
            per_eq_route=dict(payments_res.per_eq_route),
            per_eq_edge_stats=dict(payments_res.per_eq_edge_stats),
            stall_ticks=stall_ticks,
            deferred_effects=payments_res.deferred_effects,
            staged_tx_ids=frozenset(getattr(payments_res, "staged_tx_ids", ()) or ()),
            # Only the events whose payment reached a TERMINAL outcome are spent by this phase's durability: one whose
            # outcome is not established (a timeout, an unexpected error) waits and is run again by the next tick, under the
            # same key (a repeat of a payment that did land is answered with the stored one).
            scripted_event_indexes=frozenset(
                idx for seq, idx in scripted_index_by_seq.items() if seq not in payments_res.unresolved_seqs
            ),
            scripted_progress=self._scripted_payment_progress(
                scripted_index_by_seq, payments_res.unresolved_seqs, payments_res.deferred_effects
            ),
            on_durable=lambda indexes, progress: rr.mark_scripted_events_fired(run, indexes, scripted_epoch, progress),
        )

        if should_stop:
            await resolve_rollback_under_cancellation(
                rollback=session.rollback,
                on_rollback=res.apply_rollback_observations,
                on_unknown=res.apply_unknown_transaction_observations,
            )

        return res, should_stop

    @staticmethod
    def _scripted_payment_progress(
        index_by_seq: dict[int, int], unresolved_seqs: frozenset[int], deferred_effects: DeferredRealPaymentEffects | None
    ) -> dict[int, dict[str, Any]]:
        """The TRUE outcome of each scripted payment of a phase, by event index (036 B2): a committed payment is `done`
        one the core refused is `refused` with the code its `tx.failed` carries, one whose outcome is not established is
        `incomplete` with the code of the failure; each carries what was attempted (from, to, amount, equivalent).
        Built from the observations the phase already resolves - no second source of truth about a payment."""

        if not index_by_seq or deferred_effects is None:
            return {}
        by_seq = {item.seq: item for item in deferred_effects.items}
        out: dict[int, dict[str, Any]] = {}
        for seq, index in index_by_seq.items():
            item = by_seq.get(seq)
            if item is None:
                continue
            if item.outcome == "committed":
                status, reason = "done", None
            elif seq in unresolved_seqs:
                status, reason = "incomplete", item.error_code
            else:
                status, reason = "refused", item.error_code
            record: dict[str, Any] = {"kind": "payment", "status": status, "equivalent": item.equivalent}
            if reason is not None:
                record["reason"] = str(reason)
            record["payment"] = {"from": item.sender_pid, "to": item.receiver_pid, "amount": item.amount, "equivalent": item.equivalent}
            out[index] = record
        return out

    async def _record_money_conflict_tick(
        self,
        run_id: str,
        *,
        run: RunRecord,
        conflict: str,
        error: BaseException,
    ) -> None:
        """A tick whose money phase lost to contention: no progress, but not an error.

        Programme 015 / P1. A transient conflict does not touch `run.errors_total`, `run._error_timestamps` or
        `run._real_consec_tick_failures`. What can still stop the run is the absence of PROGRESS, counted
        separately: a run that has not committed a money phase for `SIMULATOR_REAL_MAX_CONSEC_MONEY_NO_PROGRESS`
        ticks in a row is not working. `last_error` still gets a code of its own (AGENTS.md §12).
        """
        rr = self._runner
        with rr._lock:
            run._real_consec_money_no_progress_ticks += 1
            no_progress = int(run._real_consec_money_no_progress_ticks)
            run.last_error = {
                "code": "REAL_MODE_MONEY_CONFLICT_UNRESOLVED",
                "message": (
                    f"The tick's money phase did not commit: {conflict} ({type(error).__name__}). "
                    f"Consecutive ticks without money progress: {no_progress}."
                ),
                "at": rr._utc_now().isoformat(),
            }

        rr._logger.warning(
            "simulator.real.money_phase_no_progress run_id=%s tick=%s conflict=%s consec=%s",
            str(run.run_id),
            int(run.tick_index or 0),
            conflict,
            no_progress,
        )

        limit = int(rr._real_max_consec_money_no_progress_limit)
        if limit > 0 and no_progress >= limit:
            await rr.fail_run(
                run_id,
                code="REAL_MODE_MONEY_NO_PROGRESS",
                message=(
                    f"The money phase made no progress for {no_progress} consecutive ticks "
                    f"under database contention"
                ),
            )

    # ── clearing: static cadence, hard timeout, one call to the common runner ─────────────────────────

    def clearing_hard_timeout_sec(self) -> float:
        """The hard timeout of the tick's clearing: `max(2 s, 4 × budget)`, capped by
        `SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC` (default 8), never below 0.1 s."""
        clearing_hard_timeout_sec = max(2.0, float(self._real_clearing_time_budget_ms) / 1000.0 * 4.0)
        env_timeout_cap = float(settings.SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC)
        if env_timeout_cap > 0:
            clearing_hard_timeout_sec = min(clearing_hard_timeout_sec, env_timeout_cap)
        return float(max(0.1, float(clearing_hard_timeout_sec)))

    async def _await_pending_clearing(self, run_id: str, *, run: RunRecord) -> None:
        rr = self._runner

        with rr._lock:
            task = run._real_clearing_task
            if task is not None and task.done():
                run._real_clearing_task = None
                task = None

        if task is None:
            return

        # If the run is no longer active, do not wait: cancel best-effort.
        if getattr(run, "state", None) in ("stopped", "stopping", "error"):
            try:
                task.cancel()
            except Exception:
                pass
            with rr._lock:
                if run._real_clearing_task is task:
                    run._real_clearing_task = None
            return

        # Bounded grace: if clearing is still running from the previous tick, wait a bit so payments don't race it
        # and cause lost updates.
        try:
            hard_timeout = self.clearing_hard_timeout_sec()
        except Exception:
            hard_timeout = 2.0
        grace_sec = max(0.1, float(hard_timeout) * 0.5)

        rr._logger.warning(
            "simulator.real.pending_clearing_await_enter run_id=%s tick=%s grace_sec=%s",
            str(run_id),
            int(getattr(run, "tick_index", 0) or 0),
            grace_sec,
        )

        try:
            await asyncio.wait_for(task, timeout=grace_sec)
        except asyncio.TimeoutError:
            rr._logger.warning(
                "simulator.real.pending_clearing_grace_timeout run_id=%s tick=%s grace_sec=%s",
                str(run_id),
                int(getattr(run, "tick_index", 0) or 0),
                grace_sec,
            )
            try:
                task.cancel()
            except Exception:
                pass
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        except Exception:
            rr._logger.warning(
                "simulator.real.pending_clearing_await_failed run_id=%s tick=%s",
                str(run_id),
                int(getattr(run, "tick_index", 0) or 0),
                exc_info=True,
            )
        finally:
            with rr._lock:
                if run._real_clearing_task is task:
                    run._real_clearing_task = None

    async def maybe_run_clearing(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        planned_len: int,
        tick_t0: float,
        payments_result: Any | None = None,
        skip_equivalents: frozenset[str] = frozenset(),
    ) -> dict[str, Decimal]:
        """The tick's clearing on the static cadence; the committed volume per equivalent, always `Decimal`.

        `skip_equivalents` (036 B1): the equivalents a scripted clearing already ran a pass for on this tick - no periodic
        pass of them runs on it.

        2026-08-20 / p007_t715: the cleared volume is money and feeds the `clearing_volume` metric series, so it
        stays Decimal across every branch - including the early returns.
        """
        if not settings.CLEARING_ENABLED or self._clearing_every_n_ticks <= 0:
            return {str(eq): Decimal("0") for eq in equivalents}

        # Static cadence - the only clearing policy since programme 021 stage 3 removed the adaptive mode.
        if int(run.tick_index) % int(self._clearing_every_n_ticks) != 0:
            return {str(eq): Decimal("0") for eq in equivalents}

        target = [eq for eq in equivalents if str(eq).upper() not in skip_equivalents]
        if not target:
            return {str(eq): Decimal("0") for eq in equivalents}
        volumes = await self._execute_clearing_with_timeout(
            session=session,
            run_id=run_id,
            run=run,
            equivalents=target,
            planned_len=planned_len,
            tick_t0=tick_t0,
            payments_result=payments_result,
        )
        return {str(eq): volumes.get(str(eq), Decimal("0")) for eq in equivalents}

    async def run_scripted_clearings(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        equivalents: list[str],
        planned_len: int,
        tick_t0: float,
        payments_result: Any | None,
    ) -> tuple[dict[str, Decimal], frozenset[str]]:
        """The scenario's due `clearing` events (036 B1), after the payments phase and BEFORE the periodic clearing.

        Returns the volume committed per equivalent (it belongs to the tick's `clearing_volume`) and the equivalents a
        scripted pass was attempted for on this tick: the periodic clearing leaves those out for this tick, so it cannot
        take the cycle the episode is about (the periodic clearing of an EARLIER tick can; that is not repaired here).

        The pass is the tick's own (`_execute_clearing_with_timeout` -> `_run_clearing` -> `run_clearing_pass` in the
        run's perimeter, its hard timeout, `clearing.done` of the equivalent's step). WHAT IT REPORTS IS WHAT HAPPENED:
        the outcome of the pass comes back from the runner (`outcomes`), and only a COMPLETE pass - an empty one included -
        is `done` and spends the event. A pass that did not complete (the equivalent stopped, the pass raised, the hard
        timeout, a clearing still running) leaves the event for the next tick; the progress says `incomplete` with the
        reason and keeps the cycles committed so far (cumulative within the epoch), and a change of status is logged once.
        An event whose equivalent is not the run's, or with clearing disabled, is refused aloud (progress and note).
        The epoch is taken before the pass: a `restart` during it discards the record (the event belongs to a launch that
        is over). A cancellation leaves the event pending."""

        rr = self._runner
        volume: dict[str, Decimal] = {}
        attempted: set[str] = set()
        events = scenario.get("events")
        for idx, evt in enumerate(events if isinstance(events, list) else []):
            if not isinstance(evt, dict) or evt.get("type") != "clearing" or idx in run._real_fired_scenario_event_indexes:
                continue
            t0 = rr._parse_event_time_ms(evt)
            if t0 is None or int(run.sim_time_ms) < int(t0):
                continue
            epoch = int(run._launch_epoch)
            eq = effective_equivalent(scenario, evt)
            if not eq:
                rr._refuse_scripted_event(run, idx, evt, "unresolved_equivalent")
                continue
            if eq not in {str(x).upper() for x in equivalents}:
                rr._refuse_scripted_event(run, idx, evt, "equivalent_not_in_the_run", equivalent=eq)
                continue
            if not settings.CLEARING_ENABLED:
                rr._refuse_scripted_event(run, idx, evt, "clearing_disabled", equivalent=eq)
                continue
            try:
                # Before the pass, as `_run_clearing` does: every ending then knows the equivalent's step.
                precision = await self._equivalent_precision(db_session.AsyncSessionLocal, eq)
            except Exception:
                self._scripted_event_status(run, idx, int(t0), epoch, eq, "incomplete", "equivalent_unavailable", [], spend=False)
                continue

            occurrences: list[Any] = []
            outcomes: dict[str, str] = {}
            volumes = await self._execute_clearing_with_timeout(
                session=session,
                run_id=run_id,
                run=run,
                equivalents=[eq],
                planned_len=planned_len,
                tick_t0=tick_t0,
                payments_result=payments_result,
                on_occurrence=lambda _eq, occurrence: occurrences.append(occurrence),
                outcomes=outcomes,
            )
            attempted.add(eq)
            volume[eq] = volume.get(eq, Decimal("0")) + volumes.get(eq, Decimal("0"))
            with rr._lock:
                pid_by_id = {participant_id: str(pid) for (participant_id, pid) in (run._real_participants or [])}
            cycles = []
            for occurrence in occurrences:
                edges = []
                for edge in occurrence.edges:
                    creditor, debtor = pid_by_id.get(edge.creditor_id), pid_by_id.get(edge.debtor_id)
                    if creditor and debtor and creditor != debtor:
                        edges.append({"from": creditor, "to": debtor})
                cycles.append({"cleared_amount": self._cleared_amount_str(precision, occurrence.amount), "edges": edges})
            outcome = outcomes.get(eq, "no_outcome")
            self._scripted_event_status(
                run, idx, int(t0), epoch, eq, "done" if outcome == "complete" else "incomplete",
                None if outcome == "complete" else outcome, cycles, spend=outcome == "complete",
            )
        return volume, frozenset(attempted)

    def _scripted_event_status(
        self,
        run: RunRecord,
        idx: int,
        t0: int,
        epoch: int,
        eq: str,
        status: str,
        reason: str | None,
        new_cycles: list[dict[str, Any]],
        *,
        spend: bool,
    ) -> None:
        """Record the outcome of one scripted clearing attempt through the runner's one progress writer: discarded if the run
        was restarted since `epoch`, the cycles cumulative within the epoch, the event spent only for a complete pass, a
        change of status logged once."""

        record: dict[str, Any] = {"kind": "clearing", "status": status, "equivalent": eq}
        if reason is not None:
            record["reason"] = reason
        self._runner._write_story_progress(run, idx, t0, epoch, record, spend=spend, add_cycles=new_cycles)

    def _should_warn(self, run: RunRecord, key: str) -> bool:
        try:
            return bool(self._runner._should_warn_this_tick(run, key=key))
        except Exception:
            return True

    @staticmethod
    def _cleared_amount_str(precision: int, amount: Decimal) -> str:
        """The single rendering of `clearing.done.cleared_amount` (012 / `T1207`): in the equivalent's step.

        One field, one scale, whichever way the clearing ended. `precision` is the equivalent's own, read right
        before the pass (`_equivalent_precision`) and held for every ending - the snapshot the interactive action
        takes as `eq_precision` (034 S2b, F-034-3). Until then it came from the `VizPatchHelper` cached on the run,
        or the constant 2 when the run had none: a cancelled pass, which publishes without patches and so without a
        helper, wrote `"2.00"` in a whole-unit equivalent; and a cached helper keeps the precision it was created
        with after the operator changes the equivalent's.
        """
        return to_money_str(amount, precision)

    @staticmethod
    async def _equivalent_precision(session_local: Any, eq: str) -> int:
        """`Equivalent.precision` of `eq` NOW: one read on a session of its own, closed before the runner is called.

        Read before the pass because the cancellation ending may not await. An equivalent that is not there raises
        (the pass would refuse it too); a row without a precision is 2, the codebase's reading of a missing
        `Equivalent.precision` (024 `T2416.1`) - a value of the row, not a fallback for a failed read.
        """
        async with session_local() as precision_session:
            row = (
                await precision_session.execute(select(Equivalent.precision).where(Equivalent.code == eq))
            ).one_or_none()
        if row is None:
            raise ValueError(f"Equivalent {eq} not found")
        return int(2 if row[0] is None else row[0])

    def _done_cycle_edges(
        self, run: RunRecord, eq: str, touched_edges: set[tuple[str, str]]
    ) -> list[dict[str, str]] | None:
        """`clearing.done.cycle_edges`: the touched edges, creditor -> debtor, as the scenario topology has them.

        `touched_edges` are (creditor_pid, debtor_pid) - the trust-line direction of the snapshot links and the edge
        patches. An edge the run's topology cache holds only reversed is flipped, one it does not hold at all is left
        out; without a cache every edge is kept. At most `SIMULATOR_CLEARING_MAX_EDGES_FOR_FX` (at least one).
        """
        with self._runner._lock:
            topology = set(((run._edges_by_equivalent or {}).get(str(eq)) or []))
        limit = max(1, int(self._runner._clearing_max_fx_edges_limit))
        out: list[dict[str, str]] = []
        for creditor, debtor in sorted(touched_edges):
            if topology and (creditor, debtor) not in topology:
                if (debtor, creditor) not in topology:
                    continue
                creditor, debtor = debtor, creditor
            out.append({"from": creditor, "to": debtor})
            if len(out) >= limit:
                break
        return out or None

    async def _run_clearing(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        committed: dict[str, Decimal],
        on_occurrence: Callable[[str, Any], None] | None = None,
        outcomes: dict[str, str] | None = None,
    ) -> None:
        """THE ONE CALL from the tick to the common clearing runner (023 (d); 021 `T2109` removed the driver).

        One pass of `run_clearing_pass` per equivalent, in the run's perimeter, its deadline the tick's clearing
        budget (checked by the runner before every cycle start; the hard timeout is the caller's,
        `_execute_clearing_with_timeout`). Clearing uses sessions of its own (`app.db.session.AsyncSessionLocal`,
        read at call time), never the tick's: a PostgreSQL error would leave that transaction aborted.

        THE PROGRESS (023 decision 10). The runner hands every durable occurrence to `on_committed` right after its
        commit, with no await in between - including one committed while the pass was being cancelled. The callback
        records it first in `committed` (the tick's volume, `V_cyc`, the sum of committed occurrences) and then in
        this equivalent's accounting: cycles, touched participants, and the touched edges converted from the runner's
        debtor -> creditor by UUID to the tick's creditor -> debtor by PID, through the run's own participant list.

        THE ENDINGS, per equivalent:
        * nothing committed - no event; a failure of the pass is classified below;
        * progress - trust growth on the touched edges (its own failure only logged), node and edge patches, one
          `clearing.done`; a failure of the pass after progress is raised only then;
        * cancellation - progress not yet published is published without patches and without growth, and the
          cancellation propagates;
        * the operator's stop or an integrity hold (`MoneyBoundary.MONEY_STOP_REASONS`) - the equivalent is skipped,
          not a run error; any other failure - a run error (`CLEARING_ERROR`, sanitised), then the next equivalent.
        """
        rr = self._runner
        budget_ms = max(1, int(self._real_clearing_time_budget_ms))
        emitter = SseEventEmitter(sse=rr._sse, utc_now=rr._utc_now, logger=rr._logger)
        session_local = db_session.AsyncSessionLocal
        with rr._lock:
            pid_by_id = {participant_id: str(pid) for (participant_id, pid) in (run._real_participants or [])}

        for eq in equivalents:
            eq = str(eq)
            plan_id = f"plan_{secrets.token_hex(6)}"
            cleared_cycles = 0
            cleared_amount = Decimal("0")
            precision: Any = None  # read before the pass; nothing is committed, so nothing published, before that
            touched_nodes: set[str] = set()
            touched_edges: set[tuple[str, str]] = set()
            done_emitted = False

            def _on_committed(occurrence, eq: str = eq) -> None:
                nonlocal cleared_cycles, cleared_amount
                if on_occurrence is not None:
                    on_occurrence(eq, occurrence)  # 036 B1: a scripted clearing keeps the exact cycles; records only
                committed[eq] = committed.get(eq, Decimal("0")) + occurrence.amount
                cleared_cycles += 1
                cleared_amount += occurrence.amount
                for edge in occurrence.edges:
                    debtor_pid = pid_by_id.get(edge.debtor_id)
                    creditor_pid = pid_by_id.get(edge.creditor_id)
                    if debtor_pid:
                        touched_nodes.add(debtor_pid)
                    if creditor_pid:
                        touched_nodes.add(creditor_pid)
                    if creditor_pid and debtor_pid:
                        edge_key = (creditor_pid, debtor_pid)
                        touched_edges.add(edge_key)

            try:
                eq_t0 = time.monotonic()
                rr._logger.info(
                    "simulator.real.clearing_eq_enter run_id=%s tick=%s eq=%s", str(run.run_id), int(run.tick_index), eq
                )
                with rr._lock:
                    run.current_phase = "clearing"

                # Before the pass, so that every ending - the cancelled one too - knows the equivalent's step.
                precision = await self._equivalent_precision(session_local, eq)
                execution_error: Exception | None = None
                try:
                    result = await clearing_runner.run_clearing_pass(
                        session_local,
                        eq,
                        allowed_participant_pids=run_perimeter_pids(run),
                        on_committed=_on_committed,
                        deadline=asyncio.get_running_loop().time() + budget_ms / 1000.0,
                    )
                except clearing_runner.ClearingPassCancelled as cancelled:
                    # A cancellation during planning does NOT stop the planner worker; the result says so
                    # (`planner_abandoned`) and its late plan starts nothing.
                    rr._logger.warning(
                        "simulator.real.clearing_pass_cancelled run_id=%s tick=%s eq=%s committed=%s "
                        "planner_abandoned=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        eq,
                        len(cancelled.result.committed),
                        bool(cancelled.result.planner_abandoned),
                    )
                    raise
                except clearing_runner.ClearingPassError as failed:
                    if cleared_cycles <= 0:
                        raise failed.cause
                    # Progress is durable: publish it below, then let the cause take its classification.
                    execution_error = failed.cause
                    if outcomes is not None:
                        outcomes[eq] = f"pass_error:{type(failed.cause).__name__}"
                else:
                    # 036 B1: the OUTCOME of the pass goes to the caller that asked for it (a scripted clearing), who must
                    # not infer success from the fact that this method returned: it swallows every ending below.
                    if outcomes is not None:
                        outcomes[eq] = (
                            "complete" if result.status == "complete"
                            else f"interrupted:{'unknown' if result.reason is None else result.reason.value}"
                        )
                    rr._logger.info(
                        "simulator.real.clearing_pass_done run_id=%s tick=%s eq=%s status=%s reason=%s "
                        "committed=%s remaining_cycles=%s elapsed_ms=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        eq,
                        result.status,
                        None if result.reason is None else result.reason.value,
                        len(result.committed),
                        result.remaining_cycles,
                        int((time.monotonic() - eq_t0) * 1000.0),
                    )

                if cleared_cycles <= 0:
                    with rr._lock:
                        run.current_phase = None
                    continue

                async with session_local() as clearing_session:
                    if touched_edges:
                        await self._grow_trust_after_clearing(
                            run_id, run, eq, clearing_session, touched_edges
                        )
                    closed: set[tuple[str, str]] = set()
                    node_patch, edge_patch = await self._clearing_patches(
                        run, eq, clearing_session, touched_nodes, touched_edges, cleared_cycles, closed
                    )
                    with rr._lock:
                        run.last_event_type = "clearing.done"
                        run.current_phase = None
                    emitter.emit_clearing_done(
                        run_id=run_id,
                        run=run,
                        equivalent=eq,
                        plan_id=plan_id,
                        cleared_cycles=cleared_cycles,
                        # `to_money_str` is total and cannot raise: no `str(Decimal)` fallback (`1E-8` on the wire).
                        cleared_amount=self._cleared_amount_str(precision, cleared_amount) if cleared_amount > 0 else None,
                        cycle_edges=self._done_cycle_edges(run, eq, touched_edges) if touched_edges else None,
                        node_patch=node_patch,
                        edge_patch=edge_patch,
                    )
                    done_emitted = True
                    # 026 `T2603.2`: the patches were read after the occurrences committed (`on_committed`).
                    await publish_closed_trustlines(emitter=emitter, lock=rr._lock, run_id=run_id, run=run,
                                                    equivalent=eq, pairs=closed)
                    rr._logger.info(
                        "simulator.real.clearing_eq_done run_id=%s tick=%s eq=%s elapsed_ms=%s cleared_cycles=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        eq,
                        int((time.monotonic() - eq_t0) * 1000.0),
                        int(cleared_cycles),
                    )
                    if execution_error is not None:
                        raise execution_error
            except asyncio.CancelledError:
                if cleared_cycles > 0 and not done_emitted:
                    with rr._lock:
                        run.last_event_type = "clearing.done"
                        run.current_phase = None
                    try:
                        emitter.emit_clearing_done(
                            run_id=run_id,
                            run=run,
                            equivalent=eq,
                            plan_id=plan_id,
                            cleared_cycles=cleared_cycles,
                            cleared_amount=self._cleared_amount_str(precision, cleared_amount),
                            cycle_edges=self._done_cycle_edges(run, eq, touched_edges),
                            node_patch=None,
                            edge_patch=None,
                        )
                    except Exception:
                        rr._logger.warning(
                            "simulator.real.clearing_cancel_partial_emit_failed run_id=%s tick=%s eq=%s",
                            str(run.run_id),
                            int(run.tick_index),
                            eq,
                            exc_info=True,
                        )
                raise
            except Exception as exc:
                refusal_reason = (exc.details or {}).get("reason") if isinstance(exc, ConflictException) else None
                if outcomes is not None and eq not in outcomes:
                    outcomes[eq] = (
                        f"equivalent_stopped:{refusal_reason}" if refusal_reason in MoneyBoundary.MONEY_STOP_REASONS
                        else f"clearing_error:{type(exc).__name__}"
                    )
                if refusal_reason in MoneyBoundary.MONEY_STOP_REASONS:
                    # T1544: the operator's stop refuses clearing in THIS equivalent; it is not a failure of the run.
                    # Same rule as a refused payment: skip the equivalent without touching `errors_total`,
                    # `_error_timestamps` or `last_error`, which would otherwise be spent on every clearing tick
                    # until the run-level error limit. Step 5c: an integrity hold is skipped identically.
                    rr._logger.info(
                        "simulator.real.clearing_refused_%s run_id=%s tick=%s eq=%s exc=%s",
                        refusal_reason,
                        str(run.run_id),
                        int(run.tick_index),
                        eq,
                        type(exc).__name__,
                    )
                    with rr._lock:
                        run.current_phase = None
                    continue
                if self._should_warn(run, f"clearing_failed:{eq}"):
                    rr._logger.warning(
                        "simulator.real.clearing_failed run_id=%s tick=%s eq=%s",
                        str(run.run_id),
                        int(run.tick_index),
                        eq,
                        exc_info=True,
                    )
                with rr._lock:
                    run.errors_total += 1
                    run._error_timestamps.append(time.time())
                    cutoff = time.time() - 60.0
                    while run._error_timestamps and run._error_timestamps[0] < cutoff:
                        run._error_timestamps.popleft()
                    # 030 S2 (§15 `T3092`): the executor's step refusal keeps its name - the run's database holds debts
                    # finer than the step and is reseeded; every other failure stays sanitised.
                    step_refused = refusal_reason == OCCURRENCE_AMOUNT_NOT_IN_STEP
                    run.last_error = {
                        "code": "CLEARING_REFUSED" if step_refused else "CLEARING_ERROR",
                        "message": exc.message if step_refused else GeoException().message,
                        "at": rr._utc_now().isoformat(),
                    }
                    run.current_phase = None
                continue

    async def _grow_trust_after_clearing(
        self,
        run_id: str,
        run: RunRecord,
        eq: str,
        clearing_session: Any,
        touched_edges: set[tuple[str, str]],
    ) -> None:
        """Trust growth on the edges clearing touched, on the clearing's session; a failure is logged, not raised."""
        rr = self._runner
        try:
            growth = await rr._trust_drift_engine.apply_trust_growth(
                run=run,
                clearing_session=clearing_session,
                touched_edges=touched_edges,
                eq_code=eq,
                tick_index=int(run.tick_index or 0),
            )
            if int(getattr(growth, "updated_count", 0) or 0) > 0:
                try:
                    edge_patch = await rr._build_edge_patch_for_equivalent(
                        session=clearing_session,
                        run=run,
                        equivalent_code=eq,
                        only_edges=None,
                        include_width_keys=True,
                    )
                    rr._broadcast_topology_edge_patch(
                        run_id=run_id, run=run, equivalent=eq, edge_patch=edge_patch, reason="trust_drift_growth"
                    )
                except Exception:
                    rr._logger.warning("simulator.real.trust_drift.growth_edge_patch_failed", exc_info=True)
        except Exception:
            rr._logger.warning(
                "simulator.real.trust_drift.growth_failed run_id=%s tick=%s eq=%s",
                str(run.run_id),
                int(run.tick_index or 0),
                eq,
                exc_info=True,
            )

    async def _clearing_patches(
        self,
        run: RunRecord,
        eq: str,
        clearing_session: Any,
        touched_nodes: set[str],
        touched_edges: set[tuple[str, str]],
        cleared_cycles: int,
        closed: set[tuple[str, str]] | None = None,
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
        """The node and edge patches of `clearing.done` for what clearing touched; `(None, None)` on any failure."""
        rr = self._runner
        node_patch: list[dict[str, Any]] | None = None
        edge_patch: list[dict[str, Any]] | None = None
        rr._logger.info(
            "simulator.real.clearing_patch_start run_id=%s tick=%s eq=%s touched_nodes=%s touched_edges=%s "
            "cleared_cycles=%s",
            str(run.run_id),
            int(run.tick_index),
            eq,
            int(len(touched_nodes)),
            int(len(touched_edges)),
            int(cleared_cycles),
        )
        patch_t0 = time.monotonic()
        try:
            with rr._lock:
                helper = run._real_viz_by_eq.get(eq)
            if helper is None:
                helper = await VizPatchHelper.create(
                    clearing_session,
                    equivalent_code=eq,
                    refresh_every_ticks=int(settings.SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS or 10),
                )
                with rr._lock:
                    run._real_viz_by_eq[eq] = helper

            participant_ids: list[uuid.UUID] = [pid for (pid, _) in (run._real_participants or [])]
            await helper.maybe_refresh_quantiles(
                clearing_session, tick_index=int(run.tick_index), participant_ids=participant_ids
            )

            pids = sorted({str(x).strip() for x in touched_nodes if str(x).strip()})
            if pids:
                res = await clearing_session.execute(select(Participant).where(Participant.pid.in_(pids)))
                pid_to_participant = {p.pid: p for p in res.scalars().all()}
                node_patch = await helper.compute_node_patches(
                    clearing_session, pid_to_participant=pid_to_participant, pids=pids
                ) or None
                edge_patch = await rr._edge_patch_builder.build_edge_patch_for_pairs(
                    session=clearing_session,
                    helper=helper,
                    edges_pairs=sorted(touched_edges),
                    pid_to_participant=pid_to_participant,
                    closed=closed,
                ) or None
        except Exception:
            if self._should_warn(run, f"clearing_done_patch_failed:{eq}"):
                rr._logger.debug(
                    "simulator.real.clearing_done_patch_failed run_id=%s tick=%s eq=%s",
                    str(run.run_id),
                    int(run.tick_index),
                    eq,
                    exc_info=True,
                )
            node_patch = None
            edge_patch = None

        patch_ms = int((time.monotonic() - patch_t0) * 1000.0)
        if patch_ms > 500:
            rr._logger.warning(
                "simulator.real.clearing_patch_slow run_id=%s tick=%s eq=%s elapsed_ms=%s",
                str(run.run_id),
                int(run.tick_index),
                eq,
                patch_ms,
            )
        rr._logger.info(
            "simulator.real.clearing_patch_done run_id=%s tick=%s eq=%s elapsed_ms=%s",
            str(run.run_id),
            int(run.tick_index),
            eq,
            patch_ms,
        )
        return node_patch, edge_patch

    async def _execute_clearing_with_timeout(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        planned_len: int,
        tick_t0: float,
        payments_result: Any | None,
        on_occurrence: Callable[[str, Any], None] | None = None,
        outcomes: dict[str, str] | None = None,
    ) -> dict[str, Decimal]:
        rr = self._runner
        tick_index = int(run.tick_index)

        # End the transaction BEFORE clearing to release the DB write lock. Not the money commit (programme 015 /
        # P1): the money committed at its own boundary. What is committed here is whatever the tail has read or
        # written since; clearing runs in sessions of its own and must not queue behind this one. The observation
        # callbacks are no-ops after a successful money commit: the buffer resolves once.
        commit_t0 = time.monotonic()
        await self._commit_and_resolve(session, **self._phase_callbacks(payments_result))
        commit_ms = (time.monotonic() - commit_t0) * 1000.0
        if commit_ms > 500.0:
            rr._logger.warning(
                "simulator.real.tick_commit_slow run_id=%s tick=%s commit_ms=%s total_tick_ms=%s",
                str(run.run_id),
                tick_index,
                int(commit_ms),
                int((time.monotonic() - tick_t0) * 1000.0),
            )

        rr._logger.info(
            "simulator.real.tick_clearing_enter run_id=%s tick=%s eqs=%s planned=%s",
            str(run.run_id),
            tick_index,
            ",".join([str(x) for x in (equivalents or [])]),
            int(planned_len),
        )

        clearing_t0 = time.monotonic()
        clearing_hard_timeout_sec = self.clearing_hard_timeout_sec()

        with rr._lock:
            existing = run._real_clearing_task
            if existing is not None and existing.done():
                run._real_clearing_task = None
                existing = None
            clearing_task = existing

        if clearing_task is None:
            committed: dict[str, Decimal] = {str(eq): Decimal("0") for eq in equivalents}
            clearing_task = asyncio.create_task(
                self._run_clearing(
                    session=session, run_id=run_id, run=run, equivalents=equivalents, committed=committed,
                    on_occurrence=on_occurrence, outcomes=outcomes,
                )
            )
            self._clearing_progress[clearing_task] = committed
            with rr._lock:
                run._real_clearing_task = clearing_task
        else:
            committed = self._clearing_progress.get(clearing_task) or {}
            rr._logger.warning(
                "simulator.real.tick_clearing_already_running run_id=%s tick=%s",
                str(run.run_id),
                tick_index,
            )
            if outcomes is not None:  # the pass in flight is not ours: its outcome is not ours to report
                for eq in equivalents:
                    outcomes.setdefault(str(eq), "clearing_already_running")

        try:
            await asyncio.wait_for(clearing_task, timeout=clearing_hard_timeout_sec)
            with rr._lock:
                if run._real_clearing_task is clearing_task:
                    run._real_clearing_task = None
        except asyncio.TimeoutError:
            rr._logger.warning(
                "simulator.real.tick_clearing_hard_timeout run_id=%s tick=%s timeout_sec=%s",
                str(run.run_id),
                tick_index,
                clearing_hard_timeout_sec,
            )
            if outcomes is not None:
                for eq in equivalents:
                    outcomes.setdefault(str(eq), "hard_timeout")
            # Managed timeout: cancel best-effort so we don't leak background clearing.
            try:
                clearing_task.cancel()
            except Exception:
                pass
            try:
                await clearing_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            with rr._lock:
                if run._real_clearing_task is clearing_task:
                    run._real_clearing_task = None
                run.current_phase = None
        except Exception:
            if outcomes is not None:
                for eq in equivalents:
                    outcomes.setdefault(str(eq), "clearing_task_failed")
            with rr._lock:
                if run._real_clearing_task is clearing_task:
                    run._real_clearing_task = None
            rr._logger.warning(
                "simulator.real.tick_clearing_failed run_id=%s tick=%s",
                str(run.run_id),
                tick_index,
                exc_info=True,
            )

        rr._logger.info(
            "simulator.real.tick_clearing_done run_id=%s tick=%s elapsed_ms=%s",
            str(run.run_id),
            tick_index,
            int((time.monotonic() - clearing_t0) * 1000.0),
        )

        # THE COMMITTED PROGRESS, not the task's return (programme 021 stage 4): a timeout or a failure after a
        # commit keeps the volume that is already durable in the database and in `clearing.done`.
        volumes = {str(eq): Decimal("0") for eq in equivalents}
        for eq, amount in committed.items():
            if str(eq) in volumes:
                volumes[str(eq)] = amount
        return volumes

    # ── trust decay: its own commit, rolled back by its owner on failure ──────────────────────────────

    async def apply_trust_decay_and_broadcast(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        payments_result: Any | None = None,
    ) -> None:
        rr = self._runner
        trust_drift_engine = rr._trust_drift_engine
        tick_index = int(run.tick_index or 0)
        try:
            decay_res = await trust_drift_engine.apply_trust_decay(
                run=run,
                session=session,
                tick_index=tick_index,
                scenario=scenario,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            rr._logger.warning(
                "simulator.real.trust_drift.decay_failed run_id=%s tick=%s",
                str(run.run_id),
                tick_index,
                exc_info=True,
            )
            # Programme 021 (T2100 P2-4): the tick OWNS the decay's transaction - it commits it below - so a failed
            # decay is rolled back HERE, before the tick continues. Without it, whatever the decay had staged (a
            # changed limit, its audit rows) stayed in this session and the tail's own commit made it durable. A
            # rollback that itself fails is not swallowed: the tick must not go on to a commit that could carry the
            # half-done decay.
            await resolve_rollback_under_cancellation(
                rollback=session.rollback,
                on_rollback=lambda: None,
                on_unknown=lambda: None,
            )
            return

        if not decay_res.updated_count:
            return

        callbacks = self._phase_callbacks(payments_result)
        on_commit = callbacks["on_commit"]

        def apply_committed_effects() -> None:
            self._apply_callback(on_commit, kind="post_commit")
            self._apply_callback(
                lambda: trust_drift_engine.apply_committed_effects(scenario=scenario, result=decay_res),
                kind="trust_drift_post_commit",
            )

        # 031 (BACKLOG item 14): a failed COMMIT whose acknowledgement was lost is resolved by the persisted limits;
        # a decay established as committed is reported below like any other.
        await commit_trust_drift(
            session=session,
            result=decay_res,
            on_commit=lambda: self._apply_callback(apply_committed_effects, kind="post_commit"),
            on_rollback=lambda: self._apply_callback(callbacks["on_rollback"], kind="rollback"),
            on_unknown=lambda: self._apply_callback(callbacks["on_unknown"], kind="unknown"),
            logger=rr._logger,
        )

        # Notify the frontend about changed limits via edge_patch (no full refresh).
        try:
            for eq in sorted(decay_res.touched_equivalents or set()):
                eq_upper = str(eq or "").strip().upper()
                if not eq_upper:
                    continue
                only_edges = (decay_res.touched_edges_by_eq or {}).get(eq_upper)
                edge_patch = await rr._build_edge_patch_for_equivalent(
                    session=session,
                    run=run,
                    equivalent_code=eq_upper,
                    only_edges=only_edges,
                    include_width_keys=True,
                )
                rr._broadcast_topology_edge_patch(
                    run_id=run_id,
                    run=run,
                    equivalent=eq_upper,
                    edge_patch=edge_patch,
                    reason="trust_drift_decay",
                )
        except Exception:
            rr._logger.warning(
                "simulator.real.trust_drift.decay_edge_patch_broadcast_error",
                exc_info=True,
            )

    # ── metrics and the persistence tail: their own commit ────────────────────────────────────────────

    async def drop_closed_trustlines(self, *, run_id: str, run: RunRecord, equivalents: list[str]) -> None:
        """029 `F-029-11`: once a tick, per equivalent, the pairs the run's edge cache holds are re-read and those
        with no live line leave the run (`publish_closed_trustlines`: cache, scenario, one `removed_edges`). A
        line the ledger closed past every patch of this run - another session's payment, an API close - was
        otherwise counted by `active_trustlines` and offered to the planner until a patch happened to cover it.
        One SELECT per equivalent; never raises (a failed re-read removes nothing and is logged there)."""

        rr = self._runner
        emitter = SseEventEmitter(sse=rr._sse, utc_now=rr._utc_now, logger=rr._logger)
        with rr._lock:
            held = {str(eq): list((run._edges_by_equivalent or {}).get(str(eq)) or ()) for eq in equivalents}
        for eq, pairs in held.items():
            await publish_closed_trustlines(emitter=emitter, lock=rr._lock, run_id=run_id, run=run, equivalent=eq,
                                            pairs=pairs)

    async def populate_per_eq_metric_values(
        self,
        *,
        session: Any,
        run: RunRecord,
        scenario: dict[str, Any],
        equivalents: list[str],
        per_eq_route: dict[str, Any],
        clearing_volume_by_eq: dict[str, Decimal | float],
        per_eq_metric_values: dict[str, dict[str, Any]],
    ) -> None:
        rr = self._runner
        # Real total debt snapshot, throttled: the aggregate SUM can become hot on large Debt tables. Only
        # equivalents actually measured in this tick appear here; a missing key means "not measured now" and is
        # persisted as NULL, never a stale or zero value stamped as fresh (spec 007, F-007-1).
        total_debt_by_eq: dict[str, Decimal] = {}

        metrics_every_n = int(self._real_db_metrics_every_n_ticks)
        should_refresh_total_debt = metrics_every_n <= 1 or (int(run.tick_index) % int(metrics_every_n) == 0)

        if should_refresh_total_debt:
            try:
                eq_rows = (
                    await session.execute(
                        select(Equivalent.id, Equivalent.code).where(Equivalent.code.in_(list(equivalents)))
                    )
                ).all()
                eq_id_by_code = {str(code): eq_id for (eq_id, code) in eq_rows}
                with rr._lock:
                    perimeter_ids = [participant_id for participant_id, _pid in (run._real_participants or [])]
                for eq_code, eq_id in eq_id_by_code.items():
                    # 034 S3 (F-034-14): the debts among the run's own participants - its perimeter, as for the
                    # tick's clearing - not every debt of the equivalent: another run's debts are not this run's.
                    total = (
                        await session.execute(
                            select(func.coalesce(func.sum(Debt.amount), 0)).where(
                                Debt.equivalent_id == eq_id,
                                Debt.debtor_id.in_(perimeter_ids),
                                Debt.creditor_id.in_(perimeter_ids),
                            )
                        )
                    ).scalar_one()
                    # 2026-08-20 / p007_t715: `total_debt` is money; the SUM over Numeric(20, 8) stays Decimal.
                    total_debt_by_eq[str(eq_code)] = total if isinstance(total, Decimal) else Decimal(str(total))

                with rr._lock:
                    run._real_total_debt_by_eq = dict(total_debt_by_eq)
                    run._real_total_debt_tick = int(run.tick_index)
            except Exception as exc:
                if rr._should_warn_this_tick(run, key="total_debt_snapshot_failed"):
                    rr._logger.warning(
                        "simulator.real.total_debt_snapshot_failed run_id=%s tick=%s error_class=%s equivalents=%d",
                        str(run.run_id),
                        int(run.tick_index),
                        type(exc).__name__,
                        len(equivalents),
                        exc_info=True,
                    )
                # A failed snapshot is not a measurement: persist nothing for total_debt this tick.
                total_debt_by_eq = {}

        for eq in equivalents:
            r = per_eq_route.get(str(eq), {}) or {}
            n = float(r.get("route_len_n", 0.0) or 0.0)
            s = float(r.get("route_len_sum", 0.0) or 0.0)
            # No successful route in this tick means the average is undefined, not zero.
            if n > 0:
                per_eq_metric_values[str(eq)]["avg_route_length"] = float(s / n)
            if str(eq) in total_debt_by_eq:
                per_eq_metric_values[str(eq)]["total_debt"] = total_debt_by_eq[str(eq)]
            # Clearing volume is money too; keep it exact instead of re-narrowing.
            raw_volume = clearing_volume_by_eq.get(str(eq)) or 0
            per_eq_metric_values[str(eq)]["clearing_volume"] = (
                raw_volume if isinstance(raw_volume, Decimal) else Decimal(str(raw_volume))
            )

        # Network topology metrics (Phase 3): active participants from the in-memory scenario (no DB).
        _scenario_parts = scenario.get("participants") or []
        _active_participants_count = float(
            sum(
                1
                for _p in _scenario_parts
                if isinstance(_p, dict) and str(_p.get("status") or "active").strip().lower() == "active"
            )
        )

        # Active trustlines per equivalent from the run's topology cache (already reflects inject ops).
        with rr._lock:
            _edges_snapshot = dict(run._edges_by_equivalent or {})

        for eq in equivalents:
            per_eq_metric_values[str(eq)]["active_participants"] = _active_participants_count
            per_eq_metric_values[str(eq)]["active_trustlines"] = float(len(_edges_snapshot.get(str(eq), [])))

    async def persist_tick_tail(
        self,
        *,
        session: Any,
        run: RunRecord,
        equivalents: list[str],
        tick_t0: float,
        planned_len: int,
        committed: int,
        rejected: int,
        errors: int,
        timeouts: int,
        per_eq: dict[str, Any],
        per_eq_metric_values: dict[str, dict[str, Any]],
        per_eq_edge_stats: dict[str, Any],
        payments_result: Any | None = None,
    ) -> None:
        rr = self._runner
        computed_at = rr._utc_now()
        with rr._lock:
            run._real_last_tick_storage_payload = {
                "run_id": str(run.run_id),
                "tick_index": int(run.tick_index),
                "t_ms": int(run.sim_time_ms),
                "per_equivalent": per_eq,
                "metric_values_by_eq": per_eq_metric_values,
                "bottlenecks": {
                    "computed_at": computed_at,
                    "equivalents": list(equivalents),
                    "edge_stats_by_eq": per_eq_edge_stats,
                },
            }

        metrics_every_n = int(self._real_db_metrics_every_n_ticks)
        bottlenecks_every_n = int(self._real_db_bottlenecks_every_n_ticks)

        should_write_metrics = metrics_every_n <= 1 or (int(run.tick_index) % int(metrics_every_n) == 0)
        should_write_bottlenecks = bottlenecks_every_n <= 1 or (int(run.tick_index) % int(bottlenecks_every_n) == 0)

        # 2026-08-22 / p009: the writers swallow their own failures by design, so their return value is the only
        # signal that the tick actually reached storage. Only an explicit False means "not written".
        wrote_everything = True

        if should_write_metrics:
            wrote_everything &= (
                await simulator_storage.write_tick_metrics(
                    run_id=run.run_id,
                    t_ms=int(run.sim_time_ms),
                    per_equivalent=per_eq,
                    metric_values_by_eq=per_eq_metric_values,
                    session=session,
                    commit=False,
                )
                is not False
            )

        if should_write_bottlenecks and rr._db_enabled():
            for eq in equivalents:
                wrote_everything &= (
                    await simulator_storage.write_tick_bottlenecks(
                        run_id=run.run_id,
                        equivalent=str(eq),
                        computed_at=computed_at,
                        edge_stats=per_eq_edge_stats.get(str(eq), {}),
                        session=session,
                        limit=50,
                        commit=False,
                    )
                    is not False
                )

        # Marking a tick flushed is what makes `flush_pending_storage` skip it on stop. Marking it after a
        # swallowed failure turns a retryable write error into permanent loss - the reported half of `F-009-2`.
        # 024 `T2416.3`: and only once the commit is CONFIRMED - in `on_commit`, which the resolver also calls
        # when the commit completed under cancellation (a line after the `await` would not run then).
        callbacks = self._phase_callbacks(payments_result)
        if (should_write_metrics or should_write_bottlenecks) and wrote_everything:
            flushed_tick, phase_on_commit = int(run.tick_index), callbacks["on_commit"]

            def _on_commit() -> None:
                with rr._lock:
                    run._real_last_tick_storage_flushed_tick = flushed_tick
                if phase_on_commit is not None:
                    phase_on_commit()

            callbacks["on_commit"] = _on_commit

        commit_t0 = time.monotonic()
        await self._commit_and_resolve(session, **callbacks)
        commit_ms = (time.monotonic() - commit_t0) * 1000.0
        if commit_ms > 500.0:
            rr._logger.warning(
                "simulator.real.tick_commit_slow run_id=%s tick=%s commit_ms=%s total_tick_ms=%s",
                str(run.run_id),
                int(run.tick_index),
                int(commit_ms),
                int((time.monotonic() - tick_t0) * 1000.0),
            )

        now_ms = int(time.time() * 1000)
        tick_write_every_ms = int(self._real_last_tick_write_every_ms)
        artifacts_sync_every_ms = int(self._real_artifacts_sync_every_ms)

        if tick_write_every_ms > 0 and (now_ms - int(run._artifact_last_tick_written_at_ms or 0)) >= tick_write_every_ms:
            rr._artifacts.write_real_tick_artifact(
                run,
                {
                    "tick_index": run.tick_index,
                    "sim_time_ms": run.sim_time_ms,
                    "budget": int(planned_len),
                    "committed": int(committed),
                    "rejected": int(rejected),
                    "errors": int(errors),
                    "timeouts": int(timeouts),
                },
            )
            run._artifact_last_tick_written_at_ms = now_ms

        if artifacts_sync_every_ms > 0 and (now_ms - int(run._artifact_last_sync_at_ms or 0)) >= artifacts_sync_every_ms:
            await simulator_storage.sync_artifacts(run)
            run._artifact_last_sync_at_ms = now_ms

    async def flush_pending_storage(self, run_id: str) -> None:
        """Best-effort flush of the last computed tick metrics/bottlenecks.

        Used on stop/error to avoid losing the last batch when DB writes are throttled.
        """
        rr = self._runner
        run = rr._get_run(run_id)

        if not rr._db_enabled():
            return

        if str(run.mode) != "real":
            return

        payload = run._real_last_tick_storage_payload
        if not isinstance(payload, dict):
            return

        # 029 `F-029-12`: `is None`, not `or -1` - tick 0 is a tick, and a mark of 0 says it was flushed.
        raw_tick, raw_flushed = payload.get("tick_index"), run._real_last_tick_storage_flushed_tick
        last_tick = -1 if raw_tick is None else int(raw_tick)
        if last_tick < 0:
            return

        flushed_tick = -1 if raw_flushed is None else int(raw_flushed)
        if flushed_tick >= last_tick:
            return

        try:
            wrote_everything = True
            async with db_session.AsyncSessionLocal() as session:
                try:
                    wrote_everything &= (
                        await simulator_storage.write_tick_metrics(
                            run_id=str(payload.get("run_id") or run.run_id),
                            t_ms=int(payload.get("t_ms") or 0),
                            per_equivalent=payload.get("per_equivalent") or {},
                            metric_values_by_eq=payload.get("metric_values_by_eq") or {},
                            session=session,
                        )
                        is not False
                    )
                    if rr._db_enabled() and isinstance(payload.get("bottlenecks"), dict):
                        computed_at = payload.get("bottlenecks", {}).get("computed_at") or rr._utc_now()
                        edge_stats_by_eq = payload.get("bottlenecks", {}).get("edge_stats_by_eq") or {}
                        equivalents = payload.get("bottlenecks", {}).get("equivalents") or []
                        for eq in equivalents:
                            wrote_everything &= (
                                await simulator_storage.write_tick_bottlenecks(
                                    run_id=str(payload.get("run_id") or run.run_id),
                                    equivalent=str(eq),
                                    computed_at=computed_at,
                                    edge_stats=edge_stats_by_eq.get(str(eq), {}) or {},
                                    session=session,
                                    limit=50,
                                )
                                is not False
                            )
                except Exception:
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                    raise

            if wrote_everything:
                with rr._lock:
                    run._real_last_tick_storage_flushed_tick = int(last_tick)
            else:
                # This is the retry path itself. Claiming the tick is flushed after a swallowed failure here would
                # end the last chance the data had.
                rr._logger.warning(
                    "simulator.real.flush_pending_storage_incomplete run_id=%s tick=%s",
                    str(run_id),
                    int(last_tick),
                )
        except Exception:
            rr._logger.warning(
                "simulator.real.flush_pending_storage_failed run_id=%s",
                str(run_id),
                exc_info=True,
            )
