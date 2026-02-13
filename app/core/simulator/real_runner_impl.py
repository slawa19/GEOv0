from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any, Callable

from app.core.simulator.adaptive_clearing_policy import AdaptiveClearingPolicyConfig
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.inject_executor import (
    InjectExecutor,
    invalidate_caches_after_inject as _inject_invalidate_caches_after_inject,
)
from app.core.simulator.models import RunRecord, TrustDriftResult
from app.core.simulator.real_clearing_engine import RealClearingEngine
from app.core.simulator.real_debt_snapshot_loader import RealDebtSnapshotLoader
from app.core.simulator.real_payment_action import _RealPaymentAction
from app.core.simulator.real_payment_planner import RealPaymentPlanner
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
from app.core.simulator.real_tick_metrics import RealTickMetrics
from app.core.simulator.real_tick_orchestrator import RealTickOrchestrator
from app.core.simulator.real_tick_payments_coordinator import RealTickPaymentsCoordinator
from app.core.simulator.real_tick_persistence import RealTickPersistence
from app.core.simulator.real_tick_trust_drift_coordinator import (
    RealTickTrustDriftCoordinator,
)
from app.core.simulator.runtime_utils import (
    safe_float_env as _safe_float_env,
    safe_int_env as _safe_int_env,
    safe_optional_decimal_env as _safe_optional_decimal_env,
    safe_str_env as _safe_str_env,
)
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.core.simulator.trust_drift_engine import TrustDriftEngine


