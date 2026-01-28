from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence, TypeVar

from sqlalchemy import delete, func, select


def _repo_root() -> Path:
    # scripts/cleanup_simulator_runs.py -> repo root
    return Path(__file__).resolve().parents[1]


# Ensure `import app...` works regardless of current working directory.
_ROOT = _repo_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from app.db.models.simulator_storage import (  # noqa: E402
    SimulatorRun,
    SimulatorRunArtifact,
    SimulatorRunBottleneck,
    SimulatorRunMetric,
)
from app.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_simulator_runs_dir(repo_root: Path) -> Path:
    return repo_root / ".local-run" / "simulator" / "runs"


T = TypeVar("T")


def _chunks(items: Sequence[T], n: int) -> Iterable[Sequence[T]]:
    if n <= 0:
        raise ValueError("chunk size must be > 0")
    for i in range(0, len(items), n):
        yield items[i : i + n]


@dataclass(frozen=True)
class CleanupPlan:
    run_ids: list[str]
    artifacts_dirs: list[Path]


async def _plan_db_deletions(*, cutoff: datetime) -> list[str]:
    async with AsyncSessionLocal() as session:
        is_inactive = SimulatorRun.state.in_(["idle", "stopped", "error"])
        effective_time = func.coalesce(SimulatorRun.stopped_at, SimulatorRun.created_at)
        rows = (
            await session.execute(
                select(SimulatorRun.run_id)
                .where(is_inactive)
                .where(effective_time < cutoff)
                .order_by(effective_time.asc())
            )
        ).scalars().all()
    return [str(r) for r in rows]


async def _get_all_db_run_ids() -> set[str]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(SimulatorRun.run_id))).scalars().all()
    return {str(r) for r in rows}


async def _apply_db_deletions(*, run_ids: Sequence[str], chunk_size: int = 500) -> int:
    if not run_ids:
        return 0

    deleted_total = 0
    async with AsyncSessionLocal() as session:
        for batch in _chunks(list(run_ids), chunk_size):
            # NOTE: There are no FK cascades in the MVP schema (SQLite-friendly),
            # so we delete dependent tables manually.
            await session.execute(delete(SimulatorRunMetric).where(SimulatorRunMetric.run_id.in_(batch)))
            await session.execute(delete(SimulatorRunBottleneck).where(SimulatorRunBottleneck.run_id.in_(batch)))
            await session.execute(delete(SimulatorRunArtifact).where(SimulatorRunArtifact.run_id.in_(batch)))
            res = await session.execute(delete(SimulatorRun).where(SimulatorRun.run_id.in_(batch)))
            # res.rowcount can be None depending on dialect
            deleted_total += int(res.rowcount or 0)
            await session.commit()

    return deleted_total


def _plan_artifacts_deletions_by_mtime(*, runs_dir: Path, cutoff: datetime) -> list[Path]:
    if not runs_dir.exists():
        return []

    out: list[Path] = []
    cutoff_ts = cutoff.timestamp()

    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except Exception:
            continue
        if mtime < cutoff_ts:
            out.append(child)

    return out


def _plan_artifacts_dirs_by_run_ids(*, runs_dir: Path, run_ids: Sequence[str]) -> list[Path]:
    return [runs_dir / run_id for run_id in run_ids if (runs_dir / run_id).is_dir()]


def _plan_orphan_artifacts_dirs(
    *,
    runs_dir: Path,
    known_run_ids: set[str],
    cutoff: datetime,
) -> list[Path]:
    if not runs_dir.exists():
        return []
    cutoff_ts = cutoff.timestamp()
    out: list[Path] = []
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name in known_run_ids:
            continue
        try:
            if child.stat().st_mtime < cutoff_ts:
                out.append(child)
        except Exception:
            continue
    return out


def _delete_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup old simulator runs (DB + local artifacts)")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=30,
        help="Retention in days (older runs will be deleted). Default: 30",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be deleted",
    )
    parser.add_argument(
        "--db",
        action="store_true",
        default=True,
        help="Cleanup DB rows (default: enabled)",
    )
    parser.add_argument(
        "--no-db",
        dest="db",
        action="store_false",
        help="Skip DB cleanup",
    )
    parser.add_argument(
        "--artifacts",
        action="store_true",
        default=True,
        help="Cleanup local artifacts under .local-run (default: enabled)",
    )
    parser.add_argument(
        "--no-artifacts",
        dest="artifacts",
        action="store_false",
        help="Skip artifacts cleanup",
    )
    parser.add_argument(
        "--delete-orphan-dirs",
        action="store_true",
        help="When DB is available, also delete artifact dirs that are not present in DB and are older than cutoff.",
    )

    args = parser.parse_args()

    retention_days = int(args.retention_days)
    if retention_days < 0:
        raise SystemExit("--retention-days must be >= 0")

    cutoff = _utc_now() - timedelta(days=retention_days)
    repo_root = _repo_root()
    runs_dir = _local_simulator_runs_dir(repo_root)

    planned_run_ids: list[str] = []
    db_usable = False
    if args.db:
        if not settings.SIMULATOR_DB_ENABLED:
            print("DB: skipped (SIMULATOR_DB_ENABLED=false)")
        else:
            try:
                planned_run_ids = await _plan_db_deletions(cutoff=cutoff)
                db_usable = True
            except Exception as e:
                msg = str(e)
                if "no such table" in msg and "simulator_runs" in msg:
                    print("DB: skipped (simulator_runs table not found)")
                else:
                    print(f"[WARN] DB cleanup skipped (DB unavailable): {e}")
                planned_run_ids = []

    planned_artifacts_dirs: list[Path] = []
    if args.artifacts:
        if db_usable and planned_run_ids:
            planned_artifacts_dirs = _plan_artifacts_dirs_by_run_ids(runs_dir=runs_dir, run_ids=planned_run_ids)
            if args.delete_orphan_dirs:
                try:
                    known_ids = await _get_all_db_run_ids()
                    planned_artifacts_dirs.extend(
                        _plan_orphan_artifacts_dirs(runs_dir=runs_dir, known_run_ids=known_ids, cutoff=cutoff)
                    )
                except Exception as e:
                    print(f"[WARN] Orphan artifacts scan skipped: {e}")
        else:
            planned_artifacts_dirs = _plan_artifacts_deletions_by_mtime(runs_dir=runs_dir, cutoff=cutoff)

    print(f"Retention days: {retention_days}")
    print(f"Cutoff (UTC): {cutoff.isoformat()}")

    if args.db:
        print(f"DB: would delete runs: {len(planned_run_ids)}")
        if planned_run_ids:
            print(f"DB: oldest candidate run_id: {planned_run_ids[0]}")

    if args.artifacts:
        print(f"Artifacts: would delete run dirs: {len(planned_artifacts_dirs)}")
        if planned_artifacts_dirs:
            print(f"Artifacts: oldest candidate dir: {planned_artifacts_dirs[0]}")

    if args.dry_run:
        print("Dry run: no changes applied")
        return 0

    deleted_db = 0
    if args.db and planned_run_ids:
        try:
            deleted_db = await _apply_db_deletions(run_ids=planned_run_ids)
            print(f"DB: deleted runs: {deleted_db}")
        except Exception as e:
            print(f"[WARN] DB deletion failed: {e}")

    deleted_dirs = 0
    if args.artifacts and planned_artifacts_dirs:
        for d in planned_artifacts_dirs:
            try:
                await asyncio.to_thread(_delete_dir, d)
                deleted_dirs += 1
            except Exception:
                continue
        print(f"Artifacts: deleted dirs: {deleted_dirs}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
