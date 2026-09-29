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
6. The post-tick audit (best effort).

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
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy import func, select

import app.core.clearing.runner as clearing_runner
import app.core.simulator.storage as simulator_storage
import app.db.session as db_session
from app.config import settings
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
from app.core.simulator.post_tick_audit import audit_tick_balance
from app.core.simulator.real_payments_executor import DeferredRealPaymentEffects, RealPaymentsResult
from app.core.simulator.real_scenario_seeder import SIMULATOR_PID_TAKEN, SimulatorPidTakenError
from app.core.simulator.run_perimeter import run_perimeter_pids
from app.core.simulator.scenario_equivalent import (
    effective_equivalent,
    scenario_default_equivalent,
)
from app.core.simulator.sse_broadcast import SseEventEmitter
from app.core.simulator.viz_patch_helper import VizPatchHelper
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import ConflictException, GeoException

if TYPE_CHECKING:
    from app.core.simulator.real_runner_impl import RealRunnerImpl


@dataclass(frozen=True)
class TickPaymentsPhase:
    """What one attempt of the money phase produced (was `RealTickPaymentsPhaseResult`)."""

    debt_snapshot: dict[tuple[str, str, str], Decimal]
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

    def discard_observations(self) -> bool:
        """Destroy this attempt's observations without publishing them (a superseded attempt)."""
        if self.deferred_effects is None:
            return False
        return self.deferred_effects.discard()

    def apply_deferred_effects(self) -> bool:
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

                    clearing_volume_by_eq = await self.maybe_run_clearing(
                        session=session,
                        run_id=run_id,
                        run=run,
                        equivalents=equivalents,
                        planned_len=len(payments_phase.planned or []),
                        tick_t0=tick_t0,
                        payments_result=payments_phase,
                    )

                    await self.apply_trust_decay_and_broadcast(
                        session=session,
                        run_id=run_id,
                        run=run,
                        debt_snapshot=payments_phase.debt_snapshot,
                        scenario=scenario,
                        payments_result=payments_phase,
                    )

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

                    await self._audit_after_tick(
                        session=session,
                        run_id=run_id,
                        run=run,
                        equivalents=equivalents,
                        payments_phase=payments_phase,
                        clearing_volume_by_eq=clearing_volume_by_eq,
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
            """The inside of the boundary: owner locks, snapshot, planning, staged payments.

            Everything in here is RECREATED per attempt, notably the plan: the planner sizes amounts against
            already-used debt, and a competitor's commit is exactly what invalidates that picture.
            """
            owner_service = PaymentService(session)
            # The owner set is this transaction's first statement. A SERIALIZABLE waiter can still receive 40001
            # here, and `money_replay.py` restarts the phase at this outer owner.
            await owner_service.acquire_shared_equivalent_locks(equivalents)

            return await self.run_payments_phase(
                session=session,
                run_id=run_id,
                run=run,
                scenario=scenario,
                participants=participants,
                equivalents=equivalents,
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

    async def run_payments_phase(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        participants: list[tuple[Any, str]],
        equivalents: list[str],
    ) -> tuple[TickPaymentsPhase, bool]:
        rr = self._runner
        # Phase 1.4: capacity-aware payment amounts. The debt snapshot is loaded *after* events (which may mutate
        # the DB). Best-effort: if the query fails, planning falls back to static limits.
        debt_snapshot: dict[tuple[str, str, str], Decimal] = {}
        try:
            debt_snapshot = await rr._load_debt_snapshot_by_pid(session, participants, equivalents)
        except Exception:
            rr._logger.debug("capacity_aware: debt snapshot load failed, falling back to static limits")

        planned = rr._plan_real_payments(run, scenario, debt_snapshot=debt_snapshot)
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
            debt_snapshot=debt_snapshot,
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
        )

        if should_stop:
            await resolve_rollback_under_cancellation(
                rollback=session.rollback,
                on_rollback=res.apply_rollback_observations,
                on_unknown=res.apply_unknown_transaction_observations,
            )

        return res, should_stop

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
    ) -> dict[str, Decimal]:
        """The tick's clearing on the static cadence; the committed volume per equivalent, always `Decimal`.

        2026-08-20 / p007_t715: the cleared volume is money and feeds the `clearing_volume` metric series, so it
        stays Decimal across every branch - including the early returns.
        """
        if not settings.CLEARING_ENABLED or self._clearing_every_n_ticks <= 0:
            return {str(eq): Decimal("0") for eq in equivalents}

        # Static cadence - the only clearing policy since programme 021 stage 3 removed the adaptive mode.
        if int(run.tick_index) % int(self._clearing_every_n_ticks) != 0:
            return {str(eq): Decimal("0") for eq in equivalents}

        return await self._execute_clearing_with_timeout(
            session=session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            planned_len=planned_len,
            tick_t0=tick_t0,
            payments_result=payments_result,
        )

    def _should_warn(self, run: RunRecord, key: str) -> bool:
        try:
            return bool(self._runner._should_warn_this_tick(run, key=key))
        except Exception:
            return True

    def _cleared_amount_str(self, run: RunRecord, eq: str, amount: Decimal) -> str:
        """The single rendering of `clearing.done.cleared_amount` (012 / `T1207`).

        One field, one scale, whichever way the clearing ended: the precision is the equivalent's, from the
        `VizPatchHelper` cached on the run (no DB round trip), and 2 - the codebase's default for a missing
        `Equivalent.precision` - before the first helper exists, on every path alike. The field used to be produced
        three times with two scales, chosen by whether the clearing had been cancelled.
        """
        precision = 2
        try:
            with self._runner._lock:
                helper = (run._real_viz_by_eq or {}).get(str(eq))
            if helper is not None:
                precision = int(2 if getattr(helper, "precision", None) is None else helper.precision)
        except Exception:
            precision = 2
        return to_money_str(amount, precision)

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
            touched_nodes: set[str] = set()
            touched_edges: set[tuple[str, str]] = set()
            cleared_amount_per_edge: dict[tuple[str, str], float] = {}
            done_emitted = False

            def _on_committed(occurrence, eq: str = eq) -> None:
                nonlocal cleared_cycles, cleared_amount
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
                        cleared_amount_per_edge[edge_key] = cleared_amount_per_edge.get(edge_key, 0.0) + float(
                            occurrence.amount
                        )

            try:
                eq_t0 = time.monotonic()
                rr._logger.warning(
                    "simulator.real.clearing_eq_enter run_id=%s tick=%s eq=%s", str(run.run_id), int(run.tick_index), eq
                )
                with rr._lock:
                    run.current_phase = "clearing"

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
                else:
                    rr._logger.warning(
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
                            run_id, run, eq, clearing_session, touched_edges, cleared_amount_per_edge
                        )
                    node_patch, edge_patch = await self._clearing_patches(
                        run, eq, clearing_session, touched_nodes, touched_edges, cleared_cycles
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
                        cleared_amount=self._cleared_amount_str(run, eq, cleared_amount) if cleared_amount > 0 else None,
                        cycle_edges=self._done_cycle_edges(run, eq, touched_edges) if touched_edges else None,
                        node_patch=node_patch,
                        edge_patch=edge_patch,
                    )
                    done_emitted = True
                    rr._logger.warning(
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
                            cleared_amount=self._cleared_amount_str(run, eq, cleared_amount),
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
                    run.last_error = {
                        "code": "CLEARING_ERROR",
                        "message": GeoException().message,
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
        cleared_amount_per_edge: dict[tuple[str, str], float],
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
                cleared_amount_per_edge=cleared_amount_per_edge,
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
    ) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
        """The node and edge patches of `clearing.done` for what clearing touched; `(None, None)` on any failure."""
        rr = self._runner
        node_patch: list[dict[str, Any]] | None = None
        edge_patch: list[dict[str, Any]] | None = None
        rr._logger.warning(
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
        rr._logger.warning(
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

        rr._logger.warning(
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
                    session=session, run_id=run_id, run=run, equivalents=equivalents, committed=committed
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
            with rr._lock:
                if run._real_clearing_task is clearing_task:
                    run._real_clearing_task = None
            rr._logger.warning(
                "simulator.real.tick_clearing_failed run_id=%s tick=%s",
                str(run.run_id),
                tick_index,
                exc_info=True,
            )

        rr._logger.warning(
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
        debt_snapshot: dict[tuple[str, str, str], Any],
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
                debt_snapshot=debt_snapshot,
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

        await self._commit_and_resolve(
            session,
            on_commit=apply_committed_effects,
            on_rollback=callbacks["on_rollback"],
            on_unknown=callbacks["on_unknown"],
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
                for eq_code, eq_id in eq_id_by_code.items():
                    total = (
                        await session.execute(
                            select(func.coalesce(func.sum(Debt.amount), 0)).where(Debt.equivalent_id == eq_id)
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
        if (should_write_metrics or should_write_bottlenecks) and wrote_everything:
            with rr._lock:
                run._real_last_tick_storage_flushed_tick = int(run.tick_index)

        commit_t0 = time.monotonic()
        await self._commit_and_resolve(session, **self._phase_callbacks(payments_result))
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

        last_tick = int(payload.get("tick_index", -1) or -1)
        if last_tick < 0:
            return

        flushed_tick = int(run._real_last_tick_storage_flushed_tick or -1)
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

    # ── the post-tick audit (best effort) ─────────────────────────────────────────────────────────────

    async def _audit_after_tick(
        self,
        *,
        session: Any,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        payments_phase: TickPaymentsPhase,
        clearing_volume_by_eq: dict[str, Decimal],
    ) -> None:
        """Detect participant drift; kept as it was (dated decision, spec 021 Changelog 2026-09-28, stage 4)."""
        rr = self._runner
        try:
            emitter = getattr(rr, "_sse_emitter", None)
            sim_idem = getattr(rr._real_payments_executor, "_sim_idempotency_key", None)

            for eq_code in equivalents:
                audit = await audit_tick_balance(
                    session=session,
                    equivalent_code=str(eq_code),
                    tick_index=int(run.tick_index or 0),
                    payments_result=payments_phase,
                    clearing_volume_by_eq=clearing_volume_by_eq,
                    run_id=str(run_id),
                    sim_idempotency_key=sim_idem,
                )
                if audit.ok:
                    continue

                # Severity heuristic: warning if drift < 1% of tick volume.
                severity = "critical"
                if audit.tick_volume > 0:
                    try:
                        ratio = audit.total_drift / audit.tick_volume
                        if ratio < Decimal("0.01"):
                            severity = "warning"
                    except Exception:
                        severity = "critical"

                rr._logger.warning(
                    "event=post_tick_audit.drift run_id=%s tick=%s eq=%s total_drift=%s severity=%s",
                    str(run_id),
                    int(run.tick_index or 0),
                    str(eq_code),
                    str(audit.total_drift),
                    str(severity),
                )

                # 1) SSE event (best-effort).
                try:
                    if emitter is not None:
                        emitter.emit_audit_drift(
                            run_id=str(run_id),
                            run=run,
                            equivalent=str(eq_code),
                            tick_index=int(run.tick_index or 0),
                            severity=str(severity),
                            total_drift=str(audit.total_drift),
                            drifts=list(audit.drifts or []),
                            source="post_tick_audit",
                        )
                except Exception:
                    rr._logger.warning(
                        "event=post_tick_audit.emit_failed run_id=%s tick=%s eq=%s",
                        str(run_id),
                        int(run.tick_index or 0),
                        str(eq_code),
                        exc_info=True,
                    )

                # 2) IntegrityAuditLog (best-effort).
                try:
                    session.add(
                        IntegrityAuditLog(
                            operation_type="SIMULATOR_AUDIT_DRIFT",
                            tx_id=None,
                            equivalent_code=str(eq_code).strip().upper(),
                            state_checksum_before="",
                            state_checksum_after="",
                            affected_participants={
                                "drifts": list(audit.drifts or []),
                                "tick_index": int(run.tick_index or 0),
                                "source": "post_tick_audit",
                            },
                            invariants_checked={
                                "post_tick_balance": {
                                    "passed": False,
                                    "total_drift": str(audit.total_drift),
                                }
                            },
                            verification_passed=False,
                            error_details={
                                "drifts": list(audit.drifts or []),
                                "severity": str(severity),
                            },
                        )
                    )
                    await session.commit()
                except Exception:
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                    rr._logger.warning(
                        "event=post_tick_audit.persist_failed run_id=%s tick=%s eq=%s",
                        str(run_id),
                        int(run.tick_index or 0),
                        str(eq_code),
                        exc_info=True,
                    )
        except Exception:
            rr._logger.warning(
                "event=post_tick_audit.failed run_id=%s tick=%s",
                str(run_id),
                int(run.tick_index or 0),
                exc_info=True,
            )
