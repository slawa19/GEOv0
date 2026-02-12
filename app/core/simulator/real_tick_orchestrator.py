from __future__ import annotations

import time
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import app.db.session as db_session
import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime_utils import safe_int_env as _safe_int_env


if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from app.core.simulator.real_payments_executor import RealPaymentsExecutor
    from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
    from app.core.simulator.real_tick_metrics import RealTickMetrics
    from app.core.simulator.real_tick_payments_coordinator import RealTickPaymentsCoordinator
    from app.core.simulator.real_tick_persistence import RealTickPersistence
    from app.core.simulator.real_tick_trust_drift_coordinator import RealTickTrustDriftCoordinator
    from app.core.simulator.trust_drift_engine import TrustDriftEngine


class _RealRunnerPort(Protocol):
    _lock: object
    _logger: "logging.Logger"
    _utc_now: "Callable[[], object]"
    _publish_run_status: "Callable[[str], None]"

    _get_run: "Callable[[str], RunRecord]"
    _get_scenario_raw: "Callable[[str], dict]"

    _real_tick_persistence: "RealTickPersistence"
    _real_tick_metrics: "RealTickMetrics"
    _real_tick_clearing_coordinator: "RealTickClearingCoordinator"
    _real_tick_trust_drift_coordinator: "RealTickTrustDriftCoordinator"
    _real_tick_payments_coordinator: "RealTickPaymentsCoordinator"
    _real_payments_executor: "RealPaymentsExecutor"
    _trust_drift_engine: "TrustDriftEngine"

    _real_max_timeouts_per_tick_limit: int
    _real_max_errors_total_limit: int
    _real_max_consec_tick_failures_limit: int

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None: ...
    async def tick_real_mode_clearing(self, session, run_id: str, run: RunRecord, equivalents: list[str]) -> dict[str, float]: ...

    async def _seed_scenario_into_db(self, session, scenario: dict) -> None: ...
    async def _load_real_participants(self, session, scenario: dict) -> list[tuple]: ...
    async def _apply_due_scenario_events(self, session, *, run_id: str, run: RunRecord, scenario: dict) -> None: ...
    async def _load_debt_snapshot_by_pid(self, session, participants: list[tuple], equivalents: list[str]) -> dict: ...
    def _plan_real_payments(self, run: RunRecord, scenario: dict, *, debt_snapshot=None): ...
    async def _build_edge_patch_for_equivalent(self, *, session, run: RunRecord, equivalent_code: str, only_edges=None, include_width_keys: bool = True): ...
    def _broadcast_topology_edge_patch(self, *, run_id: str, run: RunRecord, equivalent: str, edge_patch: list[dict], reason: str) -> None: ...
    def _should_warn_this_tick(self, run: RunRecord, *, key: str) -> bool: ...


