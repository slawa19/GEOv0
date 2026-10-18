from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Optional
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
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.core.simulator.models import RunRecord, ScenarioRecord, _Subscription, note_subscribers_locked
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.core.simulator.run_lifecycle import IDLE_STOP_REASON, RunLifecycle
from app.config import settings
from app.core.simulator.runtime_utils import (
    FIXTURES_DIR,
    REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT,
    REAL_MAX_ERRORS_TOTAL_DEFAULT,
    REAL_MAX_IN_FLIGHT_DEFAULT,
    REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT,
    SCENARIO_SCHEMA_PATH,
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

    raw = settings.SIMULATOR_SCENARIO_ALLOWLIST.strip()
    if raw:
        if raw in {"*", "all", "ALL"}:
            return None
        items = {x.strip() for x in raw.split(",") if x.strip()}
        return items or None

    return {
        "greenfield-village-100-realistic-v2",
        "riverside-town-50-realistic-v2",
        "clearing-demo-10",
    }


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
        self._active_run_id_by_owner: dict[str, str] = {}  # owner_id → run_id

        self._is_shutting_down = False

        # Runtime knobs (env-configurable; defaults preserve existing behavior).
        self._tick_ms_base = settings.SIMULATOR_TICK_MS_BASE
        self._actions_per_tick_max = settings.SIMULATOR_ACTIONS_PER_TICK_MAX
        self._clearing_every_n_ticks = settings.SIMULATOR_CLEARING_EVERY_N_TICKS
        self._max_active_runs = settings.SIMULATOR_MAX_ACTIVE_RUNS
        self._max_run_records = settings.SIMULATOR_MAX_RUN_RECORDS
        self._max_active_runs_per_owner = settings.SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER
        # 044 stage 2 (decision 2): the idle stop - its threshold and its clock (monotonic; tests replace `_idle_now`).
        self._run_idle_stop_sec = settings.SIMULATOR_RUN_IDLE_STOP_SEC
        self._idle_now: Callable[[], float] = time.monotonic

        # DB persistence throttling for run status row.
        # Defaults reduce write amplification in heartbeat loop.
        self._run_persist_every_ms = settings.SIMULATOR_RUN_PERSIST_EVERY_MS
        self._run_persist_dirty_every_ms = settings.SIMULATOR_RUN_PERSIST_DIRTY_EVERY_MS

        # Local artifact retention (0 disables).
        self._artifacts_ttl_hours = settings.SIMULATOR_ARTIFACTS_TTL_HOURS

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
        self._event_buffer_max = settings.SIMULATOR_EVENT_BUFFER_SIZE
        self._event_buffer_ttl_sec = settings.SIMULATOR_EVENT_BUFFER_TTL_SEC
        self._sse_sub_queue_max = settings.SIMULATOR_SSE_SUB_QUEUE_MAX

        self._sse = SseBroadcast(
            lock=self._lock,
            runs=self._runs,
            get_event_buffer_max=lambda: int(self._event_buffer_max),
            get_event_buffer_ttl_sec=lambda: int(self._event_buffer_ttl_sec),
            get_sub_queue_max=lambda: int(self._sse_sub_queue_max),
            enqueue_event_artifact=self._artifacts.enqueue_event_artifact,
            logger=logger,
            idle_now=lambda: self._idle_now(),
        )

        self._run_lifecycle = RunLifecycle(
            lock=self._lock,
            runs=self._runs,
            set_active_run_id=lambda run_id, owner_id="": self.set_active_run_id(run_id, owner_id=owner_id),
            clear_active_run_id=lambda owner_id="", run_id="": self.clear_active_run_id(owner_id=owner_id, run_id=run_id),
            utc_now=_utc_now,
            new_run_id=_new_run_id,
            get_scenario_raw=lambda scenario_id: self.get_scenario(scenario_id).raw,
            edges_by_equivalent=_edges_by_equivalent,
            artifacts=self._artifacts,
            sse=self._sse,
            heartbeat_loop=self._heartbeat_loop,
            publish_run_status=self.publish_run_status,
            run_to_status=_run_to_status,
            get_run_status_payload_json=lambda run_id: self.get_run_status(
                run_id
            ).model_dump(mode="json", by_alias=True),
            real_max_in_flight_default=REAL_MAX_IN_FLIGHT_DEFAULT,
            get_max_active_runs=lambda: int(self._max_active_runs),
            get_max_run_records=lambda: int(self._max_run_records),
            logger=logger,
            get_active_run_id_for_owner=lambda owner_id: (
                self._active_run_id_by_owner.get(owner_id) if owner_id else None
            ),
            get_max_active_runs_per_owner=lambda: int(self._max_active_runs_per_owner),
            idle_now=lambda: self._idle_now(),
        )

        self._real_runner = RealRunnerImpl(
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

        try:
            # Stop runs sequentially to avoid amplifying load on shutdown.
            for run_id in run_ids:
                try:
                    await self.stop(run_id)
                except Exception:
                    logger.exception(
                        "simulator.runtime.shutdown_stop_failed run_id=%s", run_id
                    )

            # Ensure subscriptions are cleared even if individual streams didn't unsubscribe.
            with self._lock:
                for run in self._runs.values():
                    try:
                        run._subs.clear()
                        note_subscribers_locked(run, self._idle_now())
                    except Exception:
                        pass

                # IMPORTANT (pytest): runtime is a process-wide singleton. FastAPI lifespan
                # may be started/stopped multiple times within one test process.
                # Clear run state so the next lifespan can create runs again.
                self._runs.clear()
                self._active_run_id_by_owner.clear()
        finally:
            # Allow re-use after graceful shutdown completes.
            with self._lock:
                self._is_shutting_down = False

    # ------------------------------------------
    # Per-owner active run management
    # ------------------------------------------

    def get_active_run_id(self, owner_id: str = "") -> Optional[str]:
        """Get active run for a specific owner; an empty owner_id has none (044 S1d).

        Every SimulatorActor carries a non-empty owner_id ("admin", "cli:", "pid:", "anon:"), so an
        empty one is never a lookup for "any owner's run".
        """
        if not owner_id:
            return None
        with self._lock:
            return self._active_run_id_by_owner.get(owner_id)

    def set_active_run_id(self, run_id: str, owner_id: str = "") -> None:
        """Register active run for owner."""
        with self._lock:
            self._active_run_id_by_owner[owner_id] = run_id

    def clear_active_run_id(self, owner_id: str = "", run_id: str = "") -> None:
        """Remove active run for owner.

        If run_id is specified, only remove if it matches the stored value.
        """
        with self._lock:
            if owner_id in self._active_run_id_by_owner:
                if run_id and self._active_run_id_by_owner[owner_id] != run_id:
                    return  # different run, don't clear
                del self._active_run_id_by_owner[owner_id]

    def get_all_active_runs(self) -> dict[str, str]:
        """Return copy of all active owner→run mappings (for admin)."""
        with self._lock:
            return dict(self._active_run_id_by_owner)

    def list_runs(
        self,
        *,
        state: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> list:
        """Return all run records with optional filtering (admin use).

        Args:
            state: Filter by run state string (e.g. "running", "stopped", "error").
            owner_id: Filter by owner_id exact match.

        Returns:
            List of RunRecord sorted newest-first by started_at; records
            without started_at go last.
        """
        with self._lock:
            runs = list(self._runs.values())

        if state is not None:
            runs = [r for r in runs if str(r.state or "") == state]
        if owner_id is not None:
            runs = [r for r in runs if r.owner_id == owner_id]

        # Sort newest-first; runs without started_at go to the end.
        runs.sort(
            key=lambda r: (
                r.started_at is None,
                -(r.started_at.timestamp() if r.started_at else 0),
            )
        )
        return runs

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

    def get_admitted_scenario(self, scenario_id: str, *, is_admin: bool) -> ScenarioRecord:
        """The scenario, if this actor may read or start it (044 `T4405`, admission (v)).

        A non-administrator reaches only the scenarios the list shows them (`SIMULATOR_SCENARIO_ALLOWLIST`, the same
        rule as `list_scenarios`); any other id answers exactly as an unknown one (404), so the refusal discloses
        nothing. The administrator reaches every registered scenario (it uploads them). Checked before any effect."""

        allow = _scenario_allowlist()
        if not is_admin and allow is not None and scenario_id not in allow:
            raise NotFoundException(f"Scenario {scenario_id} not found")
        return self.get_scenario(scenario_id)

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
        """The scenario's graph as its TEMPLATE draws it, without starting a run.

        `mode` is always `real` (042: the `fixtures` mode is gone; the API refuses it with 422). 044 `T4405`: no
        database enrichment. A run's participants are fresh rows under identities of that run, so the template's pids
        name no run's state; enriching by them read whatever rows happened to carry those pids (another visitor's, an
        older unbound run's). `session` is kept for the route's signature and not read.
        """

        rec = self.get_scenario(scenario_id)
        return scenario_to_snapshot(rec.raw, equivalent=equivalent, utc_now=_utc_now)

    # -----------------------------
    # Runs
    # -----------------------------

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    def get_run_status(self, run_id: str) -> RunStatus:
        run = self.get_run(run_id)
        return _run_to_status(run)

    async def create_run(
        self,
        *,
        scenario_id: str,
        mode: RunMode,
        intensity_percent: Optional[int] = None,
        owner_id: str = "",
        owner_kind: str = "",
        created_by: Optional[dict] = None,
    ) -> str:
        with self._lock:
            if self._is_shutting_down:
                raise ConflictException("Simulator runtime is shutting down")
        return await self._run_lifecycle.create_run(
            scenario_id=scenario_id,
            mode=mode,
            intensity_percent=intensity_percent,
            owner_id=owner_id,
            owner_kind=owner_kind,
            created_by=created_by,
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

    def _build_run_status_event(self, run_id: str, event_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        stall_ticks = int(run._real_consec_all_rejected_ticks or 0)
        return SimulatorRunStatusEvent(
            event_id=event_id,
            ts=_utc_now(),
            type="run_status",
            run_id=run.run_id,
            scenario_id=run.scenario_id,
            state=run.state,
            sim_time_ms=run.sim_time_ms,
            intensity_percent=run.intensity_percent,
            ops_sec=run.ops_sec,
            queue_depth=run.queue_depth,
            attempts_total=run.attempts_total,
            committed_total=run.committed_total,
            rejected_total=run.rejected_total,
            errors_total=run.errors_total,
            timeouts_total=run.timeouts_total,
            last_event_type=run.last_event_type,
            current_phase=run.current_phase,
            last_error=_dict_to_last_error(run.last_error),
            consec_all_rejected_ticks=(stall_ticks if stall_ticks > 0 else None),
        ).model_dump(mode="json", by_alias=True)

    def publish_run_status(self, run_id: str) -> str:
        payload = self._sse.publish_event(
            run_id=run_id,
            payload_factory=lambda event_id: self._build_run_status_event(
                run_id, event_id
            ),
        )
        if payload is None:  # pragma: no cover - get_run callers own this invariant
            raise RuntimeError("Cannot publish status for missing simulator run")
        return str(payload["event_id"])

    async def pause(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.pause(run_id)

    async def resume(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.resume(run_id)

    async def stop(
        self,
        run_id: str,
        *,
        source: Optional[str] = None,
        reason: Optional[str] = None,
        client: Optional[str] = None,
    ) -> RunStatus:
        # 044 stage 2 (Codex §15 fix-delta): the record is pinned for the WHOLE cleanup - the lifecycle's stop (writer
        # drain, upsert, artifacts finalize, which reads the record) and the final flush below - so a pruning pass
        # running meanwhile cannot drop it; pinned before the first await, unpinned in `finally`, pruned after.
        with self._lock:
            self._run_lifecycle.pin_cleanup_locked(run_id)
        try:
            status = await self._run_lifecycle.stop(
                run_id, source=source, reason=reason, client=client
            )

            # Best-effort final DB flush for real-mode tick metrics/bottlenecks.
            # Must not break stop() even if DB is unavailable.
            try:
                await self._real_runner.flush_pending_storage(run_id)
            except Exception:
                logger.warning(
                    "simulator.runtime.flush_pending_storage_failed run_id=%s",
                    run_id,
                    exc_info=True,
                )
        finally:
            with self._lock:
                self._run_lifecycle.unpin_cleanup_locked(run_id)

        # 044 stage 2: a run that ends is a pruning point too (after the flush, which reads the record).
        try:
            await self._run_lifecycle.prune_ended_runs()
        except Exception:
            logger.exception("simulator.runtime.prune_ended_runs_failed run_id=%s", run_id)

        return status

    async def restart(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.restart(run_id)

    async def set_intensity(self, run_id: str, intensity_percent: int) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            run.intensity_percent = int(intensity_percent)

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return _run_to_status(run)

    async def subscribe(
        self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None
    ) -> _Subscription:
        self.get_run(run_id)
        return await self._sse.subscribe(
            run_id, equivalent=equivalent, after_event_id=after_event_id
        )

    async def subscribe_with_status(
        self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None
    ) -> tuple[_Subscription, str]:
        """Atomically enqueue replay + authoritative status before exposure."""
        payload: dict[str, Any] | None = None

        def build_status(event_id: str) -> dict[str, Any]:
            nonlocal payload
            payload = self._build_run_status_event(run_id, event_id)
            return payload

        sub = await self._sse.subscribe(
            run_id,
            equivalent=equivalent,
            after_event_id=after_event_id,
            bootstrap_event_factory=build_status,
        )
        if payload is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("SSE status bootstrap was not created")

        return sub, str(payload["event_id"])

    def finish_replay_bootstrap(
        self, run_id: str, sub: _Subscription
    ) -> Optional[list[dict[str, Any]]]:
        return self._sse.finish_replay_bootstrap(run_id=run_id, sub=sub)

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
        snap = await self.build_graph_snapshot(
            run_id=run_id, equivalent=equivalent, session=session
        )
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
        links = [
            edge
            for edge in snap.links
            if edge.source in visited and edge.target in visited
        ]
        return SimulatorGraphSnapshot(
            equivalent=snap.equivalent,
            generated_at=snap.generated_at,
            nodes=nodes,
            links=links,
            palette=snap.palette,
            limits=snap.limits,
        )

    async def _heartbeat_failed(self, run_id: str, error: Exception) -> None:
        """An iteration of the heartbeat raised: the run is stopped as failed, visibly (034 `F-034-10`).

        The loop used to catch `CancelledError` only. Any other exception - a status publication, the registry, a
        failure inside the tick's own failure handling - ended the task and left the run `running` with nothing
        ticking it, no `last_error` and no log line of the application. There is no retry here: what failed is
        not known to be transient, and a run that is not ticked must not say `running`. The run goes to `error`
        with `last_error`, through the same `fail_run` a failing tick uses; `resume` brings it back (024 T2416.2).
        Never raises (a cancellation aside).
        """

        logger.error(
            "simulator.heartbeat.failed run_id=%s error_type=%s",
            str(run_id),
            type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )
        # ONLY THE TYPE of the exception goes out: `last_error` is published in `run_status`, served by the API and
        # stored in `simulator_runs`, and the text of an `OSError` or a database error carries local paths and SQL
        # (AGENTS.md §12). The full text is in the log line above, found by the run id.
        message = f"The heartbeat failed: {type(error).__name__}"
        try:
            await self._real_runner.fail_run(run_id, code="HEARTBEAT_FAILED", message=message)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("simulator.heartbeat.fail_run_failed run_id=%s", str(run_id), exc_info=True)
        # `fail_run` raised. It moves the state first and then publishes and stores; when it is the publication
        # that failed (the usual way to get here: it is what failed the heartbeat too), the run is `error` in
        # memory and NOTHING of that was stored - the `simulator_runs` row would keep saying `running`. So the
        # state is made sure of here (for the case where `fail_run` could not even start) and the row is written.
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.state == "running":
                run.state = "error"
                run.stopped_at = _utc_now()
                run.current_phase = None
                run.errors_total += 1
                run.last_error = {"code": "HEARTBEAT_FAILED", "message": message, "at": _utc_now().isoformat()}
        if run is None:
            return  # the registry no longer has the run: there is nothing left to mark or to store
        try:
            await simulator_storage.upsert_run(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("simulator.heartbeat.fail_run_upsert_failed run_id=%s", str(run_id), exc_info=True)

    @staticmethod
    def _tick_seconds_of(run: RunRecord) -> float:
        """The real duration of a tick of this run: `settings.playback.tick_seconds` of its scenario (036 B2), a LOWER bound of
        the period (the work of the tick comes on top), 1.0 - the pace before B2 - when the scenario says nothing. The value
        was checked against the schema's range when the run was created (`build_story`); a value outside it is not trusted
        here either and falls back to the default."""

        settings_block = (getattr(run, "_scenario_raw", None) or {}).get("settings")
        playback = settings_block.get("playback") if isinstance(settings_block, dict) else None
        value = playback.get("tick_seconds") if isinstance(playback, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0.25 <= float(value) <= 5.0):
            return 1.0
        return float(value)

    def _idle_expired_locked(self, run: RunRecord) -> bool:
        """044 stage 2 (decision 2): the run has had no SSE subscriber for `SIMULATOR_RUN_IDLE_STOP_SEC` (0: never)."""

        limit = int(self._run_idle_stop_sec or 0)
        if limit <= 0 or run._subs:
            return False
        since = run._no_subscribers_since
        return since is not None and self._idle_now() - since >= limit

    async def _stop_idle_run(self, run_id: str, run: RunRecord) -> None:
        """Stop the run from its own heartbeat: the task detaches itself first, so `stop` does not cancel and await the
        task it runs in (a task cannot await itself); the loop returns right after."""

        with self._lock:
            if run._heartbeat_task is asyncio.current_task():
                run._heartbeat_task = None
            idle_for = self._idle_now() - float(run._no_subscribers_since or 0.0)
        logger.info(
            "simulator.run.idle_stop run_id=%s state=%s idle_sec=%d threshold_sec=%d",
            run_id,
            run.state,
            int(idle_for),
            int(self._run_idle_stop_sec or 0),
        )
        await self.stop(run_id, source="server", reason=IDLE_STOP_REASON)

    async def _heartbeat_loop(self, run_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(self._tick_seconds_of(self.get_run(run_id)))
                run = self.get_run(run_id)

                with self._lock:
                    if run.state in ("stopped", "stopping", "error"):
                        return
                    # 044 stage 2: checked before the pause - a paused run nobody watches is stopped too.
                    idle = self._idle_expired_locked(run)
                    if not idle and run.state != "running":
                        continue
                    if not idle:
                        # Runner-algorithm: fixed sim-time tick; intensity controls action budget.
                        run.tick_index += 1
                        run.sim_time_ms = run.tick_index * int(self._tick_ms_base)
                if idle:
                    await self._stop_idle_run(run_id, run)
                    return

                # The runner's work is intentionally outside the lock (it can touch the DB and may take time).
                # Every run is real-mode (042 S1).
                if run.state == "running":
                    _t0 = time.monotonic()
                    await self._real_runner.tick_real_mode(run_id)
                    _tick_elapsed_s = time.monotonic() - _t0
                    if _tick_elapsed_s > 2.0:
                        logger.warning(
                            "simulator.heartbeat.slow_tick run_id=%s tick=%d elapsed=%.2fs",
                            run_id,
                            run.tick_index,
                            _tick_elapsed_s,
                        )

                self.publish_run_status(run_id)

                # Debounce DB writes to reduce disk IO.
                now_ms = int(time.time() * 1000)
                persist_every_ms = int(getattr(self, "_run_persist_every_ms", 0) or 0)
                dirty_every_ms = int(
                    getattr(self, "_run_persist_dirty_every_ms", 0) or 0
                )
                if dirty_every_ms <= 0:
                    dirty_every_ms = persist_every_ms

                if persist_every_ms <= 0:
                    await simulator_storage.upsert_run(run)
                    continue

                last_sig = run._persist_last_sig
                if last_sig is None:
                    # Initialize baseline from current state; create_run already upserts.
                    run._persist_last_at_ms = now_ms
                    run._persist_last_state = str(run.state)
                    run._persist_last_sig = (
                        str(run.state),
                        int(run.intensity_percent or 0),
                        float(run.ops_sec or 0.0),
                        int(run.queue_depth or 0),
                        int(run.errors_total or 0),
                        str(run.last_event_type or ""),
                        str(run.current_phase or ""),
                        str(run.stopped_at or ""),
                        str(run.last_error or ""),
                    )
                    continue

                sig = (
                    str(run.state),
                    int(run.intensity_percent or 0),
                    float(run.ops_sec or 0.0),
                    int(run.queue_depth or 0),
                    int(run.errors_total or 0),
                    str(run.last_event_type or ""),
                    str(run.current_phase or ""),
                    str(run.stopped_at or ""),
                    str(run.last_error or ""),
                )

                last_at = int(run._persist_last_at_ms or 0)
                last_state = str(run._persist_last_state or "")
                state_now = str(run.state)
                state_changed = state_now != last_state
                dirty = sig != last_sig
                due_ms = dirty_every_ms if dirty else persist_every_ms

                if state_changed or (due_ms > 0 and (now_ms - last_at) >= due_ms):
                    await simulator_storage.upsert_run(run)
                    run._persist_last_at_ms = now_ms
                    run._persist_last_state = state_now
                    run._persist_last_sig = sig
        except asyncio.CancelledError:
            return
        except Exception as error:
            # 034 `F-034-10`: the loop ends here either way; the run must not stay `running` behind it.
            await self._heartbeat_failed(run_id, error)


class SimulatorRuntime(_SimulatorRuntimeBase):
    """Concrete runtime implementation.

    Public entrypoints should import `SimulatorRuntime`/`runtime` from
    `app.core.simulator.runtime` to keep imports stable.
    """


runtime = SimulatorRuntime()
