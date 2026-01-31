from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Optional
from pathlib import Path

from app.schemas.simulator import (
    BottlenecksResponse,
    MetricsResponse,
    RunMode,
    RunStatus,
    ScenarioSummary,
    SimulatorGraphSnapshot,
    SimulatorRunStatusEvent,
)
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.fixtures_runner import FixturesRunner
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.core.simulator.models import RunRecord, ScenarioRecord, _Subscription
from app.core.simulator.real_runner import RealRunner
from app.core.simulator.run_lifecycle import RunLifecycle
from app.core.simulator.runtime_utils import (
    ACTIONS_PER_TICK_MAX,
    CLEARING_EVERY_N_TICKS,
    FIXTURES_DIR,
    REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT,
    REAL_MAX_ERRORS_TOTAL_DEFAULT,
    REAL_MAX_IN_FLIGHT_DEFAULT,
    REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT,
    SCENARIO_SCHEMA_PATH,
    TICK_MS_BASE,
    dict_to_last_error as _dict_to_last_error,
    edges_by_equivalent as _edges_by_equivalent,
    local_state_dir as _local_state_dir,
    new_run_id as _new_run_id,
    run_to_status as _run_to_status,
    utc_now as _utc_now,
)
from app.core.simulator.scenario_registry import ScenarioRegistry
from app.core.simulator.snapshot_builder import SnapshotBuilder, scenario_to_snapshot
import app.core.simulator.storage as simulator_storage
from app.core.simulator.sse_broadcast import SseBroadcast
from app.utils.exceptions import NotFoundException
from app.utils.exceptions import ConflictException

logger = logging.getLogger(__name__)


def _scenario_allowlist() -> Optional[set[str]]:
    """Allowlist for preset scenarios shown to the UI.

    - If SIMULATOR_SCENARIO_ALLOWLIST is unset: show only the canonical demo presets.
    - If set to '*' or 'all': disable filtering.
    - If set to comma-separated ids: show only those.
    """

    raw = str(os.environ.get("SIMULATOR_SCENARIO_ALLOWLIST", "")).strip()
    if raw:
        if raw in {"*", "all", "ALL"}:
            return None
        items = {x.strip() for x in raw.split(",") if x.strip()}
        return items or None

    return {"greenfield-village-100", "riverside-town-50"}

def _safe_int_env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, str(default)) or str(default))
        return v
    except Exception:
        return int(default)


