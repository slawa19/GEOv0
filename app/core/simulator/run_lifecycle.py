from __future__ import annotations

import asyncio
import hashlib
import os
import random
from typing import Any, Callable, Optional

import app.core.simulator.storage as simulator_storage
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast
from app.schemas.simulator import RunMode, RunStatus
from app.utils.exceptions import ConflictException
from app.utils.exceptions import NotFoundException


class RunLifecycle:
    def __init__(
        self,
        *,
        lock,
        runs: dict[str, RunRecord],
        set_active_run_id: Callable[[str], None],
        utc_now,
        new_run_id: Callable[[], str],
        get_scenario_raw: Callable[[str], dict[str, Any]],
        edges_by_equivalent: Callable[[dict[str, Any]], dict[str, list[tuple[str, str]]]],
        artifacts: ArtifactsManager,
        sse: SseBroadcast,
        heartbeat_loop,
        publish_run_status: Callable[[str], None],
        run_to_status: Callable[[RunRecord], RunStatus],
        get_run_status_payload_json: Callable[[str], dict[str, Any]],
        real_max_in_flight_default: int,
        get_max_active_runs: Callable[[], int],
        get_max_run_records: Callable[[], int],
        logger,
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._set_active_run_id = set_active_run_id
        self._utc_now = utc_now
        self._new_run_id = new_run_id
        self._get_scenario_raw = get_scenario_raw
        self._edges_by_equivalent = edges_by_equivalent
        self._artifacts = artifacts
        self._sse = sse
        self._heartbeat_loop = heartbeat_loop
        self._publish_run_status = publish_run_status
        self._run_to_status = run_to_status
        self._get_run_status_payload_json = get_run_status_payload_json
        self._real_max_in_flight_default = real_max_in_flight_default
        self._get_max_active_runs = get_max_active_runs
        self._get_max_run_records = get_max_run_records
        self._logger = logger

    def _count_active_runs_locked(self) -> int:
        active_states = {"running", "paused", "stopping"}
        return sum(1 for r in self._runs.values() if str(getattr(r, "state", "")) in active_states)

    def _enforce_active_run_limit_locked(self) -> None:
        max_active = int(self._get_max_active_runs() or 0)
        if max_active <= 0:
            return
        active = self._count_active_runs_locked()
        if active >= max_active:
            raise ConflictException(
                "Too many active simulator runs",
                details={"max_active_runs": max_active, "active_runs": active},
            )

    def _prune_run_records_locked(self) -> None:
        max_records = int(self._get_max_run_records() or 0)
        if max_records <= 0:
            return
        if len(self._runs) <= max_records:
            return

        # Prefer pruning stopped runs first (oldest first).
        stopped: list[RunRecord] = [r for r in self._runs.values() if str(getattr(r, "state", "")) == "stopped"]

        def _sort_key(r: RunRecord):
            ts = getattr(r, "stopped_at", None) or getattr(r, "started_at", None)
            return (ts is None, ts)

        stopped.sort(key=_sort_key)

        while len(self._runs) > max_records and stopped:
            r = stopped.pop(0)
            try:
                del self._runs[r.run_id]
            except Exception:
                break

    def _get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    async def create_run(self, *, scenario_id: str, mode: RunMode, intensity_percent: int) -> str:
        # Validate scenario exists (even for real mode for now).
        _ = self._get_scenario_raw(scenario_id)

        run_id = self._new_run_id()
        seed_material = hashlib.sha256(run_id.encode("utf-8")).digest()
        seed = int.from_bytes(seed_material[:4], "big")
        run = RunRecord(
            run_id=run_id,
            scenario_id=scenario_id,
            mode=mode,
            state="running",
            started_at=self._utc_now(),
            seed=seed,
            tick_index=0,
            intensity_percent=intensity_percent,
        )

        if mode == "real":
            try:
                run._real_max_in_flight = max(
                    1,
                    int(os.getenv("SIMULATOR_REAL_MAX_IN_FLIGHT", str(self._real_max_in_flight_default))),
                )
            except Exception:
                run._real_max_in_flight = self._real_max_in_flight_default

        # Precompute per-equivalent edges for quick event generation.
        scenario = self._get_scenario_raw(scenario_id)
        run._edges_by_equivalent = self._edges_by_equivalent(scenario)
        run._rng = random.Random(run_id)
        run._next_tx_at_ms = 0
        run._next_clearing_at_ms = 25_000
        run._clearing_pending_done_at_ms = None

        with self._lock:
            self._enforce_active_run_limit_locked()

            # Minimal local artifacts (best-effort). Do it before exposing the run.
            self._artifacts.init_run_artifacts(run)

            self._runs[run_id] = run
            self._set_active_run_id(run_id)

            # Start artifacts writer (best-effort; no-op if artifacts disabled).
            self._artifacts.start_events_writer(run_id)

            # Start heartbeat loop.
            run._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(run_id),
                name=f"simulator-heartbeat:{run_id}",
            )

            # Emit immediate status event (so SSE clients don't wait for first tick).
            self._publish_run_status(run_id)

            # Prevent unbounded growth in-memory.
            self._prune_run_records_locked()

        await simulator_storage.upsert_run(run)
        await simulator_storage.sync_artifacts(run)
        return run_id

    async def pause(self, run_id: str) -> RunStatus:
        run = self._get_run(run_id)
        with self._lock:
            if run.state == "paused":
                pass
            elif run.state == "running":
                run.state = "paused"
            elif run.state in ("stopping", "stopped", "error"):
                # Idempotent, but no-op.
                pass
            else:
                run.state = "paused"

        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return self._run_to_status(run)

    async def resume(self, run_id: str) -> RunStatus:
        run = self._get_run(run_id)
        with self._lock:
            if run.state == "running":
                pass
            elif run.state == "paused":
                run.state = "running"
            elif run.state in ("stopping", "stopped"):
                # Idempotent: keep stopped
                pass
            else:
                run.state = "running"

        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return self._run_to_status(run)

    async def stop(
        self,
        run_id: str,
        *,
        source: Optional[str] = None,
        reason: Optional[str] = None,
        client: Optional[str] = None,
    ) -> RunStatus:
        run = self._get_run(run_id)
        task = None
        events_task: Optional[asyncio.Task[None]] = None
        with self._lock:
            if run.state in ("stopped",):
                return self._run_to_status(run)

            if source is not None:
                run.stop_source = str(source)
            if reason is not None:
                run.stop_reason = str(reason)
            if client is not None:
                run.stop_client = str(client)
            if run.stop_requested_at is None and (source is not None or reason is not None or client is not None):
                run.stop_requested_at = self._utc_now()

            if run.state != "stopping":
                run.state = "stopping"
                task = run._heartbeat_task
                events_task = run._artifact_events_task

        self._publish_run_status(run_id)

        # Transition to stopped.
        with self._lock:
            run.state = "stopped"
            run.stopped_at = self._utc_now()

        self._publish_run_status(run_id)

        await simulator_storage.upsert_run(run)

        # Enforce TTL-based pruning even if no further events are appended.
        with self._lock:
            self._sse.prune_event_buffer_locked(run)

        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                self._logger.exception("simulator.run.stop_heartbeat_failed run_id=%s", run_id)

        if events_task is not None:
            await self._artifacts.stop_events_writer(run_id)

        # Finalize artifacts (best-effort): status.json, summary.json, bundle.zip.
        await self._artifacts.finalize_run_artifacts(
            run_id=run_id,
            status_payload=self._get_run_status_payload_json(run_id),
        )
        return self._run_to_status(run)

    async def restart(self, run_id: str) -> RunStatus:
        run = self._get_run(run_id)
        with self._lock:
            run.sim_time_ms = 0
            run.tick_index = 0
            run.errors_total = 0
            run.last_error = None
            run.last_event_type = None
            run.current_phase = None
            run.queue_depth = 0
            run.ops_sec = 0.0
            run.stopped_at = None
            run.state = "running"
            run.started_at = self._utc_now()

            # Keep buffer but prune to avoid unbounded growth across long sessions.
            self._sse.prune_event_buffer_locked(run)

        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return self._run_to_status(run)
