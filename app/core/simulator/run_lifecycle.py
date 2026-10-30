from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime
from typing import Any, Callable, Optional

import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import PAUSE_REASON_SCENARIO_EXHAUSTED, RunRecord, note_subscribers_locked
from app.core.simulator.run_perimeter import bind_run_identities
from app.core.simulator.scenario_story import build_story
from app.core.simulator.sse_broadcast import SseBroadcast
from app.schemas.simulator import RunMode, RunStatus
from app.utils.exceptions import ConflictException
from app.utils.exceptions import NotFoundException

#: 054-A-7c (owner, 2026-10-30): the `last_error.code` of a run in `error` that `resume` refuses - "no second chance".
#: The run was stopped by its own error budget, by a run of failing ticks, by money that could not progress, or by a
#: violated integrity invariant; resuming it would run the same thing again. `restart` (and `stop`) stay as they are. Any
#: other code of `error` (`HEARTBEAT_FAILED`, `SIMULATOR_PID_TAKEN`, `SCENARIO_TRUSTLINE_REFUSED`, ...) is still
#: resumed. The codes are spelled where they are raised: `tick.py`,
#: `real_payments_executor.py` (`REAL_MODE_TOO_MANY_TIMEOUTS`, `INTEGRITY_VIOLATION`).
RESUME_REFUSED_ERROR_CODES = frozenset(
    {
        "REAL_MODE_TOO_MANY_ERRORS",
        "REAL_MODE_TOO_MANY_TIMEOUTS",
        "REAL_MODE_TICK_FAILED_REPEATED",
        "REAL_MODE_MONEY_NO_PROGRESS",
        "INTEGRITY_VIOLATION",
    }
)

#: The intensity of a run when neither the request nor the scenario's `settings.playback.intensity_percent` names one (036 B2).
DEFAULT_RUN_INTENSITY_PERCENT = 30

#: 044 stage 2 (decision 2): `stop_reason` of a run its heartbeat stopped because nobody watched it (`stop_source` "server").
IDLE_STOP_REASON = "idle_no_subscribers"


