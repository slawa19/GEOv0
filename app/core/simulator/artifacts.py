from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete

import app.core.simulator.storage as simulator_storage
import app.db.session as db_session
from app.config import settings
from app.core.simulator.helpers import artifact_content_type, artifact_sha256
from app.core.simulator.models import RunRecord
from app.db.models.simulator_storage import SimulatorRunArtifact
from app.schemas.simulator import SIMULATOR_API_VERSION, ArtifactIndex, ArtifactItem
from app.utils.exceptions import NotFoundException

# ── The retention of run directories: its constants (034 S1c; AGENTS.md §12) ──────────────────────────────────────
# The TTL and the limit are settings (`SIMULATOR_ARTIFACTS_TTL_HOURS`, `SIMULATOR_ARTIFACTS_MAX_RUNS`, both off at 0).
#: A run directory written within this many seconds is never removed, by either rule, whoever wrote it.
RECENT_WRITE_GRACE_SEC = 3600
#: The states of a run in THIS process's registry whose directory the retention may remove. Every other state can
#: still write, or be resumed into writing, without re-creating the directory.
_PRUNABLE_STATES = frozenset({"stopped"})
#: A run directory being deleted is first renamed to `<run_id>` + this.
_DELETING_SUFFIX = ".deleting"


def _last_write(run_dir: Path) -> float:
    """The newest modification time in a run directory: the directory, `artifacts/` and the files in it."""

    newest = float(run_dir.stat().st_mtime)
    stack = [run_dir]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                try:
                    newest = max(newest, float(entry.stat(follow_symlinks=False).st_mtime))
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                except FileNotFoundError:
                    continue
    return newest