class _SimulatorRuntimeBase:
    """In-process simulator runtime (MVP).

    Provides:
    - Scenario registry (fixtures + uploaded)
    - Runs registry
    - SSE-friendly pub/sub per run

    NOTE: This is intentionally in-memory and best-effort for MVP.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scenarios: dict[str, ScenarioRecord] = {}
        self._runs: dict[str, RunRecord] = {}
        self._active_run_id: Optional[str] = None

        self._is_shutting_down = False

        # Runtime knobs (env-configurable; defaults preserve existing behavior).
        self._tick_ms_base = _safe_int_env("SIMULATOR_TICK_MS_BASE", TICK_MS_BASE)
        self._actions_per_tick_max = _safe_int_env("SIMULATOR_ACTIONS_PER_TICK_MAX", ACTIONS_PER_TICK_MAX)
        self._clearing_every_n_ticks = _safe_int_env("SIMULATOR_CLEARING_EVERY_N_TICKS", CLEARING_EVERY_N_TICKS)
        self._max_active_runs = _safe_int_env("SIMULATOR_MAX_ACTIVE_RUNS", 1)
        self._max_run_records = _safe_int_env("SIMULATOR_MAX_RUN_RECORDS", 200)

        # Local artifact retention (0 disables).
        self._artifacts_ttl_hours = _safe_int_env("SIMULATOR_ARTIFACTS_TTL_HOURS", 0)

        self._artifacts = ArtifactsManager(
            lock=self._lock,
            runs=self._runs,
            local_state_dir=_local_state_dir,
            utc_now=_utc_now,
            db_enabled=simulator_storage.db_enabled,
            logger=logger,
        )

        self._metrics_bottlenecks = MetricsBottlenecks(
            lock=self._lock,
            runs=self._runs,
            scenarios=self._scenarios,
            utc_now=_utc_now,
            db_enabled=simulator_storage.db_enabled,
            logger=logger,
        )

        self._snapshot_builder = SnapshotBuilder(
            lock=self._lock,
            runs=self._runs,
            scenarios=self._scenarios,
            utc_now=_utc_now,
            db_enabled=simulator_storage.db_enabled,
        )

        self._scenario_registry = ScenarioRegistry(
            lock=self._lock,
            scenarios=self._scenarios,
            fixtures_dir=FIXTURES_DIR,
            schema_path=SCENARIO_SCHEMA_PATH,
            local_state_dir=_local_state_dir(),
            utc_now=_utc_now,
            logger=logger,
        )
        self._scenario_registry.load_all()

        # Best-effort cleanup of old local artifacts (keeps dev dirs from growing forever).
        try:
            self._artifacts.cleanup_old_runs(ttl_hours=self._artifacts_ttl_hours)
        except Exception:
            logger.exception("simulator.artifacts.cleanup_failed")

        # SSE replay buffer sizing/TTL. Best-effort; does not change OpenAPI.
        self._event_buffer_max = _safe_int_env("SIMULATOR_EVENT_BUFFER_SIZE", 2000)
        self._event_buffer_ttl_sec = _safe_int_env("SIMULATOR_EVENT_BUFFER_TTL_SEC", 600)
        self._sse_sub_queue_max = _safe_int_env("SIMULATOR_SSE_SUB_QUEUE_MAX", 500)

        # If enabled, SSE endpoints may return 410 when Last-Event-ID is too old
        # to be replayed from the in-memory ring buffer.
        self._sse_strict_replay = bool(_safe_int_env("SIMULATOR_SSE_STRICT_REPLAY", 0))

        self._sse = SseBroadcast(
            lock=self._lock,
            runs=self._runs,
            get_event_buffer_max=lambda: int(self._event_buffer_max),
            get_event_buffer_ttl_sec=lambda: int(self._event_buffer_ttl_sec),
            get_sub_queue_max=lambda: int(self._sse_sub_queue_max),
            enqueue_event_artifact=self._artifacts.enqueue_event_artifact,
            logger=logger,
        )

        self._run_lifecycle = RunLifecycle(
            lock=self._lock,
            runs=self._runs,
            set_active_run_id=lambda run_id: setattr(self, "_active_run_id", run_id),
            utc_now=_utc_now,
            new_run_id=_new_run_id,
            get_scenario_raw=lambda scenario_id: self.get_scenario(scenario_id).raw,
            edges_by_equivalent=_edges_by_equivalent,
            artifacts=self._artifacts,
            sse=self._sse,
            heartbeat_loop=self._heartbeat_loop,
            publish_run_status=self.publish_run_status,
            run_to_status=_run_to_status,
            get_run_status_payload_json=lambda run_id: self.get_run_status(run_id).model_dump(mode="json"),
            real_max_in_flight_default=REAL_MAX_IN_FLIGHT_DEFAULT,
            get_max_active_runs=lambda: int(self._max_active_runs),
            get_max_run_records=lambda: int(self._max_run_records),
            logger=logger,
        )

        self._fixtures_runner = FixturesRunner(
            lock=self._lock,
            get_run=self.get_run,
            sse=self._sse,
            utc_now=_utc_now,
        )

        self._real_runner = RealRunner(
            lock=self._lock,
            get_run=self.get_run,
            get_scenario_raw=lambda scenario_id: self.get_scenario(scenario_id).raw,
            sse=self._sse,
            artifacts=self._artifacts,
            utc_now=_utc_now,
            publish_run_status=self.publish_run_status,
            db_enabled=simulator_storage.db_enabled,
            actions_per_tick_max=self._actions_per_tick_max,
            clearing_every_n_ticks=self._clearing_every_n_ticks,
            real_max_consec_tick_failures_default=REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT,
            real_max_timeouts_per_tick_default=REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT,
            real_max_errors_total_default=REAL_MAX_ERRORS_TOTAL_DEFAULT,
            logger=logger,
        )

    async def shutdown(self) -> None:
        """Best-effort graceful shutdown.

        Cancels heartbeat tasks, stops artifact writers, and clears subscriptions.
        """

        run_ids: list[str]
        with self._lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True
            run_ids = list(self._runs.keys())

        # Stop runs sequentially to avoid amplifying load on shutdown.
        for run_id in run_ids:
            try:
                await self.stop(run_id)
            except Exception:
                logger.exception("simulator.runtime.shutdown_stop_failed run_id=%s", run_id)

        # Ensure subscriptions are cleared even if individual streams didn't unsubscribe.
        with self._lock:
            for run in self._runs.values():
                try:
                    run._subs.clear()
                except Exception:
                    pass

    def is_sse_strict_replay_enabled(self) -> bool:
        return self._sse_strict_replay

    def is_replay_too_old(self, *, run_id: str, after_event_id: str) -> bool:
        """Returns True if after_event_id is older than the oldest event in buffer.

        This is best-effort and only applies to standard runtime event ids.
        """

        return self._sse.is_replay_too_old(run_id=run_id, after_event_id=after_event_id)

    # -----------------------------
    # Scenarios
    # -----------------------------

    def list_scenarios(self) -> list[ScenarioSummary]:
        allow = _scenario_allowlist()
        with self._lock:
            items = sorted(self._scenarios.values(), key=lambda s: s.scenario_id)
        if allow is not None:
            items = [s for s in items if s.scenario_id in allow]
        return [s.summary() for s in items]

    def get_scenario(self, scenario_id: str) -> ScenarioRecord:
        with self._lock:
            rec = self._scenarios.get(scenario_id)
        if rec is None:
            raise NotFoundException(f"Scenario {scenario_id} not found")
        return rec

    def save_uploaded_scenario(self, scenario: dict[str, Any]) -> ScenarioRecord:
        return self._scenario_registry.save_uploaded_scenario(scenario)

    async def build_scenario_preview(
        self,
        *,
        scenario_id: str,
        equivalent: str,
        mode: RunMode,
        session=None,
    ) -> SimulatorGraphSnapshot:
        """Build a graph preview without starting a run.

        - mode=fixtures: topology-only preview (no DB enrichment)
        - mode=real: best-effort DB enrichment (balances/debts -> viz_*)
        """

        rec = self.get_scenario(scenario_id)
        snap = scenario_to_snapshot(rec.raw, equivalent=equivalent, utc_now=_utc_now)
        if mode != "real":
            return snap
        return await self._snapshot_builder.enrich_snapshot_from_db_if_enabled(snap, equivalent=equivalent, session=session)

    # -----------------------------
    # Runs
    # -----------------------------

    def get_active_run_id(self) -> Optional[str]:
        with self._lock:
            return self._active_run_id

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    def get_run_status(self, run_id: str) -> RunStatus:
        run = self.get_run(run_id)
        return _run_to_status(run)

    async def create_run(self, *, scenario_id: str, mode: RunMode, intensity_percent: int) -> str:
        with self._lock:
            if self._is_shutting_down:
                raise ConflictException("Simulator runtime is shutting down")
        return await self._run_lifecycle.create_run(
            scenario_id=scenario_id,
            mode=mode,
            intensity_percent=intensity_percent,
        )

    async def build_metrics(
        self,
        *,
        run_id: str,
        equivalent: str,
        from_ms: int,
        to_ms: int,
        step_ms: int,
    ) -> MetricsResponse:
        return await self._metrics_bottlenecks.build_metrics(
            run_id=run_id,
            equivalent=equivalent,
            from_ms=from_ms,
            to_ms=to_ms,
            step_ms=step_ms,
        )

    async def build_bottlenecks(
        self,
        *,
        run_id: str,
        equivalent: str,
        limit: int,
        min_score: Optional[float],
    ) -> BottlenecksResponse:
        return await self._metrics_bottlenecks.build_bottlenecks(
            run_id=run_id,
            equivalent=equivalent,
            limit=limit,
            min_score=min_score,
        )

    async def list_artifacts(self, *, run_id: str):
        return await self._artifacts.list_artifacts(run_id=run_id)

    def get_artifact_path(self, *, run_id: str, name: str) -> Path:
        return self._artifacts.get_artifact_path(run_id=run_id, name=name)

    def publish_run_status(self, run_id: str) -> None:
        run = self.get_run(run_id)
        payload = SimulatorRunStatusEvent(
            event_id=self._sse.next_event_id(run),
            ts=_utc_now(),
            type="run_status",
            run_id=run.run_id,
            scenario_id=run.scenario_id,
            state=run.state,
            sim_time_ms=run.sim_time_ms,
            intensity_percent=run.intensity_percent,
            ops_sec=run.ops_sec,
            queue_depth=run.queue_depth,
            last_event_type=run.last_event_type,
            current_phase=run.current_phase,
            last_error=_dict_to_last_error(run.last_error),
        ).model_dump(mode="json")
        self._sse.broadcast(run_id, payload)

    async def pause(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.pause(run_id)

    async def resume(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.resume(run_id)

    async def stop(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.stop(run_id)

    async def restart(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.restart(run_id)

    async def set_intensity(self, run_id: str, intensity_percent: int) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            run.intensity_percent = int(intensity_percent)

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return _run_to_status(run)

    async def subscribe(self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None) -> _Subscription:
        run = self.get_run(run_id)
        sub = await self._sse.subscribe(run_id, equivalent=equivalent, after_event_id=after_event_id)

        # For fixtures-mode UX/tests: emit one domain event immediately after subscribe.
        if after_event_id is None and run.state == "running" and run.mode == "fixtures":
            evt = self._fixtures_runner.maybe_make_tx_updated(run_id=run_id, equivalent=equivalent)
            if evt is not None:
                try:
                    sub.queue.put_nowait(evt)
                except asyncio.QueueFull:
                    pass
        return sub

    async def unsubscribe(self, run_id: str, sub: _Subscription) -> None:
        await self._sse.unsubscribe(run_id, sub)

    async def build_graph_snapshot(
        self,
        *,
        run_id: str,
        equivalent: str,
        session=None,
    ) -> SimulatorGraphSnapshot:
        return await self._snapshot_builder.build_graph_snapshot(
            run_id=run_id,
            equivalent=equivalent,
            session=session,
        )

    async def build_ego_snapshot(
        self,
        *,
        run_id: str,
        equivalent: str,
        pid: str,
        depth: int,
        session=None,
    ) -> SimulatorGraphSnapshot:
        snap = await self.build_graph_snapshot(run_id=run_id, equivalent=equivalent, session=session)
        if depth <= 0:
            return snap

        # Undirected BFS on links.
        neighbors: dict[str, set[str]] = {}
        for link in snap.links:
            neighbors.setdefault(link.source, set()).add(link.target)
            neighbors.setdefault(link.target, set()).add(link.source)

        visited: set[str] = {pid}
        frontier: set[str] = {pid}

        for _ in range(depth):
            nxt: set[str] = set()
            for cur in frontier:
                nxt |= neighbors.get(cur, set())
            nxt -= visited
            if not nxt:
                break
            visited |= nxt
            frontier = nxt

        nodes = [n for n in snap.nodes if n.id in visited]
        links = [l for l in snap.links if l.source in visited and l.target in visited]
        return SimulatorGraphSnapshot(
            equivalent=snap.equivalent,
            generated_at=snap.generated_at,
            nodes=nodes,
            links=links,
            palette=snap.palette,
            limits=snap.limits,
        )

    async def _heartbeat_loop(self, run_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                run = self.get_run(run_id)

                with self._lock:
                    if run.state != "running":
                        continue

                    # Runner-algorithm: fixed sim-time tick; intensity controls action budget.
                    run.tick_index += 1
                    run.sim_time_ms = run.tick_index * int(self._tick_ms_base)

                    # Real-mode sets queue_depth during tick work; fixtures-mode has no queue.
                    if run.mode == "fixtures":
                        run.queue_depth = 0

                    # Fixtures-mode event generation (best-effort).
                    if run.mode == "fixtures":
                        self._fixtures_runner.tick_fixtures_events(run_id)

                # Real-mode runner work is intentionally outside the lock
                # (it can touch the DB and may take time).
                if run.mode == "real" and run.state == "running":
                    await self._real_runner.tick_real_mode(run_id)

                self.publish_run_status(run_id)
                await simulator_storage.upsert_run(run)
        except asyncio.CancelledError:
            return


class SimulatorRuntime(_SimulatorRuntimeBase):
    """Concrete runtime implementation.

    Public entrypoints should import `SimulatorRuntime`/`runtime` from
    `app.core.simulator.runtime` to keep imports stable.
    """

    def _db_enabled(self) -> bool:
        """Whether simulator DB persistence is enabled in the current process."""
        return simulator_storage.db_enabled()


runtime = SimulatorRuntime()