class RealRunnerImpl:
    def __init__(
        self,
        *,
        lock,
        get_run: Callable[[str], RunRecord],
        get_scenario_raw: Callable[[str], dict[str, Any]],
        sse: SseBroadcast,
        artifacts: ArtifactsManager,
        utc_now,
        publish_run_status: Callable[[str], None],
        db_enabled: Callable[[], bool],
        actions_per_tick_max: int,
        clearing_every_n_ticks: int,
        real_max_consec_tick_failures_default: int,
        real_max_timeouts_per_tick_default: int,
        real_max_errors_total_default: int,
        logger: logging.Logger,
    ) -> None:
        self._lock = lock
        self._get_run = get_run
        self._get_scenario_raw = get_scenario_raw
        self._sse = sse
        self._artifacts = artifacts
        self._utc_now = utc_now
        self._publish_run_status = publish_run_status
        self._db_enabled = db_enabled
        self._actions_per_tick_max = int(actions_per_tick_max)
        self._clearing_every_n_ticks = int(clearing_every_n_ticks)
        self._real_max_consec_tick_failures_default = int(
            real_max_consec_tick_failures_default
        )
        self._real_max_timeouts_per_tick_default = int(
            real_max_timeouts_per_tick_default
        )
        self._real_max_errors_total_default = int(real_max_errors_total_default)
        self._logger = logger

        # Cache env-derived limits (avoid getenv on every tick).
        self._real_max_consec_tick_failures_limit = _safe_int_env(
            "SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES",
            int(self._real_max_consec_tick_failures_default),
        )
        self._real_max_timeouts_per_tick_limit = _safe_int_env(
            "SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK",
            int(self._real_max_timeouts_per_tick_default),
        )
        self._real_max_errors_total_limit = _safe_int_env(
            "SIMULATOR_REAL_MAX_ERRORS_TOTAL",
            int(self._real_max_errors_total_default),
        )
        self._clearing_max_depth_limit = _safe_int_env(
            "SIMULATOR_CLEARING_MAX_DEPTH", 6
        )
        self._clearing_max_fx_edges_limit = _safe_int_env(
            "SIMULATOR_CLEARING_MAX_EDGES_FOR_FX", 30
        )
        # Amount cap is opt-in. Default must not override scenario amount_model bounds.
        self._real_amount_cap_limit = _safe_optional_decimal_env(
            "SIMULATOR_REAL_AMOUNT_CAP"
        )
        self._real_enable_inject = (
            int(_safe_int_env("SIMULATOR_REAL_ENABLE_INJECT", 0)) >= 1
        )

        # Cache env-derived throttling knobs (avoid getenv on every tick).
        self._real_db_metrics_every_n_ticks = _safe_int_env(
            "SIMULATOR_REAL_DB_METRICS_EVERY_N_TICKS", 5
        )
        self._real_db_bottlenecks_every_n_ticks = _safe_int_env(
            "SIMULATOR_REAL_DB_BOTTLENECKS_EVERY_N_TICKS", 10
        )
        self._real_last_tick_write_every_ms = _safe_int_env(
            "SIMULATOR_REAL_LAST_TICK_WRITE_EVERY_MS", 500
        )
        self._real_artifacts_sync_every_ms = _safe_int_env(
            "SIMULATOR_REAL_ARTIFACTS_SYNC_EVERY_MS", 5000
        )

        # Clearing loop throttling: keep default behavior, but avoid long event-loop stalls.
        # If budget is exceeded, clearing will continue on the next tick.
        self._real_clearing_time_budget_ms = _safe_int_env(
            "SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS", 250
        )

        # Adaptive clearing policy knobs (§5 of adaptive-clearing-policy-spec).
        self._clearing_policy = _safe_str_env("SIMULATOR_CLEARING_POLICY", "static")
        if self._clearing_policy not in ("static", "adaptive"):
            self._clearing_policy = "static"
        self._adaptive_clearing_config: AdaptiveClearingPolicyConfig | None = None
        if self._clearing_policy == "adaptive":
            self._adaptive_clearing_config = AdaptiveClearingPolicyConfig(
                window_ticks=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_WINDOW_TICKS", 30),
                no_capacity_high=_safe_float_env("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_HIGH", 0.60),
                no_capacity_low=_safe_float_env("SIMULATOR_CLEARING_ADAPTIVE_NO_CAPACITY_LOW", 0.30),
                min_interval_ticks=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_MIN_INTERVAL_TICKS", 5),
                backoff_max_interval_ticks=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_BACKOFF_MAX_INTERVAL_TICKS", 60),
                time_budget_ms_min=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MIN", 50),
                time_budget_ms_max=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_TIME_BUDGET_MS_MAX", 250),
                max_depth_min=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MIN", 3),
                max_depth_max=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_MAX_DEPTH_MAX", 6),
                inflight_threshold=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_INFLIGHT_THRESHOLD", 0),
                queue_depth_threshold=_safe_int_env("SIMULATOR_CLEARING_ADAPTIVE_QUEUE_DEPTH_THRESHOLD", 0),
                global_max_depth_ceiling=int(self._clearing_max_depth_limit),
                global_time_budget_ms_ceiling=int(self._real_clearing_time_budget_ms),
                warmup_fallback_cadence=int(self._clearing_every_n_ticks),
            )

        # Sub-components: eager init (RealRunner is created once on startup).
        self._edge_patch_builder: EdgePatchBuilder = EdgePatchBuilder(logger=self._logger)
        self._real_debt_snapshot_loader: RealDebtSnapshotLoader = RealDebtSnapshotLoader()
        self._sse_emitter: SseEventEmitter = SseEventEmitter(
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
        )

        self._real_payment_planner: RealPaymentPlanner = RealPaymentPlanner(
            actions_per_tick_max=int(self._actions_per_tick_max),
            amount_cap_limit=self._real_amount_cap_limit,
            logger=self._logger,
            action_factory=lambda seq, eq, sender_pid, receiver_pid, amount: _RealPaymentAction(
                seq=int(seq),
                equivalent=str(eq),
                sender_pid=str(sender_pid),
                receiver_pid=str(receiver_pid),
                amount=str(amount),
            ),
        )

        self._real_payments_executor: RealPaymentsExecutor = RealPaymentsExecutor(
            lock=self._lock,
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            edge_patch_builder=self._edge_patch_builder,
            should_warn_this_tick=self._should_warn_this_tick,
            sim_idempotency_key=self._sim_idempotency_key,
        )

        self._trust_drift_engine: TrustDriftEngine = TrustDriftEngine(
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            get_scenario_raw=self._get_scenario_raw,
        )

        self._inject_executor: InjectExecutor = InjectExecutor(
            sse=self._sse,
            artifacts=self._artifacts,
            utc_now=self._utc_now,
            logger=self._logger,
        )

        self._real_clearing_engine: RealClearingEngine = RealClearingEngine(
            lock=self._lock,
            sse=self._sse,
            utc_now=self._utc_now,
            logger=self._logger,
            edge_patch_builder=self._edge_patch_builder,
            clearing_max_depth_limit=int(self._clearing_max_depth_limit),
            clearing_max_fx_edges_limit=int(self._clearing_max_fx_edges_limit),
            real_clearing_time_budget_ms=int(self._real_clearing_time_budget_ms),
            should_warn_this_tick=lambda run, key: self._should_warn_this_tick(run, key=key),
        )

        self._real_tick_persistence: RealTickPersistence = RealTickPersistence(
            lock=self._lock,
            artifacts=self._artifacts,
            utc_now=self._utc_now,
            db_enabled=self._db_enabled,
            logger=self._logger,
            real_db_metrics_every_n_ticks=int(self._real_db_metrics_every_n_ticks),
            real_db_bottlenecks_every_n_ticks=int(self._real_db_bottlenecks_every_n_ticks),
            real_last_tick_write_every_ms=int(self._real_last_tick_write_every_ms),
            real_artifacts_sync_every_ms=int(self._real_artifacts_sync_every_ms),
        )

        self._real_tick_metrics: RealTickMetrics = RealTickMetrics(
            lock=self._lock,
            logger=self._logger,
            real_db_metrics_every_n_ticks=int(self._real_db_metrics_every_n_ticks),
        )

        self._real_tick_clearing_coordinator: RealTickClearingCoordinator = (
            RealTickClearingCoordinator(
                lock=self._lock,
                logger=self._logger,
                clearing_every_n_ticks=int(self._clearing_every_n_ticks),
                real_clearing_time_budget_ms=int(self._real_clearing_time_budget_ms),
                clearing_policy=self._clearing_policy,  # type: ignore[arg-type]
                adaptive_config=self._adaptive_clearing_config,
            )
        )
        self._real_tick_trust_drift_coordinator: RealTickTrustDriftCoordinator = (
            RealTickTrustDriftCoordinator(logger=self._logger)
        )
        self._real_tick_payments_coordinator: RealTickPaymentsCoordinator = (
            RealTickPaymentsCoordinator(lock=self._lock, logger=self._logger)
        )
        self._real_scenario_seeder: RealScenarioSeeder = RealScenarioSeeder()

        self._real_tick_orchestrator: RealTickOrchestrator = RealTickOrchestrator(self)

    def _parse_event_time_ms(self, evt: Any) -> int | None:
        if not isinstance(evt, dict):
            return None
        t = evt.get("time")
        if isinstance(t, int):
            return max(0, int(t))
        # MVP: token-based times are future.
        return None

    def _compute_stress_multipliers(
        self,
        *,
        events: Any,
        sim_time_ms: int,
    ) -> tuple[float, dict[str, float], dict[str, float]]:
        return self._real_payment_planner.compute_stress_multipliers(
            events=events,
            sim_time_ms=sim_time_ms,
        )

    async def _apply_due_scenario_events(
        self, session, *, run_id: str, run: RunRecord, scenario: dict[str, Any]
    ) -> None:
        events = scenario.get("events")
        if not isinstance(events, list) or not events:
            return

        # Build pid->id map once.
        pid_to_participant_id: dict[str, uuid.UUID] = {}
        if run._real_participants:
            pid_to_participant_id = {
                str(pid): participant_id
                for (participant_id, pid) in run._real_participants
            }

        for idx, evt in enumerate(events):
            if idx in run._real_fired_scenario_event_indexes:
                continue

            t0 = self._parse_event_time_ms(evt)
            if t0 is None or int(run.sim_time_ms) < int(t0):
                continue

            evt_type = str((evt or {}).get("type") or "").strip()

            if evt_type == "note":
                payload = {
                    "type": "note",
                    "ts": self._utc_now().isoformat(),
                    "sim_time_ms": int(run.sim_time_ms),
                    "tick_index": int(run.tick_index),
                    "scenario": {
                        "event_index": int(idx),
                        "time": t0,
                        "description": str((evt or {}).get("description") or ""),
                        "metadata": (
                            (evt or {}).get("metadata")
                            if isinstance((evt or {}).get("metadata"), dict)
                            else None
                        ),
                    },
                }
                self._artifacts.enqueue_event_artifact(run_id, payload)
                run._real_fired_scenario_event_indexes.add(idx)
                continue

            if evt_type == "inject":
                await self._inject_executor.apply_inject_event(
                    session,
                    run_id=run_id,
                    run=run,
                    scenario=scenario,
                    event_index=idx,
                    event_time_ms=t0,
                    event=evt,
                    pid_to_participant_id=pid_to_participant_id,
                    inject_enabled=bool(self._real_enable_inject),
                    build_edge_patch_for_equivalent=self._build_edge_patch_for_equivalent,
                    broadcast_topology_edge_patch=self._broadcast_topology_edge_patch,
                )
                continue

            # Unknown / unsupported event types are ignored, but we still mark them fired once due.
            run._real_fired_scenario_event_indexes.add(idx)

    async def _apply_inject_event(
        self,
        session,
        *,
        run_id: str,
        run: RunRecord,
        scenario: dict[str, Any],
        event_index: int,
        event_time_ms: int,
        event: dict[str, Any] | None,
        pid_to_participant_id: dict[str, uuid.UUID],
    ) -> None:
        await self._inject_executor.apply_inject_event(
            session,
            run_id=run_id,
            run=run,
            scenario=scenario,
            event_index=event_index,
            event_time_ms=event_time_ms,
            event=event,
            pid_to_participant_id=pid_to_participant_id,
            inject_enabled=bool(self._real_enable_inject),
            build_edge_patch_for_equivalent=self._build_edge_patch_for_equivalent,
            broadcast_topology_edge_patch=self._broadcast_topology_edge_patch,
        )

    def _invalidate_caches_after_inject(
        self,
        *,
        run: RunRecord,
        scenario: dict[str, Any],
        affected_equivalents: set[str],
        new_participants: list[tuple[uuid.UUID, str]],
        new_participants_scenario: list[dict[str, Any]],
        new_trustlines_scenario: list[dict[str, Any]],
        frozen_pids: list[str],
    ) -> None:
        _inject_invalidate_caches_after_inject(
            logger=self._logger,
            run=run,
            scenario=scenario,
            affected_equivalents=affected_equivalents,
            new_participants=new_participants,
            new_participants_scenario=new_participants_scenario,
            new_trustlines_scenario=new_trustlines_scenario,
            frozen_pids=frozen_pids,
        )

    async def _build_edge_patch_for_equivalent(
        self,
        *,
        session,
        run: RunRecord,
        equivalent_code: str,
        only_edges: set[tuple[str, str]] | None = None,
        include_width_keys: bool = True,
    ) -> list[dict[str, Any]]:
        return await self._edge_patch_builder.build_edge_patch_for_equivalent(
            session=session,
            run=run,
            equivalent_code=equivalent_code,
            only_edges=only_edges,
            include_width_keys=include_width_keys,
        )

    def _broadcast_topology_edge_patch(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        edge_patch: list[dict[str, Any]],
        reason: str,
    ) -> None:
        self._sse_emitter.emit_topology_edge_patch(
            run_id=run_id,
            run=run,
            equivalent=equivalent,
            edge_patch=edge_patch,
            reason=reason,
        )

    def _should_warn_this_tick(self, run: RunRecord, *, key: str) -> bool:
        with self._lock:
            tick = int(run.tick_index)
            if int(run._real_warned_tick) != tick:
                run._real_warned_tick = tick
                run._real_warned_keys.clear()

            if key in run._real_warned_keys:
                return False
            run._real_warned_keys.add(key)
            return True

    def _init_trust_drift(self, run: RunRecord, scenario: dict[str, Any]) -> None:
        self._trust_drift_engine.init_trust_drift(run, scenario)

    async def _apply_trust_growth(
        self,
        run: RunRecord,
        clearing_session,
        touched_edges: set[tuple[str, str]],
        eq_code: str,
        tick_index: int,
        cleared_amount_per_edge: dict[tuple[str, str], float],
    ) -> TrustDriftResult:
        return await self._trust_drift_engine.apply_trust_growth(
            run,
            clearing_session,
            touched_edges,
            eq_code,
            tick_index,
            cleared_amount_per_edge,
        )

    async def _apply_trust_decay(
        self,
        run: RunRecord,
        session,
        tick_index: int,
        debt_snapshot: dict[tuple[str, str, str], Decimal],
        scenario: dict[str, Any],
    ) -> TrustDriftResult:
        return await self._trust_drift_engine.apply_trust_decay(
            run,
            session,
            tick_index,
            debt_snapshot,
            scenario,
        )

    async def flush_pending_storage(self, run_id: str) -> None:
        await self._real_tick_orchestrator.flush_pending_storage(run_id)

    async def tick_real_mode(self, run_id: str) -> None:
        await self._real_tick_orchestrator.tick_real_mode(run_id)

    async def fail_run(self, run_id: str, *, code: str, message: str) -> None:
        await self._real_tick_orchestrator.fail_run(run_id, code=code, message=message)

    async def tick_real_mode_clearing(
        self,
        session,  # NOTE: Unused now; clearing uses its own isolated session
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
        *,
        async_session_local: Any | None = None,
        clearing_service_cls: Any | None = None,
        time_budget_ms_override: int | None = None,
        max_depth_override: int | None = None,
    ) -> dict[str, float]:
        return await self._real_clearing_engine.tick_real_mode_clearing(
            session,
            run_id=run_id,
            run=run,
            equivalents=equivalents,
            apply_trust_growth=self._trust_drift_engine.apply_trust_growth,
            build_edge_patch_for_equivalent=self._build_edge_patch_for_equivalent,
            broadcast_topology_edge_patch=self._broadcast_topology_edge_patch,
            async_session_local=async_session_local,
            clearing_service_cls=clearing_service_cls,
            time_budget_ms_override=time_budget_ms_override,
            max_depth_override=max_depth_override,
        )

    def _plan_real_payments(
        self,
        run: RunRecord,
        scenario: dict[str, Any],
        *,
        debt_snapshot: dict[tuple[str, str, str], Decimal] | None = None,
    ) -> list[_RealPaymentAction]:
        return self._real_payment_planner.plan_payments(
            run,
            scenario,
            debt_snapshot=debt_snapshot,
        )

    def _sim_idempotency_key(
        self,
        *,
        run_id: str,
        tick_ms: int,
        sender_pid: str,
        receiver_pid: str,
        equivalent: str,
        amount: str,
        seq: int,
    ) -> str:
        material = f"{run_id}|{tick_ms}|{sender_pid}|{receiver_pid}|{equivalent}|{amount}|{seq}"
        return "sim:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    async def _load_real_participants(
        self, session, scenario: dict[str, Any]
    ) -> list[tuple[uuid.UUID, str]]:
        return await self._real_scenario_seeder.load_real_participants(
            session=session,
            scenario=scenario,
        )

    async def _load_debt_snapshot_by_pid(
        self,
        session,
        participants: list[tuple[uuid.UUID, str]],
        equivalents: list[str],
    ) -> dict[tuple[str, str, str], Decimal]:
        return await self._real_debt_snapshot_loader.load_debt_snapshot_by_pid(
            session=session,
            participants=participants,
            equivalents=equivalents,
        )

    async def _seed_scenario_into_db(self, session, scenario: dict[str, Any]) -> None:
        await self._real_scenario_seeder.seed_scenario_into_db(
            session=session,
            scenario=scenario,
        )
