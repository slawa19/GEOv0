from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import random
from datetime import datetime
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
        set_active_run_id: Callable,  # (run_id: str, owner_id: str = "") -> None
        clear_active_run_id: Optional[Callable] = None,  # (owner_id: str = "", run_id: str = "") -> None
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
        get_active_run_id_for_owner: Optional[Callable[[str], Optional[str]]] = None,
        get_max_active_runs_per_owner: Optional[Callable[[], int]] = None,
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._set_active_run_id = set_active_run_id
        self._clear_active_run_id = clear_active_run_id
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
        self._get_active_run_id_for_owner = get_active_run_id_for_owner
        self._get_max_active_runs_per_owner = get_max_active_runs_per_owner

    def _count_active_runs_locked(self) -> int:
        active_states = {"running", "paused", "stopping"}
        return sum(1 for r in self._runs.values() if str(getattr(r, "state", "")) in active_states)

    def _count_active_runs_for_owner_locked(self, owner_id: str) -> tuple[int, Optional[str]]:
        """Return (count, most_recent_active_run_id) for a given owner."""
        owner = str(owner_id or "")
        if not owner:
            return 0, None

        active_states = {"running", "paused", "stopping"}
        active_runs: list[RunRecord] = [
            r
            for r in self._runs.values()
            if str(getattr(r, "owner_id", "") or "") == owner
            and str(getattr(r, "state", "") or "") in active_states
        ]

        if not active_runs:
            return 0, None

        # Deterministic: prefer most recent started_at.
        active_runs.sort(
            key=lambda r: (
                getattr(r, "started_at", None) is None,
                # sentinel for None: datetime.min ensures type-consistent comparison
                getattr(r, "started_at", None) or datetime.min,
                str(getattr(r, "run_id", "")),
            ),
            reverse=True,
        )
        most_recent_id = str(getattr(active_runs[0], "run_id", "") or "").strip() or None
        return len(active_runs), most_recent_id

    def _enforce_active_run_limit_locked(self) -> None:
        max_active = int(self._get_max_active_runs() or 0)
        if max_active <= 0:
            return
        active = self._count_active_runs_locked()
        if active >= max_active:
            raise ConflictException(
                "Global active runs limit reached",
                details={
                    "conflict_kind": "global_active_limit",
                    "max_active_runs": max_active,
                    "active_runs": active,
                },
            )

    def _prune_run_records_locked(self) -> None:
        max_records = int(self._get_max_run_records() or 0)
        if max_records <= 0:
            return
        if len(self._runs) <= max_records:
            return

        # Prefer pruning stopped runs first (oldest first).
        def _has_live_task(task: Optional[asyncio.Task[None]]) -> bool:
            return task is not None and not task.done()

        stopped: list[RunRecord] = [
            r
            for r in self._runs.values()
            if str(getattr(r, "state", "")) == "stopped"
            and not _has_live_task(getattr(r, "_heartbeat_task", None))
            and not _has_live_task(getattr(r, "_artifact_events_task", None))
        ]

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

    async def create_run(
        self,
        *,
        scenario_id: str,
        mode: RunMode,
        intensity_percent: int,
        owner_id: str = "",
        owner_kind: str = "",
        created_by: Optional[dict] = None,
    ) -> str:
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
            owner_id=owner_id,
            owner_kind=owner_kind,
            created_by=created_by,
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
        # (Needed for both fixtures + real runners.)
        scenario = self._get_scenario_raw(scenario_id)
        run._scenario_raw = copy.deepcopy(scenario)
        run._edges_by_equivalent = self._edges_by_equivalent(run._scenario_raw)
        run._rng = random.Random(run_id)
        run._next_tx_at_ms = 0
        run._next_clearing_at_ms = 25_000
        run._clearing_pending_done_at_ms = None

        with self._lock:
            # Thread safety: RunRecord is created outside lock (cheap), but all
            # state mutations (_runs, _active_run_id_by_owner) happen atomically
            # inside this lock block.

            # Per-owner limit check (§6.3): owner may have at most
            # SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER active runs (default: 1).
            if owner_id:
                max_per_owner = 1
                if self._get_max_active_runs_per_owner is not None:
                    try:
                        max_per_owner = int(self._get_max_active_runs_per_owner() or 0)
                    except Exception:
                        max_per_owner = 1

                if max_per_owner > 0:
                    count, most_recent_run_id = self._count_active_runs_for_owner_locked(owner_id)
                    mapped_run_id: Optional[str] = None
                    if self._get_active_run_id_for_owner is not None:
                        try:
                            mapped_run_id = self._get_active_run_id_for_owner(owner_id)
                        except Exception:
                            mapped_run_id = None

                    # The active mapping is the primary source of truth for "has an active run".
                    # If it points to a run not present in _runs (e.g. tests or best-effort cleanup),
                    # still treat it as an active slot for per-owner limit enforcement.
                    if mapped_run_id:
                        if mapped_run_id not in self._runs:
                            count = max(count, 1)
                        if most_recent_run_id is None:
                            most_recent_run_id = mapped_run_id

                    if count >= max_per_owner:
                        active_run_id = most_recent_run_id

                        raise ConflictException(
                            "Owner already has an active run",
                            details={
                                "conflict_kind": "owner_active_exists",
                                "active_run_id": active_run_id,
                                "owner_id": owner_id,
                                "active_runs": count,
                                "max_active_runs_per_owner": max_per_owner,
                            },
                        )

            self._enforce_active_run_limit_locked()

            # Minimal local artifacts (best-effort). Do it before exposing the run.
            self._artifacts.init_run_artifacts(run)

            self._runs[run_id] = run
            self._set_active_run_id(run_id, owner_id)

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
        heartbeat_task: Optional[asyncio.Task[None]] = None
        with self._lock:
            if source is not None:
                run.stop_source = str(source)
            if reason is not None:
                run.stop_reason = str(reason)
            if client is not None:
                run.stop_client = str(client)
            if run.stop_requested_at is None and (source is not None or reason is not None or client is not None):
                run.stop_requested_at = self._utc_now()

            # Always detach tasks for cancellation (idempotent stop).
            # IMPORTANT: shutdown may call stop() even if a previous stop already
            # transitioned the run into "stopping" or even "stopped"; we still must
            # ensure background tasks are cancelled/awaited to avoid leaks in pytest.
            heartbeat_task = run._heartbeat_task

            if run.state not in ("stopping", "stopped"):
                run.state = "stopping"

        self._publish_run_status(run_id)

        # Transition to stopped early so active-run limits are released promptly.
        with self._lock:
            if run.state != "stopped":
                run.state = "stopped"
            if run.stopped_at is None:
                run.stopped_at = self._utc_now()

        # Release per-owner active run slot.
        if self._clear_active_run_id is not None:
            try:
                self._clear_active_run_id(owner_id=run.owner_id, run_id=run_id)
            except Exception:
                self._logger.exception(
                    "simulator.run.clear_active_run_id_failed run_id=%s", run_id
                )

        self._publish_run_status(run_id)

        # Best-effort: make sure background tasks are stopped even if persistence fails.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                self._logger.exception("simulator.run.stop_heartbeat_failed run_id=%s", run_id)
            finally:
                with self._lock:
                    if run._heartbeat_task is heartbeat_task:
                        run._heartbeat_task = None

        # Always attempt to stop artifacts writer (idempotent).
        try:
            await self._artifacts.stop_events_writer(run_id)
        except Exception:
            self._logger.exception("simulator.run.stop_artifacts_writer_failed run_id=%s", run_id)

        try:
            await simulator_storage.upsert_run(run)
        except Exception:
            self._logger.exception("simulator.run.stop_upsert_failed run_id=%s", run_id)

        # Enforce TTL-based pruning even if no further events are appended.
        with self._lock:
            self._sse.prune_event_buffer_locked(run)

        # Finalize artifacts (best-effort): status.json, summary.json, bundle.zip.
        try:
            await self._artifacts.finalize_run_artifacts(
                run_id=run_id,
                status_payload=self._get_run_status_payload_json(run_id),
            )
        except Exception:
            self._logger.exception("simulator.run.stop_finalize_artifacts_failed run_id=%s", run_id)

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

            # Restore active mapping removed during stop(). Without this,
            # get_active_run_id(owner_id) → None and admin stop-all won't see
            # the restarted run (§FIX-CR2).
            if run.owner_id:
                existing: Optional[str] = None
                if self._get_active_run_id_for_owner is not None:
                    try:
                        existing = self._get_active_run_id_for_owner(run.owner_id)
                    except Exception:
                        existing = None
                if existing and existing != run_id:
                    raise ConflictException(
                        "Owner already has another active run",
                        details={
                            "conflict_kind": "owner_active_exists",
                            "active_run_id": existing,
                        },
                    )
                # _set_active_run_id is always set (non-Optional Callable)
                self._set_active_run_id(run_id, run.owner_id)

        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return self._run_to_status(run)
