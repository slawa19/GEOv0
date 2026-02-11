from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete

import app.core.simulator.storage as simulator_storage
import app.db.session as db_session
from app.core.simulator.helpers import artifact_content_type, artifact_sha256
from app.core.simulator.models import RunRecord
from app.db.models.simulator_storage import SimulatorRunArtifact
from app.schemas.simulator import SIMULATOR_API_VERSION, ArtifactIndex, ArtifactItem
from app.utils.exceptions import NotFoundException


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

    def cleanup_old_runs(self, *, ttl_hours: int) -> None:
        """Best-effort cleanup for `.local-run/simulator/runs/*`.

        Only touches local filesystem artifacts; never affects DB state.
        """

        ttl_hours = int(ttl_hours or 0)
        if ttl_hours <= 0:
            return

        base = (self._local_state_dir() / "runs").resolve()
        if not base.exists() or not base.is_dir():
            return

        cutoff = time.time() - (ttl_hours * 3600)

        for p in sorted(base.iterdir()):
            if not p.is_dir():
                continue

            try:
                rp = p.resolve()
                if not str(rp).startswith(str(base)):
                    continue
                mtime = float(rp.stat().st_mtime)
                if mtime >= cutoff:
                    continue
                shutil.rmtree(rp, ignore_errors=False)
            except FileNotFoundError:
                continue
            except Exception:
                self._logger.exception("simulator.artifacts.cleanup_failed path=%s", str(p))

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

        sha_max_bytes = int(os.getenv("SIMULATOR_ARTIFACT_SHA_MAX_BYTES", "524288") or "524288")

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
            artifact_path=str(base),
            items=items,
            bundle_url=(
                f"/api/v1/simulator/runs/{run_id}/artifacts/bundle.zip" if (base / "bundle.zip").exists() else None
            ),
        )

    def get_artifact_path(self, *, run_id: str, name: str) -> Path:
        run = self._get_run(run_id)
        base = run.artifacts_dir
        if base is None:
            raise NotFoundException("Artifact not found")
        p = (base / name).resolve()
        if not str(p).startswith(str(base.resolve())):
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
            buf.clear()
            try:
                await asyncio.to_thread(_append_text, path, text)
            except Exception:
                # Best-effort: drop on IO errors.
                self._logger.exception("simulator.artifacts.events_writer_append_failed run_id=%s", run_id)
                continue

    def enqueue_event_artifact(self, run_id: str, payload: dict[str, Any]) -> None:
        run = self._get_run(run_id)
        q = run._artifact_events_queue
        if q is None:
            return
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        except Exception:
            self._logger.exception("simulator.artifacts.events_json_encode_failed run_id=%s", run_id)
            return
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            # Best-effort drop.
            return

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
            return

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