class ArtifactsManager:
    def __init__(
        self,
        *,
        lock,
        runs: dict[str, RunRecord],
        local_state_dir,
        utc_now,
        db_enabled,
        logger,
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._local_state_dir = local_state_dir
        self._utc_now = utc_now
        self._db_enabled = db_enabled
        self._logger = logger
        # Run directories the retention could not remove, by name: the traceback is logged once per name.
        self._cleanup_failures: set[str] = set()

    def _get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    def init_run_artifacts(self, run: RunRecord) -> None:
        """Initialize artifacts directory and minimal baseline files.

        Best-effort: on any failure, disables artifacts for this run.
        """
        try:
            artifacts_dir = self._local_state_dir() / "runs" / run.run_id / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            run.artifacts_dir = artifacts_dir

            def _atomic_write_text(path: Path, text: str) -> None:
                tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(path)

            _atomic_write_text(
                artifacts_dir / "last_tick.json",
                json.dumps({"tick_index": 0, "sim_time_ms": 0}, ensure_ascii=False, indent=2),
            )
            (artifacts_dir / "status.json").write_text(
                json.dumps(
                    {
                        "api_version": SIMULATOR_API_VERSION,
                        "run_id": run.run_id,
                        "scenario_id": run.scenario_id,
                        "mode": run.mode,
                        "created_at": self._utc_now().isoformat(),
                        "seed": run.seed,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # Raw events export (NDJSON). The writer task appends lines.
            (artifacts_dir / "events.ndjson").write_text("", encoding="utf-8")
        except Exception:
            self._logger.exception("simulator.artifacts.init_failed run_id=%s", getattr(run, "run_id", ""))
            run.artifacts_dir = None

    def cleanup_old_runs(self, *, ttl_hours: int, max_runs: int = 0) -> None:
        """Best-effort retention of `<state dir>/runs/*`: a TTL and a limit of run directories (034 S1c, `F-034-8`).

        A run directory NOT WRITTEN for more than `ttl_hours` is removed; then, if more than `max_runs` run
        directories are left, the least recently written are removed until `max_runs` are, or until nothing
        removable is left. Either rule is off at 0. Called when the runtime starts and right after a run's
        artifacts are finalized. Only touches local filesystem artifacts; never affects DB state.

        THE AGE OF A RUN is the newest write anywhere in its directory (`_last_write`), not the directory's own
        modification time: a run appends to `runs/<id>/artifacts/events.ndjson` for as long as it lives, and that
        moves nothing on `runs/<id>` itself.

        NEVER REMOVED, and each for its own reason:
        * a run of THIS process that may still write or be written to again - every state but `stopped`
          (`_PRUNABLE_STATES`): `resume` takes an `error` or `idle` run back to `running` without restarting its
          writer (`run_lifecycle.py`, `resume`), so its directory must stay; a `stopped` run can only be brought
          back by `restart`, which re-creates what it needs (`start_events_writer`);
        * a directory written within `RECENT_WRITE_GRACE_SEC` by ANYONE: the state directory may be shared by
          other processes whose runs this registry knows nothing about, and the file system is the one thing they
          have in common. It counts against the limit and is not removed for it;
        * anything that is not a directory directly under `runs/` resolving inside it - a file there, a link out
          of it, the scenarios store beside it.

        NOT SEEN (and so not protected): a run of ANOTHER process that has written nothing for longer than the
        grace - a paused one, typically. There is no lock between processes here; the limit of this rule is that.

        A directory is renamed aside (`<id>.deleting`) before it is deleted, so that one still open somewhere
        (Windows refuses the rename) is left whole instead of half-deleted; a leftover `*.deleting` is removed by
        the next call.
        """

        ttl_hours, max_runs = int(ttl_hours or 0), int(max_runs or 0)
        if ttl_hours <= 0 and max_runs <= 0:
            return

        base = (self._local_state_dir() / "runs").resolve()
        if not base.exists() or not base.is_dir():
            return

        with self._lock:
            may_still_write = {
                str(run_id) for run_id, run in self._runs.items()
                if str(getattr(run, "state", "")) not in _PRUNABLE_STATES
            }

        now = time.time()
        expired_before = now - (ttl_hours * 3600)
        recent_after = now - RECENT_WRITE_GRACE_SEC
        left: list[tuple[float, Path]] = []
        for p in sorted(base.iterdir()):
            try:
                if not p.is_dir() or not p.resolve().is_relative_to(base) or p.resolve() != base / p.name:
                    continue
                if p.name.endswith(_DELETING_SUFFIX):
                    self._delete_aside(p)
                    continue
                if p.name in may_still_write:
                    continue
                written = _last_write(p)
            except FileNotFoundError:
                continue
            except Exception:
                self._cleanup_failed(p.name)
                continue
            removable = written < recent_after
            if ttl_hours > 0 and removable and written < expired_before and self._remove_run_dir(p):
                continue
            left.append((written if removable else float("inf"), p))

        if max_runs > 0 and len(left) > max_runs:
            left.sort(key=lambda entry: (entry[0], entry[1].name))
            for written, p in left[: len(left) - max_runs]:
                if written != float("inf"):
                    self._remove_run_dir(p)

    def _remove_run_dir(self, path: Path) -> bool:
        """Remove one run directory whole or not at all: renamed aside first, then deleted."""

        aside = path.with_name(path.name + _DELETING_SUFFIX)
        try:
            os.rename(path, aside)
        except FileNotFoundError:
            return True
        except Exception:
            # In use (a download, a writer of another process): nothing of it was touched.
            self._cleanup_failed(path.name)
            return False
        self._delete_aside(aside)
        return True

    def _delete_aside(self, aside: Path) -> None:
        try:
            shutil.rmtree(aside, ignore_errors=False)
        except FileNotFoundError:
            return
        except Exception:
            self._cleanup_failed(aside.name)

    def _cleanup_failed(self, name: str) -> None:
        """Log a directory the retention could not remove: by its NAME (the run id), never its absolute path
        (AGENTS.md §12), and with the traceback once per name - the retention runs after every finalize, and a
        directory that stays busy would otherwise repeat it each time."""

        first = name not in self._cleanup_failures
        self._cleanup_failures.add(name)
        self._logger.log(
            logging.WARNING if first else logging.DEBUG,
            "simulator.artifacts.cleanup_failed run_dir=%s",
            name,
            exc_info=first,
        )

    async def list_artifacts(self, *, run_id: str) -> ArtifactIndex:
        run = self._get_run(run_id)
        base = run.artifacts_dir
        if base is None or not base.exists():
            return ArtifactIndex(
                api_version=SIMULATOR_API_VERSION,
                run_id=run_id,
                artifact_path=None,
                items=[],
                bundle_url=None,
            )

        sha_max_bytes = settings.SIMULATOR_ARTIFACT_SHA_MAX_BYTES

        items: list[ArtifactItem] = []
        for p in sorted(base.iterdir()):
            if not p.is_file():
                continue
            url = f"/api/v1/simulator/runs/{run_id}/artifacts/{p.name}"
            sha = None
            size = None
            try:
                size = int(p.stat().st_size)
                if size <= sha_max_bytes:
                    sha = artifact_sha256(p)
            except Exception:
                pass
            items.append(
                ArtifactItem(
                    name=p.name,
                    url=url,
                    content_type=artifact_content_type(p.name),
                    size_bytes=size,
                    sha256=sha,
                )
            )

        if self._db_enabled():
            try:
                async with db_session.AsyncSessionLocal() as session:
                    await session.execute(delete(SimulatorRunArtifact).where(SimulatorRunArtifact.run_id == run_id))
                    rows = [
                        SimulatorRunArtifact(
                            run_id=run_id,
                            name=i.name,
                            content_type=i.content_type,
                            size_bytes=i.size_bytes,
                            sha256=i.sha256,
                            storage_url=str(i.url),
                        )
                        for i in items
                    ]
                    session.add_all(rows)
                    await session.commit()
            except Exception:
                self._logger.exception("simulator.artifacts.db_sync_failed run_id=%s", run_id)

        return ArtifactIndex(
            api_version=SIMULATOR_API_VERSION,
            run_id=run_id,
            artifact_path=self._public_artifact_path(base),
            items=items,
            bundle_url=(
                f"/api/v1/simulator/runs/{run_id}/artifacts/bundle.zip" if (base / "bundle.zip").exists() else None
            ),
        )

    def _public_artifact_path(self, base: Path) -> str | None:
        """The artifacts directory relative to the simulator's state root, POSIX; never absolute.

        SIM-11 (programme 024): this value goes out over the API to any run owner, and AGENTS.md
        section 12 forbids handing out absolute local paths. A directory outside the root has no
        relative name, so the field is omitted (it is nullable) rather than leaking.
        """
        try:
            return base.resolve().relative_to(self._local_state_dir().resolve()).as_posix()
        except ValueError:
            return None

    def get_artifact_path(self, *, run_id: str, name: str) -> Path:
        run = self._get_run(run_id)
        base = run.artifacts_dir
        if base is None:
            raise NotFoundException("Artifact not found")
        p = (base / name).resolve()
        # Containment over both resolved ends (fix-delta D1): a string prefix admitted a sibling
        # directory sharing it (`artifacts_x`), reachable through `..` or a `%5C` backslash.
        if not p.is_relative_to(base.resolve()) or p == base.resolve():
            raise NotFoundException("Artifact not found")
        if not p.exists() or not p.is_file():
            raise NotFoundException("Artifact not found")
        return p

    def start_events_writer(self, run_id: str) -> None:
        run = self._get_run(run_id)
        base = run.artifacts_dir
        if base is None:
            return
        path = base / "events.ndjson"
        try:
            # Ensure file exists.
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")
        except Exception:
            self._logger.exception("simulator.artifacts.events_writer_init_failed run_id=%s", run_id)
            return

        with self._lock:
            if run._artifact_events_task is not None and not run._artifact_events_task.done():
                return
            q: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=10_000)
            run._artifact_events_queue = q
            run._artifact_events_task = asyncio.create_task(
                self._events_writer_loop(run_id=run_id, path=path, queue=q),
                name=f"simulator-artifacts-events:{run_id}",
            )

    async def stop_events_writer(self, run_id: str) -> None:
        run = self._get_run(run_id)
        task: Optional[asyncio.Task[None]]
        q: Optional[asyncio.Queue[Optional[str]]]
        with self._lock:
            task = run._artifact_events_task
            q = run._artifact_events_queue
            run._artifact_events_task = None
            run._artifact_events_queue = None

        if task is None:
            return

        if q is not None:
            try:
                q.put_nowait(None)
            except Exception:
                self._logger.exception("simulator.artifacts.events_writer_stop_enqueue_failed run_id=%s", run_id)

        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except Exception:
                self._logger.exception("simulator.artifacts.events_writer_stop_cancel_failed run_id=%s", run_id)
        except Exception:
            self._logger.exception("simulator.artifacts.events_writer_stop_failed run_id=%s", run_id)
            return

    async def _events_writer_loop(
        self,
        *,
        run_id: str,
        path: Path,
        queue: "asyncio.Queue[Optional[str]]",
    ) -> None:
        # Batch writes and offload to a thread to avoid blocking the event loop.
        def _append_text(p: Path, text: str) -> None:
            with p.open("a", encoding="utf-8") as f:
                f.write(text)

        buf: list[str] = []
        while True:
            item = await queue.get()
            if item is None:
                break
            buf.append(item)

            # Drain quickly to batch.
            for _ in range(1000):
                try:
                    nxt = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    # Re-queue sentinel for the outer loop.
                    await queue.put(None)
                    break
                buf.append(nxt)

            text = "".join(buf)
            lines = len(buf)
            buf.clear()
            try:
                await asyncio.to_thread(_append_text, path, text)
            except Exception:
                # Best-effort: the batch is dropped on an IO error - and counted (034 `F-034-8`).
                self._logger.exception("simulator.artifacts.events_writer_append_failed run_id=%s", run_id)
                self._count_dropped_events(run_id, reason="write_failed", count=lines)
                continue

    def _count_dropped_events(self, run_id: str, *, reason: str, count: int = 1) -> None:
        """034 `F-034-8`: an event the writer could not record is COUNTED - on the run, on the `/metrics` counter
        `geo_simulator_artifact_events_dropped_total{reason}` - and the loss is logged: at the run's first drop
        and then once per thousand, with the run's total. Never raises; a drop stays a drop.

        THREE REASONS ARE COUNTED, and nothing else: `queue_full` (the writer's queue had no room), `write_failed`
        (a batch could not be appended; every line of it) and `encode_failed` (the event is not JSON-serialisable).
        NOT COUNTED - so a zero here does not say "nothing is missing from `events.ndjson`": an event that arrives
        when the run has no writer (before it starts, after the run is stopped - `enqueue_event_artifact` returns
        on `q is None`), and events still queued when `stop_events_writer` cancels a writer that did not finish in
        its 2 s."""

        total = count
        try:
            with self._lock:
                run = self._runs.get(run_id)
                if run is not None:
                    run._artifact_events_dropped += count
                    total = int(run._artifact_events_dropped)
            from app.utils import metrics

            metrics.SIMULATOR_ARTIFACT_EVENTS_DROPPED_TOTAL.labels(reason=reason).inc(count)
        except Exception:
            self._logger.debug("simulator.artifacts.drop_count_failed run_id=%s", run_id, exc_info=True)
        if total == count or total // 1000 != (total - count) // 1000:
            self._logger.warning(
                "simulator.artifacts.events_dropped run_id=%s reason=%s dropped_total=%d", run_id, reason, total
            )

    def enqueue_event_artifact(self, run_id: str, payload: dict[str, Any]) -> None:
        run = self._get_run(run_id)
        q = run._artifact_events_queue
        if q is None:
            return
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        except Exception:
            self._logger.exception("simulator.artifacts.events_json_encode_failed run_id=%s", run_id)
            self._count_dropped_events(run_id, reason="encode_failed")
            return
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            # Best-effort drop: the event is not recorded in `events.ndjson`, and that is counted.
            self._count_dropped_events(run_id, reason="queue_full")

    async def finalize_run_artifacts(self, *, run_id: str, status_payload: dict[str, Any]) -> None:
        run = self._get_run(run_id)
        base = run.artifacts_dir
        if base is None or not base.exists():
            return

        summary_payload: dict[str, Any] = {
            "api_version": SIMULATOR_API_VERSION,
            "generated_at": self._utc_now().isoformat(),
            "run_id": run_id,
            "scenario_id": run.scenario_id,
            "mode": run.mode,
            "state": run.state,
            "status": status_payload,
        }

        def _write_json(path: Path, payload: dict[str, Any]) -> None:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        def _build_bundle(artifacts_dir: Path) -> None:
            bundle = artifacts_dir / "bundle.zip"
            with zipfile.ZipFile(bundle, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(artifacts_dir.iterdir()):
                    if not p.is_file():
                        continue
                    if p.name == "bundle.zip":
                        continue
                    zf.write(p, arcname=p.name)

        try:
            await asyncio.to_thread(_write_json, base / "status.json", status_payload)
            await asyncio.to_thread(_write_json, base / "summary.json", summary_payload)
            await asyncio.to_thread(_build_bundle, base)
        except Exception:
            self._logger.exception("simulator.artifacts.finalize_failed run_id=%s", run_id)
            return

        try:
            await simulator_storage.sync_artifacts(run)
        except Exception:
            self._logger.exception("simulator.artifacts.sync_failed run_id=%s", run_id)

        # 034 S1c, `F-034-8` (AGENTS.md §12): the retention is applied right after the write, not only at start.
        # In a thread (it walks and deletes directories), and a failure of it never costs what was just written.
        try:
            await asyncio.to_thread(
                self.cleanup_old_runs,
                ttl_hours=settings.SIMULATOR_ARTIFACTS_TTL_HOURS,
                max_runs=settings.SIMULATOR_ARTIFACTS_MAX_RUNS,
            )
        except Exception:
            self._logger.warning("simulator.artifacts.cleanup_failed run_id=%s", run_id, exc_info=True)

    def write_real_tick_artifact(self, run: RunRecord, payload: dict[str, Any]) -> None:
        base = run.artifacts_dir
        if base is None:
            return
        try:
            path = base / "last_tick.json"
            tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception:
            self._logger.exception("simulator.artifacts.last_tick_write_failed run_id=%s", getattr(run, "run_id", ""))
            return