class RealTickOrchestrator:
    def __init__(self, runner: _RealRunnerPort) -> None:
        self._runner = runner

    async def flush_pending_storage(self, run_id: str) -> None:
        rr = self._runner
        run = rr._get_run(run_id)
        await rr._real_tick_persistence.flush_pending_storage(
            run_id=run_id,
            run=run,
        )

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None:
        rr = self._runner
        run = rr._get_run(run_id)
        task = None
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
        if task is not None:
            task.cancel()

    async def tick_real_mode(self, run_id: str) -> None:
        rr = self._runner

        run: RunRecord = rr._get_run(run_id)
        scenario = getattr(run, "_scenario_raw", None) or rr._get_scenario_raw(run.scenario_id)

        tick_t0 = time.monotonic()
        # NOTE: keep this at DEBUG to avoid log spam; we add WARNING logs around
        # potentially blocking stages (clearing/commit).
        rr._logger.debug(
            "simulator.real.tick_start run_id=%s tick=%s sim_time_ms=%s",
            str(run.run_id),
            int(run.tick_index or 0),
            int(run.sim_time_ms or 0),
        )

        try:
            async with db_session.AsyncSessionLocal() as session:
                try:
                    if not run._real_seeded:
                        await rr._seed_scenario_into_db(session, scenario)
                        await session.commit()
                        run._real_seeded = True

                    if run._real_participants is None or run._real_equivalents is None:
                        run._real_participants = await rr._load_real_participants(
                            session, scenario
                        )
                        run._real_equivalents = [
                            str(x)
                            for x in (scenario.get("equivalents") or [])
                            if str(x).strip()
                        ]

                    participants = run._real_participants or []
                    equivalents = run._real_equivalents or []
                    if len(participants) < 2 or not equivalents:
                        return

                    # Initialize trust drift (once per run)
                    if run._trust_drift_config is None:
                        rr._trust_drift_engine.init_trust_drift(run, scenario)

                    # Apply due scenario timeline events (note/stress/inject). Best-effort.
                    # IMPORTANT: inject modifies DB state and must happen before payments.
                    await rr._apply_due_scenario_events(
                        session, run_id=run_id, run=run, scenario=scenario
                    )

                    payments_phase, should_stop = await rr._real_tick_payments_coordinator.run_payments_phase(
                        session=session,
                        run_id=run_id,
                        run=run,
                        scenario=scenario,
                        participants=participants,
                        equivalents=equivalents,
                        load_debt_snapshot_by_pid=rr._load_debt_snapshot_by_pid,
                        plan_payments=lambda _run, _scenario, _debt_snapshot: rr._plan_real_payments(
                            _run, _scenario, debt_snapshot=_debt_snapshot
                        ),
                        payments_executor=rr._real_payments_executor,
                        max_in_flight=int(run._real_max_in_flight),
                        max_timeouts_per_tick=int(rr._real_max_timeouts_per_tick_limit),
                        max_errors_total=int(rr._real_max_errors_total_limit),
                        fail_run=lambda _run_id, code, message: rr.fail_run(
                            _run_id, code=code, message=message
                        ),
                    )
                    if should_stop:
                        return

                    debt_snapshot = payments_phase.debt_snapshot
                    planned = payments_phase.planned
                    per_eq_metric_values = payments_phase.per_eq_metric_values

                    committed = payments_phase.committed
                    rejected = payments_phase.rejected
                    errors = payments_phase.errors
                    timeouts = payments_phase.timeouts

                    per_eq = payments_phase.per_eq
                    per_eq_route = payments_phase.per_eq_route
                    per_eq_edge_stats = payments_phase.per_eq_edge_stats

                    # Best-effort clearing (optional MVP): once in a while, attempt clearing per equivalent.
                    clearing_volume_by_eq = await rr._real_tick_clearing_coordinator.maybe_run_clearing(
                        session=session,
                        run_id=run_id,
                        run=run,
                        equivalents=equivalents,
                        planned_len=len(planned or []),
                        tick_t0=tick_t0,
                        clearing_enabled=bool(getattr(settings, "CLEARING_ENABLED", True)),
                        safe_int_env=_safe_int_env,
                        run_clearing=lambda: rr.tick_real_mode_clearing(
                            session, run_id, run, equivalents
                        ),
                    )

                    # ── Trust Drift: decay overloaded edges ─────────────
                    await rr._real_tick_trust_drift_coordinator.apply_trust_decay_and_broadcast(
                        session=session,
                        run_id=run_id,
                        run=run,
                        tick_index=int(run.tick_index or 0),
                        debt_snapshot=debt_snapshot,
                        scenario=scenario,
                        trust_drift_engine=rr._trust_drift_engine,
                        build_edge_patch_for_equivalent=rr._build_edge_patch_for_equivalent,
                        broadcast_topology_edge_patch=rr._broadcast_topology_edge_patch,
                    )

                    await rr._real_tick_metrics.populate_per_eq_metric_values(
                        session=session,
                        run=run,
                        scenario=scenario,
                        equivalents=equivalents,
                        per_eq_route=per_eq_route,
                        clearing_volume_by_eq=clearing_volume_by_eq,
                        per_eq_metric_values=per_eq_metric_values,
                        should_warn=lambda key: rr._should_warn_this_tick(run, key=key),
                    )

                    await rr._real_tick_persistence.persist_tick_tail(
                        session=session,
                        run=run,
                        equivalents=equivalents,
                        tick_t0=tick_t0,
                        planned_len=len(planned),
                        committed=committed,
                        rejected=rejected,
                        errors=errors,
                        timeouts=timeouts,
                        per_eq=per_eq,
                        per_eq_metric_values=per_eq_metric_values,
                        per_eq_edge_stats=per_eq_edge_stats,
                    )
                except Exception:
                    # CRITICAL: always rollback on tick failure.
                    # Otherwise the underlying pooled connection can be returned
                    # in an invalid transaction state, and subsequent ticks may
                    # fail with "Can't reconnect until invalid transaction is rolled back".
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                    raise
        except Exception as e:
            rr._logger.warning(
                "simulator.real.tick_failed run_id=%s tick=%s",
                str(run.run_id),
                int(run.tick_index or 0),
                exc_info=True,
            )
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

    async def tick_real_mode_clearing(
        self,
        session,  # NOTE: Unused now; clearing uses its own isolated session
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
    ) -> dict[str, float]:
        rr = self._runner
        return await rr.tick_real_mode_clearing(
            session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
        )