class RunLifecycle:
    def __init__(
        self,
        *,
        lock,
        runs: dict[str, RunRecord],
        set_active_run_id: Callable[[str, str], None],
        clear_active_run_id: Callable[..., None],  # (*, owner_id: str, run_id: str) -> None
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
        get_active_run_id_for_owner: Callable[[str], Optional[str]],
        get_max_active_runs_per_owner: Callable[[], int],
        idle_now: Callable[[], float] = time.monotonic,
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
        self._idle_now = idle_now
        # 044 stage 2 (Codex §15, two rounds): records whose cleanup is in progress - a pruning pass ending their events
        # writer, or a `stop` from its first await to its last (writer drain, final upsert, artifacts finalize, final
        # metrics flush). Such a record looks writer-less (`stop_events_writer` detaches before it awaits the drain) and
        # must not be dropped meanwhile. A count per run: two cleanups of one run may overlap.
        self._cleanup_in_progress: dict[str, int] = {}

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
                # 044 stage 2 (decision 3): the message names the number of places; `details` keep their shape.
                f"Global active runs limit reached: all {max_active} places are taken",
                details={
                    "conflict_kind": "global_active_limit",
                    "max_active_runs": max_active,
                    "active_runs": active,
                },
            )

    def _enforce_entry_limits_locked(self, run: RunRecord) -> None:
        """F-028-5: a run re-entering `running` from an inactive state passes the owner and global limits of create."""
        if str(run.state) in ("running", "paused", "stopping"):
            return  # already counted as active
        max_per_owner = int(self._get_max_active_runs_per_owner() or 0)
        if run.owner_id and max_per_owner > 0:
            count, active_run_id = self._count_active_runs_for_owner_locked(run.owner_id)
            if count >= max_per_owner:
                raise ConflictException(
                    "Owner already has an active run",
                    details={"conflict_kind": "owner_active_exists", "active_run_id": active_run_id},
                )
        self._enforce_active_run_limit_locked()

    @staticmethod
    def _has_live_task(task: Optional[asyncio.Task[None]]) -> bool:
        return task is not None and not task.done()

    def _ended_overflow_locked(self) -> list[RunRecord]:
        """The ended records (`stopped` and, 044 stage 2, `error`) to drop for `SIMULATOR_MAX_RUN_RECORDS`, oldest first,
        as many as the registry is over the limit. A record whose heartbeat still runs is never one of them."""

        max_records = int(self._get_max_run_records() or 0)
        if max_records <= 0 or len(self._runs) <= max_records:
            return []
        ended = [
            r
            for r in self._runs.values()
            if str(getattr(r, "state", "")) in ("stopped", "error")
            and not self._has_live_task(getattr(r, "_heartbeat_task", None))
        ]

        def _sort_key(r: RunRecord):
            ts = getattr(r, "stopped_at", None) or getattr(r, "started_at", None)
            return (ts is None, ts)

        ended.sort(key=_sort_key)
        return ended[: len(self._runs) - max_records]

    def _prune_run_records_locked(self) -> None:
        """Drop the oldest ended records over the limit whose events writer has ended (044 stage 2: `error` records
        too - until then they were never dropped). A dropped record's owner mapping is cleared with it: `fail_run` leaves
        the mapping of an `error` run in place (admin stop-all and the 409 of `restart` read it), and a mapping to a
        record that no longer exists would answer for a run nobody can reach."""

        for r in self._ended_overflow_locked():
            if self._has_live_task(getattr(r, "_artifact_events_task", None)) or r.run_id in self._cleanup_in_progress:
                continue  # `prune_ended_runs` ends it first; never dropped with a writer still running or draining
            if self._runs.get(r.run_id) is not r:
                continue
            del self._runs[r.run_id]
            if r.owner_id:
                self._clear_active_run_id(owner_id=r.owner_id, run_id=r.run_id)

    def pin_cleanup_locked(self, run_id: str) -> None:
        """044 stage 2: the record is not dropped by a pruning pass until the matching `unpin_cleanup_locked`."""
        self._cleanup_in_progress[run_id] = self._cleanup_in_progress.get(run_id, 0) + 1

    def unpin_cleanup_locked(self, run_id: str) -> None:
        left = self._cleanup_in_progress.get(run_id, 0) - 1
        if left > 0:
            self._cleanup_in_progress[run_id] = left
        else:
            self._cleanup_in_progress.pop(run_id, None)

    async def prune_ended_runs(self) -> None:
        """044 stage 2 (stage 0 finding (n)): the pruning pass also when a run ends, not only on create. An `error` run
        keeps its events writer (`fail_run` does not stop it), so the writers of the records about to be dropped are
        ended first, outside the lock; then the records are dropped under it."""

        with self._lock:
            candidates = [
                (r, int(r._launch_epoch))
                for r in self._ended_overflow_locked()
                if self._has_live_task(getattr(r, "_artifact_events_task", None)) and r.run_id not in self._cleanup_in_progress
            ]
        for r, epoch in candidates:
            # 044 stage 2 (Codex §15): the list was taken before earlier awaits; a candidate the owner resumed or
            # restarted meanwhile (it left the ended state, or a new launch began) keeps its writer. Re-checked under
            # the lock right before the detach, which `stop_events_writer` does before its first await.
            with self._lock:
                still_ended = (
                    self._runs.get(r.run_id) is r
                    and str(r.state) in ("stopped", "error")
                    and int(r._launch_epoch) == epoch
                    and r.run_id not in self._cleanup_in_progress
                )
                if still_ended:
                    self.pin_cleanup_locked(r.run_id)
            if not still_ended:
                continue
            try:
                await self._artifacts.stop_events_writer(r.run_id)
            except NotFoundException:
                continue
            except Exception:
                self._logger.exception("simulator.run.prune_stop_writer_failed run_id=%s", r.run_id)
            finally:
                with self._lock:
                    self.unpin_cleanup_locked(r.run_id)
        with self._lock:
            self._prune_run_records_locked()

    def _start_heartbeat_locked(self, run: RunRecord) -> None:
        run._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(run.run_id),
            name=f"simulator-heartbeat:{run.run_id}",
        )

    async def _ensure_heartbeat(self, run: RunRecord) -> None:
        """One live heartbeat for a `running` run (024 T2416.2): keep a live loop, replace an ended one.

        A cancelled loop still unwinding is awaited outside the lock and the state re-checked, so two
        ticking loops never coexist and a run stopped meanwhile gets none.
        """
        while True:
            with self._lock:
                task = run._heartbeat_task
                if run.state != "running" or (task is not None and not task.done() and not task.cancelling()):
                    return
                if task is None or task.done():
                    self._start_heartbeat_locked(run)
                    return
            await asyncio.wait({task})

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
        intensity_percent: Optional[int] = None,
        owner_id: str = "",
        owner_kind: str = "",
        created_by: Optional[dict] = None,
    ) -> str:
        # Validate scenario exists.
        scenario_for_story = self._get_scenario_raw(scenario_id)
        # 036 B2: a scenario whose story cannot be served as written is refused HERE, by the rule and with the answer of the
        # detail read (`build_story`: 409/E008, SCENARIO_INVALID, the path of every bad field). Nothing is created.
        build_story(scenario_for_story)
        # The run's intensity: the request's value (0 included), else the scenario's `settings.playback.intensity_percent`,
        # else 30. The Simulator UI always sends a number, so the scenario's default is not reached from it yet.
        if intensity_percent is None:
            settings_block = scenario_for_story.get("settings") if isinstance(scenario_for_story, dict) else None
            playback = settings_block.get("playback") if isinstance(settings_block, dict) else None
            from_scenario = playback.get("intensity_percent") if isinstance(playback, dict) else None
            intensity_percent = int(from_scenario) if isinstance(from_scenario, int) and not isinstance(from_scenario, bool) else DEFAULT_RUN_INTENSITY_PERCENT

        run_id = self._new_run_id()
        # 044 `T4405`: the run's private copy of the scenario, its participants under identities of THIS run - bound
        # once, here, before the run exists (a reference to an undeclared participant or an identity that does not fit
        # refuses the run: 409 SCENARIO_INVALID, nothing created). `restart` keeps the copy, so the same participants.
        bound_scenario = bind_run_identities(self._get_scenario_raw(scenario_id), run_id=run_id)
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

        configured = settings.SIMULATOR_REAL_MAX_IN_FLIGHT
        run._real_max_in_flight = max(1, self._real_max_in_flight_default if configured is None else configured)

        # Precompute per-equivalent edges (the runner's topology cache).
        run._scenario_raw = bound_scenario
        run._identity_run_id = run_id
        run._edges_by_equivalent = self._edges_by_equivalent(run._scenario_raw)

        with self._lock:
            # Thread safety: RunRecord is created outside lock (cheap), but all
            # state mutations (_runs, _active_run_id_by_owner) happen atomically
            # inside this lock block.

            # Per-owner limit check (§6.3): owner may have at most
            # SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER active runs (default: 1).
            if owner_id:
                try:
                    max_per_owner = int(self._get_max_active_runs_per_owner() or 0)
                except Exception:
                    max_per_owner = 1

                if max_per_owner > 0:
                    count, most_recent_run_id = self._count_active_runs_for_owner_locked(owner_id)
                    mapped_run_id: Optional[str]
                    try:
                        mapped_run_id = self._get_active_run_id_for_owner(owner_id)
                    except Exception:
                        mapped_run_id = None

                    # The active mapping is the primary source of truth for "has an active run".
                    # But if it points to a run that is no longer active (not in _runs, or
                    # in a terminal state), the mapping is stale and must be cleared before
                    # enforcing the per-owner limit.
                    if mapped_run_id:
                        existing_mapped = self._runs.get(mapped_run_id)
                        _terminal_states = ("stopped", "error")
                        _is_stale = (
                            existing_mapped is None
                            or str(getattr(existing_mapped, "state", "") or "") in _terminal_states
                        )
                        if _is_stale:
                            self._logger.warning(
                                "simulator.run.stale_owner_mapping_detected "
                                "owner_id=%s mapped_run_id=%s run_in_memory=%s run_state=%s — clearing",
                                owner_id,
                                mapped_run_id,
                                existing_mapped is not None,
                                str(getattr(existing_mapped, "state", None)) if existing_mapped else "N/A",
                            )
                            try:
                                self._clear_active_run_id(owner_id=owner_id, run_id=mapped_run_id)
                            except Exception:
                                self._logger.exception(
                                    "simulator.run.stale_mapping_clear_failed owner_id=%s mapped_run_id=%s",
                                    owner_id,
                                    mapped_run_id,
                                )
                            # Recompute count without the now-cleared stale mapping.
                            count, most_recent_run_id = self._count_active_runs_for_owner_locked(owner_id)
                        else:
                            # Mapping is valid (the run is in _runs, so the scan above saw it).
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
            # 044 `T4405`: the run's participant identities carry its id, so an id already held would hand this run
            # the other's participants. Never expected (`new_run_id` is a second-resolution stamp and 32 random bits);
            # refused rather than assumed.
            if run_id in self._runs:
                raise ConflictException("Run id already in use", details={"conflict_kind": "run_id_collision"})

            # Minimal local artifacts (best-effort). Do it before exposing the run.
            self._artifacts.init_run_artifacts(run)

            self._runs[run_id] = run
            # 044 stage 2: a new run has no subscriber yet - its idle window starts now.
            note_subscribers_locked(run, self._idle_now())
            self._set_active_run_id(run_id, owner_id)

            # Start artifacts writer (best-effort; no-op if artifacts disabled).
            self._artifacts.start_events_writer(run_id)

            self._start_heartbeat_locked(run)

            # Emit immediate status event (so SSE clients don't wait for first tick).
            self._publish_run_status(run_id)

            # Prevent unbounded growth in-memory.
            self._prune_run_records_locked()

        await simulator_storage.upsert_run(run)
        await simulator_storage.sync_artifacts(run)
        await self.prune_ended_runs()  # the `error` records the locked pass above had to leave (their writer ran)
        return run_id

    async def pause(self, run_id: str) -> RunStatus:
        run = self._get_run(run_id)
        with self._lock:
            if run.state == "paused":
                pass  # 054-A-7a: a pause the run took itself keeps its reason; the user's pause adds nothing to it
            elif run.state == "running":
                run.state = "paused"
                run.pause_reason = None  # 054-A-7a: the user's pause has no reason of the run's own
            elif run.state in ("stopping", "stopped", "error"):
                # Idempotent, but no-op.
                pass
            else:
                run.state = "paused"
                run.pause_reason = None

        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return self._run_to_status(run)

    async def resume(self, run_id: str) -> RunStatus:
        run = self._get_run(run_id)
        with self._lock:
            if run.state == "running":
                pass
            elif run.state == "paused":
                if run.pause_reason == PAUSE_REASON_SCENARIO_EXHAUSTED:
                    # 054-A-7a: the user takes the run on again - its run of all-refused ticks starts from zero, so the
                    # stall clearing and the pause come only after the next `STALL_CLEARING_AFTER_TICKS` of them.
                    run._real_consec_all_rejected_ticks = 0
                    run._real_stall_clearing_at = None
                run.pause_reason = None
                run.state = "running"
                note_subscribers_locked(run, self._idle_now())  # 044 stage 2: a resume restarts the idle window
            elif run.state in ("stopping", "stopped"):
                # Idempotent: keep stopped
                pass
            else:
                # 054-A-7c: refused before any mutation, like the entry limits below.
                failed_code = (run.last_error or {}).get("code") if run.state == "error" else None
                if failed_code in RESUME_REFUSED_ERROR_CODES:
                    raise ConflictException(
                        "The run failed terminally and cannot be resumed; restart it",
                        details={"reason": "run_failed_terminal", "code": failed_code, "run_id": run_id},
                    )
                self._enforce_entry_limits_locked(run)
                run.state = "running"
                note_subscribers_locked(run, self._idle_now())
                if run.owner_id:
                    self._set_active_run_id(run_id, run.owner_id)
            if run.state == "running":
                # 044 stage 2 (Codex §15): a run resumed while a pruning pass ended its writer gets one again
                # (idempotent: a live writer is kept), so its events are not dropped with a `None` queue.
                self._artifacts.start_events_writer(run_id)

        await self._ensure_heartbeat(run)
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
            # 054-A review FD2: a stop that finds another stop draining the same heartbeat only WAITS for that drain.
            # Cancelling the task again is a repeated cancellation: `commit_resolution._drain_task` then cancels the
            # in-flight COMMIT it shields, and the tick ends with its committed money unknown and unpublished.
            joins_drain = run._stop_draining and heartbeat_task is not None
            if heartbeat_task is not None:
                # From here until the drain below is over, every `run_status` says `stopping` - also the one a second
                # stop (retry, stop-all, shutdown), a new subscription, a pause or an intensity change publishes.
                run._stop_draining = True

            if run.state not in ("stopping", "stopped"):
                run.state = "stopping"
            run.pause_reason = None  # 054-A-7a: the reason of a pause belongs to the paused state only

        self._publish_run_status(run_id)

        # Transition to stopped early so active-run limits are released promptly.
        with self._lock:
            if run.state != "stopped":
                run.state = "stopped"
            if run.stopped_at is None:
                run.stopped_at = self._utc_now()

        # Release per-owner active run slot.
        try:
            self._clear_active_run_id(owner_id=run.owner_id, run_id=run_id)
        except Exception:
            self._logger.exception(
                "simulator.run.clear_active_run_id_failed run_id=%s — "
                "owner mapping may remain stale; will be auto-cleaned on next create_run()",
                run_id,
            )
            self._logger.warning(
                "simulator.run.stale_mapping_risk owner_id=%s run_id=%s — "
                "clear_active_run_id raised; stale mapping will be detected in create_run()",
                run.owner_id,
                run_id,
            )

        # Best-effort: make sure background tasks are stopped even if persistence fails.
        if heartbeat_task is not None and joins_drain:
            try:
                await asyncio.shield(heartbeat_task)  # this caller's own cancellation must not reach the task
            except asyncio.CancelledError:
                if not heartbeat_task.done():
                    raise  # this caller was cancelled: the drain and its terminal publication stay the owner's
            except Exception:
                self._logger.exception("simulator.run.stop_heartbeat_failed run_id=%s", run_id)
        elif heartbeat_task is not None:
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

        # 054-A-1: the terminal `stopped` is published only AFTER the heartbeat is drained. A cancelled tick whose
        # money COMMIT already landed still publishes its `tx.updated`/`clearing.done` and settles its counters on the
        # way out; published before the drain, `stopped` got an older event id than those events, the live SSE stream
        # (which returns on `stopped`) never delivered them and `stopped` carried the counters of before the commit.
        # The state and the owner slot above are still released early - only the publication waits.
        with self._lock:
            run._stop_draining = False  # 054-A review FD2: the drain is over; `stopped` may be said
        self._publish_run_status(run_id)

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
            # Refuse before any mutation: a refused restart must leave the run as it was (024 T2416.2).
            # Restore active mapping removed during stop(). Without this,
            # get_active_run_id(owner_id) → None and admin stop-all won't see
            # the restarted run (§FIX-CR2).
            if run.owner_id:
                existing: Optional[str] = None
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
            self._enforce_entry_limits_locked(run)
            if run.owner_id:
                # _set_active_run_id is always set (non-Optional Callable)
                self._set_active_run_id(run_id, run.owner_id)

            run.sim_time_ms = 0
            run.tick_index = 0
            # 053: cached quantiles and same-tick clearing exemptions belong to
            # the old tick sequence. Preserve the substantive clearing history.
            run._real_viz_by_eq.clear()
            for history in run._edge_clearing_history.values():
                history.last_clearing_tick = -1
            # 034 `F-034-1`: the tick numbers start again under the same `run_id`, so the launch is what tells this
            # tick's payments from the stored payments of the same tick number before the restart.
            run._launch_epoch += 1
            # 036 B1 (BACKLOG 034-3 (g)): the story starts over - the scenario's events are due again and the scripted
            # ones are new operations of the new epoch. The debts of the launch before stay (a restart undoes nothing;
            # a story replayed from scratch is a new run).
            run._real_fired_scenario_event_indexes.clear()
            run._real_story_progress.clear()
            # 024 Sh6 fix-delta (§15 P2): the flush mark and the tail it refers to belong to the old tick
            # sequence; a retained mark >= the new tick numbers made the final flush skip a lost new tail.
            run._real_last_tick_storage_flushed_tick = -1
            run._real_last_tick_storage_payload = None
            run.errors_total = 0
            run.last_error = None
            run.last_event_type = None
            run.current_phase = None
            run.queue_depth = 0
            run.ops_sec = 0.0
            # 049 S2a: an empty share no longer clears the stall run nor the run of tick failures (`is_empty_share`),
            # and the failures are cleared only by a tick that succeeds (`tick.py`), so the restarted launch starts
            # from zero here, like `errors_total` above - the old launch's counts must not greet the new one.
            run._real_consec_all_rejected_ticks = 0
            run._real_stall_clearing_at = None  # 054-A-7a: with the counter it belongs to
            run.pause_reason = None
            run._real_consec_tick_failures = 0
            run._real_consec_money_no_progress_ticks = 0
            run.stopped_at = None
            run.state = "running"
            run.started_at = self._utc_now()
            # 044 stage 2: the restarted run's idle window starts now.
            note_subscribers_locked(run, self._idle_now())

            # Keep buffer but prune to avoid unbounded growth across long sessions.
            self._sse.prune_event_buffer_locked(run)
            self._artifacts.start_events_writer(run_id)  # stop() ended it (F-028-4); idempotent

        await self._ensure_heartbeat(run)
        self._publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return self._run_to_status(run)
