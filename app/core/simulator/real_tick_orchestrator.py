from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import app.db.session as db_session
import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.simulator.post_tick_audit import audit_tick_balance
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime_utils import safe_int_env as _safe_int_env
from app.db.models.audit_log import IntegrityAuditLog


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
    _sse_emitter: "object"
    _trust_drift_engine: "TrustDriftEngine"

    _real_max_timeouts_per_tick_limit: int
    _real_max_errors_total_limit: int
    _real_max_consec_tick_failures_limit: int

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None: ...
    async def tick_real_mode_clearing(self, session, run_id: str, run: RunRecord, equivalents: list[str], *, time_budget_ms_override: int | None = None, max_depth_override: int | None = None) -> dict[str, float]: ...

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

        # Bounded grace: if clearing is still running from the previous tick,
        # wait a bit so payments don't race it and cause lost updates.
        try:
            hard_timeout = rr._real_tick_clearing_coordinator.compute_static_clearing_hard_timeout_sec(
                safe_int_env=_safe_int_env
            )
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
        if task is not None:
            task.cancel()

        if clearing_task is not None:
            try:
                clearing_task.cancel()
            except Exception:
                pass
            # Best-effort: await cancellation so we don't leave a background task
            # running after transitioning the run into error.
            try:
                await asyncio.wait_for(clearing_task, timeout=1.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

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

        # CRITICAL: prevent payments from racing an unfinished background clearing
        # from the previous tick (lost updates under Postgres).
        await self._await_pending_clearing(run_id, run=run)

        try:
            async with db_session.AsyncSessionLocal() as session:
                try:
                    if not run._real_seeded:
                        with rr._lock:
                            if run._real_seeding_lock is None:
                                run._real_seeding_lock = asyncio.Lock()
                            seeding_lock = run._real_seeding_lock

                        async with seeding_lock:
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
                        run_clearing_for_eq=lambda eq, *, time_budget_ms_override=None, max_depth_override=None: rr.tick_real_mode_clearing(
                            session, run_id, run, [eq],
                            time_budget_ms_override=time_budget_ms_override,
                            max_depth_override=max_depth_override,
                        ),
                        payments_result=payments_phase,
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

                    # ── Post-tick audit (best-effort): detect participant drift ──
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
        *,
        time_budget_ms_override: int | None = None,
        max_depth_override: int | None = None,
    ) -> dict[str, float]:
        rr = self._runner
        return await rr.tick_real_mode_clearing(
            session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            time_budget_ms_override=time_budget_ms_override,
            max_depth_override=max_depth_override,
        )
