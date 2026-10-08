"""034 S1b, F-034-8: the simulator's run artifacts have a TTL and a limit that are applied after a write, and an
event the artifact writer could not take is counted.

WHAT WAS WRONG (on `0f248b9c`). `SIMULATOR_ARTIFACTS_TTL_HOURS` defaulted to 0, which switches the only cleanup off;
there was no limit on the number of run directories at all; the cleanup ran once, when the runtime was constructed,
never after an artifact was written; and an event that did not fit the writer's queue was dropped without a trace, so
"no events were lost" and "nobody counted" looked the same (AGENTS.md §1, §12).

THE STAND is the real `ArtifactsManager` over a temporary state directory - no database, no runtime singleton. Run
directories get their modification times from `os.utime`, never from waiting.

WHAT THESE TESTS DO NOT SEE: the size of a single `events.ndjson` (it is still unbounded while its run lives), and
artifacts written by another process on the same state directory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from prometheus_client import REGISTRY

import app.core.simulator.storage as simulator_storage
from app.config import Settings, settings
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import RunRecord

_LOG = logging.getLogger("tests.p034.s1b.artifacts")
_DROPPED = "geo_simulator_artifact_events_dropped_total"


def _manager(state_dir: Path, runs: dict[str, RunRecord]) -> ArtifactsManager:
    return ArtifactsManager(
        lock=threading.RLock(), runs=runs, local_state_dir=lambda: state_dir,
        utc_now=lambda: datetime.now(timezone.utc), db_enabled=lambda: False, logger=_LOG,
    )


def _run_dir(state_dir: Path, run_id: str, *, age_s: float) -> Path:
    """A run directory with one artifact in it, last modified `age_s` seconds ago."""

    artifacts = state_dir / "runs" / run_id / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "events.ndjson").write_text("{}\n", encoding="utf-8")
    stamp = time.time() - age_s
    for path in (artifacts, artifacts.parent):
        os.utime(path, (stamp, stamp))
    return artifacts


def _registered(runs: dict[str, RunRecord], run_id: str, state: str, artifacts: Path) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="s", mode="fixtures", state=state)
    run.artifacts_dir = artifacts
    runs[run_id] = run
    return run


def _run_dirs(state_dir: Path) -> set[str]:
    return {p.name for p in (state_dir / "runs").iterdir() if p.is_dir()}


def test_a_ttl_and_a_run_limit_are_declared_by_default() -> None:
    """The declaration itself (AGENTS.md §12): both are on unless an operator switches them off."""

    fields = Settings.model_fields
    ttl = fields["SIMULATOR_ARTIFACTS_TTL_HOURS"].default
    limit = fields["SIMULATOR_ARTIFACTS_MAX_RUNS"].default if "SIMULATOR_ARTIFACTS_MAX_RUNS" in fields else None
    assert isinstance(ttl, int) and ttl > 0 and isinstance(limit, int) and limit > 0, (
        f"simulator run artifacts by default: TTL {ttl!r} h (0 switches the cleanup off), limit of run "
        f"directories {limit!r} (None: not declared anywhere). Expected both positive"
    )


@pytest.mark.asyncio
async def test_finalizing_a_run_prunes_the_expired_and_the_surplus_run_directories(tmp_path, monkeypatch) -> None:
    """After `finalize_run_artifacts` - the write - with a TTL of 1 h and a limit of 3: the expired directory is
    gone and three run directories besides the active run's remain, the newest ones, the finalized run among them.

    COUNTER-CHECK in the same state directory: the directory of an ACTIVE run is kept although it is older than
    the TTL and beyond the limit, and what is not a run directory - a file under `runs/`, the scenarios store
    beside it - is not touched."""

    monkeypatch.setattr(simulator_storage, "sync_artifacts", AsyncMock())
    monkeypatch.setattr(settings, "SIMULATOR_ARTIFACTS_TTL_HOURS", 1)
    if "SIMULATOR_ARTIFACTS_MAX_RUNS" in Settings.model_fields:
        monkeypatch.setattr(settings, "SIMULATOR_ARTIFACTS_MAX_RUNS", 3)
    runs: dict[str, RunRecord] = {}
    manager = _manager(tmp_path, runs)

    _run_dir(tmp_path, "expired", age_s=10 * 3600)
    for minutes, name in ((40, "fresh-oldest"), (30, "fresh-older"), (20, "fresh-newer")):
        _run_dir(tmp_path, name, age_s=minutes * 60)
    _registered(runs, "active-and-old", "running", _run_dir(tmp_path, "active-and-old", age_s=10 * 3600))
    finalized = _registered(runs, "finalized", "stopped", _run_dir(tmp_path, "finalized", age_s=0))
    (tmp_path / "runs" / "README.txt").write_text("not a run", encoding="utf-8")
    scenario = tmp_path / "scenarios" / "uploaded" / "scenario.json"
    scenario.parent.mkdir(parents=True)
    scenario.write_text("{}", encoding="utf-8")
    old = time.time() - 10 * 3600
    os.utime(scenario.parent, (old, old))

    await manager.finalize_run_artifacts(run_id=finalized.run_id, status_payload={"state": "stopped"})

    # Controls: the write happened, and what must never be pruned is still there.
    assert (finalized.artifacts_dir / "summary.json").is_file() and (finalized.artifacts_dir / "bundle.zip").is_file()
    assert (tmp_path / "runs" / "active-and-old" / "artifacts" / "events.ndjson").is_file(), "an ACTIVE run's artifacts were pruned"
    assert (tmp_path / "runs" / "README.txt").is_file() and scenario.is_file(), "something that is not a run directory was pruned"

    assert _run_dirs(tmp_path) == {"active-and-old", "finalized", "fresh-newer", "fresh-older"}, (
        f"after the finalize of a run with TTL 1 h and a limit of 3 run directories: {sorted(_run_dirs(tmp_path))}. "
        "Expected the expired one and the oldest surplus one gone: "
        "['active-and-old', 'finalized', 'fresh-newer', 'fresh-older']"
    )


def test_each_rule_is_off_at_zero_and_works_alone(tmp_path) -> None:
    """The two rules are independent, and 0 switches a rule off - an operator's choice, no longer the default."""

    manager = _manager(tmp_path, {})
    for hours, name in ((30, "a-oldest"), (20, "b"), (10, "c-newest")):
        _run_dir(tmp_path, name, age_s=hours * 3600)

    manager.cleanup_old_runs(ttl_hours=0, max_runs=0)
    assert _run_dirs(tmp_path) == {"a-oldest", "b", "c-newest"}  # both off: nothing is removed
    manager.cleanup_old_runs(ttl_hours=25, max_runs=0)
    assert _run_dirs(tmp_path) == {"b", "c-newest"}  # the TTL alone
    manager.cleanup_old_runs(ttl_hours=0, max_runs=1)
    assert _run_dirs(tmp_path) == {"c-newest"}  # the limit alone keeps the newest


def _dropped(reason: str) -> float:
    return REGISTRY.get_sample_value(_DROPPED, {"reason": reason}) or 0.0


@pytest.mark.asyncio
async def test_an_event_the_artifact_writer_could_not_take_is_counted(tmp_path, caplog) -> None:
    """The writer's queue is full (its consumer is not running here): three more events are lost. Each is counted
    where an operator can read it - the `/metrics` counter - and the loss is logged; before, nothing said so."""

    runs: dict[str, RunRecord] = {}
    manager = _manager(tmp_path, runs)
    run = _registered(runs, "r-drops", "running", _run_dir(tmp_path, "r-drops", age_s=0))
    run._artifact_events_queue = asyncio.Queue(maxsize=2)
    before = _dropped("queue_full")

    with caplog.at_level(logging.WARNING, logger=_LOG.name):
        for seq in range(5):
            manager.enqueue_event_artifact(run.run_id, {"type": "tx.updated", "seq": seq})

    # Control: two were taken, so three were lost.
    assert run._artifact_events_queue.qsize() == 2
    counted = _dropped("queue_full") - before
    logged = [r.getMessage() for r in caplog.records if r.name == _LOG.name and r.levelno >= logging.WARNING]
    assert counted == 3 and logged, (
        f"3 events did not fit the artifact writer's queue; counted by {_DROPPED}{{reason=\"queue_full\"}}: "
        f"{counted:g}; warnings logged: {logged}. Expected 3 counted and the loss logged"
    )
    assert run._artifact_events_dropped == 3 and len(logged) == 1, (run._artifact_events_dropped, logged)  # once, not per event


@pytest.mark.asyncio
async def test_a_batch_the_writer_could_not_append_is_counted(tmp_path) -> None:
    """The other way an event is lost: the append itself fails (here `events.ndjson` is a directory). The real
    writer loop takes the two queued events as one batch, fails to append it, and counts both."""

    runs: dict[str, RunRecord] = {}
    manager = _manager(tmp_path, runs)
    artifacts = _run_dir(tmp_path, "r-io", age_s=0)
    run = _registered(runs, "r-io", "running", artifacts)
    (artifacts / "events.ndjson").unlink()
    (artifacts / "events.ndjson").mkdir()
    before = _dropped("write_failed")

    queue: asyncio.Queue = asyncio.Queue()
    for item in ('{"seq":0}\n', '{"seq":1}\n', None):
        queue.put_nowait(item)
    await manager._events_writer_loop(run_id=run.run_id, path=artifacts / "events.ndjson", queue=queue)

    assert (_dropped("write_failed") - before, run._artifact_events_dropped) == (2, 2)

