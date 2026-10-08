"""034 S1c, F-034-8 (the retention half): run directories have a TTL and a limit, applied after a write.

WHAT WAS WRONG (on `0f248b9c`). There was no limit of run directories at all; the one cleanup (a TTL) ran only when
the runtime was constructed, never after an artifact was written; it logged the absolute path of what it could not
remove (AGENTS.md §12); and it read a run's age from the modification time of `runs/<id>`, which nothing moves
while the run keeps appending to `runs/<id>/artifacts/events.ndjson`.

The first version of this fix (S1b, `3594976b`) was withdrawn after an adversarial pass, and each thing that pass
found is held here by a test of its own:

1. the AGE of a run is the newest write inside its directory - shown on a real append by the real writer loop;
2. a run of this process that can still write, or be resumed into writing, is never removed: every state but
   `stopped`;
3. a directory ANOTHER process wrote to recently is never removed - the evidence is the file system, not this
   process's registry; and the test tier itself keeps the simulator's state under the task's artifact root, not in
   the checkout's `.local-run/simulator`;
4. a directory that cannot be removed is left whole, and is named in the log by its id, not by its path, with the
   traceback once.

HOW TIME IS MADE: `_age` sets the modification times of a whole run directory - every entry in it - into the past
with `os.utime`. It never ages the directory alone; whatever is written afterwards is a real write at the real time.

WHAT THESE TESTS DO NOT SEE: a run of another process that has written nothing for longer than the grace (it is not
protected - there is no lock between processes); a directory held open on Windows (the refusal is simulated at
`os.rename`, the one call the removal starts with); the size of a single `events.ndjson`.
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

import app.core.simulator.artifacts as artifacts_module
import app.core.simulator.storage as simulator_storage
from app.config import settings
from app.core.simulator.artifacts import RECENT_WRITE_GRACE_SEC, ArtifactsManager
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime import runtime
from app.core.simulator.runtime_utils import local_state_dir, repo_root

_LOG = logging.getLogger("tests.p034.s1c.artifacts")
_HOUR = 3600.0


def _manager(state_dir: Path, runs: dict[str, RunRecord] | None = None) -> ArtifactsManager:
    return ArtifactsManager(
        lock=threading.RLock(), runs={} if runs is None else runs, local_state_dir=lambda: state_dir,
        utc_now=lambda: datetime.now(timezone.utc), db_enabled=lambda: False, logger=_LOG,
    )


def _age(run_dir: Path, hours: float) -> None:
    """Move the modification time of EVERYTHING in a run directory `hours` into the past."""

    stamp = time.time() - hours * _HOUR
    for path in (run_dir, *run_dir.rglob("*")):
        os.utime(path, (stamp, stamp))


def _run_dir(state_dir: Path, run_id: str, *, hours: float) -> Path:
    """A run directory as `init_run_artifacts` leaves it, last written `hours` ago. Returns `artifacts/`."""

    artifacts = state_dir / "runs" / run_id / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "events.ndjson").write_text("{}\n", encoding="utf-8")
    (artifacts / "status.json").write_text("{}", encoding="utf-8")
    _age(artifacts.parent, hours)
    return artifacts


def _registered(runs: dict[str, RunRecord], run_id: str, state: str, artifacts: Path) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="s", mode="fixtures", state=state)
    run.artifacts_dir = artifacts
    runs[run_id] = run
    return run


def _run_dirs(state_dir: Path) -> list[str]:
    return sorted(p.name for p in (state_dir / "runs").iterdir() if p.is_dir())


# ── the two rules ─────────────────────────────────────────────────────────────────────────────────────────────


def test_each_rule_works_alone_and_is_off_at_zero(tmp_path) -> None:
    manager = _manager(tmp_path)
    for hours, name in ((30, "a-oldest"), (20, "b"), (10, "c-newest")):
        _run_dir(tmp_path, name, hours=hours)

    manager.cleanup_old_runs(ttl_hours=0, max_runs=0)
    assert _run_dirs(tmp_path) == ["a-oldest", "b", "c-newest"]  # both off: nothing is removed
    manager.cleanup_old_runs(ttl_hours=25, max_runs=0)
    assert _run_dirs(tmp_path) == ["b", "c-newest"]  # the TTL alone
    manager.cleanup_old_runs(ttl_hours=0, max_runs=1)
    assert _run_dirs(tmp_path) == ["c-newest"]  # the limit alone keeps the most recently written


def test_both_rules_are_off_by_default() -> None:
    """Switching either on removes directories that are already there. That is an operator's decision, not a
    default this slice takes: the mechanism is delivered, the defaults stay as they were."""

    from app.config import Settings

    fields = Settings.model_fields
    assert (fields["SIMULATOR_ARTIFACTS_TTL_HOURS"].default, fields["SIMULATOR_ARTIFACTS_MAX_RUNS"].default) == (0, 0)


@pytest.mark.asyncio
async def test_the_retention_is_applied_right_after_a_run_is_finalized(tmp_path, monkeypatch) -> None:
    """`finalize_run_artifacts` is the write; with a TTL of 2 h and a limit of 2 the expired directory and the
    surplus one are gone when it returns, and the run just finalized - the newest write there is - stays.
    COUNTER-CHECK: what is not a run directory is not touched."""

    monkeypatch.setattr(simulator_storage, "sync_artifacts", AsyncMock())
    monkeypatch.setattr(settings, "SIMULATOR_ARTIFACTS_TTL_HOURS", 2)
    monkeypatch.setattr(settings, "SIMULATOR_ARTIFACTS_MAX_RUNS", 2)
    runs: dict[str, RunRecord] = {}
    manager = _manager(tmp_path, runs)

    _run_dir(tmp_path, "expired", hours=30)
    _run_dir(tmp_path, "surplus", hours=1.9)
    _run_dir(tmp_path, "kept", hours=1.5)
    finalized = _registered(runs, "finalized", "stopped", _run_dir(tmp_path, "finalized", hours=1.8))
    (tmp_path / "runs" / "README.txt").write_text("not a run", encoding="utf-8")
    scenario = tmp_path / "scenarios" / "uploaded" / "scenario.json"
    scenario.parent.mkdir(parents=True)
    scenario.write_text("{}", encoding="utf-8")
    _age(scenario.parent, 30)

    await manager.finalize_run_artifacts(run_id=finalized.run_id, status_payload={"state": "stopped"})

    assert (finalized.artifacts_dir / "summary.json").is_file() and (finalized.artifacts_dir / "bundle.zip").is_file()
    assert (tmp_path / "runs" / "README.txt").is_file() and scenario.is_file(), "something that is not a run directory was pruned"
    assert _run_dirs(tmp_path) == ["finalized", "kept"], (
        f"after the finalize of a run with TTL 2 h and a limit of 2 run directories: {_run_dirs(tmp_path)}. "
        "Expected the expired one and the least recently written surplus one gone: ['finalized', 'kept']"
    )


# ── 1: the age of a run is its newest write ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_that_is_still_being_written_is_not_expired_by_the_age_of_its_directory(tmp_path) -> None:
    """A run created 30 h ago whose writer has JUST appended to `events.ndjson` - the real writer loop, a real
    append. Nothing but that file's modification time moved: `runs/<id>` and `runs/<id>/artifacts` still say 30 h.
    Another process (a manager with an empty registry) applies a TTL of 25 h: the run stays. CONTROL: its
    neighbour, equally old and not written since, goes."""

    writing = _run_dir(tmp_path, "still-written", hours=30)
    _run_dir(tmp_path, "abandoned", hours=30)
    queue: asyncio.Queue = asyncio.Queue()
    for item in ('{"seq":1}\n', None):
        queue.put_nowait(item)
    await _manager(tmp_path)._events_writer_loop(run_id="still-written", path=writing / "events.ndjson", queue=queue)

    # Control: the append is real and it moved no directory's time.
    assert (writing / "events.ndjson").read_text(encoding="utf-8") == '{}\n{"seq":1}\n'
    assert max(writing.stat().st_mtime, writing.parent.stat().st_mtime) < time.time() - 29 * _HOUR

    _manager(tmp_path).cleanup_old_runs(ttl_hours=25)

    assert _run_dirs(tmp_path) == ["still-written"], (
        f"a run whose `events.ndjson` was appended to a moment ago, under a TTL of 25 h: {_run_dirs(tmp_path)} "
        "left. Expected ['still-written'] - its age is its newest write, not its directory's"
    )


# ── 2: a run of this process that may still write ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state", ["idle", "running", "paused", "stopping", "error"])
def test_a_run_that_can_still_write_or_be_resumed_is_never_removed(tmp_path, state) -> None:
    """Old beyond the TTL and beyond the limit, and in a state from which the run still writes or can be resumed
    into writing without its directory being re-created (`error` and `idle` resume straight to `running`,
    `run_lifecycle.py`, `resume`). COUNTER-CHECK in the same call: a `stopped` run of the same age goes."""

    runs: dict[str, RunRecord] = {}
    manager = _manager(tmp_path, runs)
    alive = _registered(runs, "alive", state, _run_dir(tmp_path, "alive", hours=30))
    _registered(runs, "stopped", "stopped", _run_dir(tmp_path, "stopped", hours=30))

    manager.cleanup_old_runs(ttl_hours=1, max_runs=1)

    assert _run_dirs(tmp_path) == ["alive"], f"a run in state {state!r}: {_run_dirs(tmp_path)} left"
    # ...and it can go on writing where it was: a real artifact write into the directory that was kept.
    manager.write_real_tick_artifact(alive, {"tick_index": 1})
    assert (alive.artifacts_dir / "last_tick.json").is_file()


# ── 3: other processes, and the test tier itself ────────────────────────────────────────────────────────────────


def test_a_directory_written_recently_by_another_process_is_never_removed(tmp_path, monkeypatch) -> None:
    """This process knows none of these runs (an empty registry) - they are another process's, as far as it can
    tell. TWO of them were written within the grace; a limit of 1 would have to remove the less recent of the two,
    and does not: both stay, over the limit, and only the old ones go. (With one recent directory the limit would
    keep it anyway, as the newest - the first version of this test was written that way and could not tell the
    grace from its absence; a mutation survived it.)"""

    grace_h = RECENT_WRITE_GRACE_SEC / _HOUR
    _run_dir(tmp_path, "written-a-moment-ago", hours=grace_h / 6)
    _run_dir(tmp_path, "written-within-the-grace", hours=grace_h / 2)
    _run_dir(tmp_path, "old-a", hours=grace_h * 3)
    _run_dir(tmp_path, "old-b", hours=grace_h * 2)

    _manager(tmp_path).cleanup_old_runs(ttl_hours=0, max_runs=1)

    assert _run_dirs(tmp_path) == ["written-a-moment-ago", "written-within-the-grace"], (
        f"two directories written within the grace ({RECENT_WRITE_GRACE_SEC} s) and a limit of 1: "
        f"{_run_dirs(tmp_path)} left. Expected both of them and neither of the old ones"
    )

    # The TTL is counted in whole hours and the grace is one hour, so a TTL alone can never reach inside the grace.
    # The rule is still the TTL's too, and is shown with a longer grace: written 2 h ago, TTL 1 h, grace 3 h.
    _run_dir(tmp_path, "two-hours-old", hours=2)
    monkeypatch.setattr(artifacts_module, "RECENT_WRITE_GRACE_SEC", 3 * _HOUR)
    _manager(tmp_path).cleanup_old_runs(ttl_hours=1, max_runs=0)
    assert "two-hours-old" in _run_dirs(tmp_path), _run_dirs(tmp_path)
    monkeypatch.setattr(artifacts_module, "RECENT_WRITE_GRACE_SEC", RECENT_WRITE_GRACE_SEC)
    _manager(tmp_path).cleanup_old_runs(ttl_hours=1, max_runs=0)
    assert "two-hours-old" not in _run_dirs(tmp_path), _run_dirs(tmp_path)  # control: past the grace it does expire


def test_the_test_tier_keeps_the_simulator_state_out_of_the_checkouts_directory() -> None:
    """A policy guard of the harness, not of the product (AGENTS.md §7, §12): a test process's simulator state -
    what its runs write, and what its retention would clean - is not the checkout's `.local-run/simulator`, and the
    runtime the tests drive really uses it. It checks the wiring of THIS process; it cannot see another one."""

    developers = (repo_root() / ".local-run" / "simulator").resolve()
    state = local_state_dir().resolve()
    assert state != developers and not state.is_relative_to(developers), state
    assert state.is_relative_to((repo_root() / ".local-run" / "test-runs").resolve()), state
    assert runtime._artifacts._local_state_dir().resolve() == state
    assert Path(runtime._scenario_registry._local_state_dir).resolve() == state


# ── 4: what cannot be removed ──────────────────────────────────────────────────────────────────────────────────


def test_a_directory_that_cannot_be_removed_is_left_whole_and_named_by_its_id_once(tmp_path, monkeypatch, caplog) -> None:
    """The directory is in use: the rename the removal starts with is refused, as Windows refuses it for a
    directory with an open file. Nothing of it is deleted; the log names the run directory, not its absolute
    path; and the traceback is logged the first time only, however often the retention runs."""

    artifacts = _run_dir(tmp_path, "in-use", hours=30)
    before = sorted(p.name for p in artifacts.iterdir())
    real_rename = os.rename

    def refuse(src, dst, *args, **kwargs):
        if Path(src).name == "in-use":
            raise PermissionError(13, "The process cannot access the file because it is being used", str(src))
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(artifacts_module.os, "rename", refuse)
    manager = _manager(tmp_path)
    with caplog.at_level(logging.DEBUG, logger=_LOG.name):
        for _ in range(3):
            manager.cleanup_old_runs(ttl_hours=1)

    assert sorted(p.name for p in artifacts.iterdir()) == before, "a directory that could not be removed was half-deleted"
    records = [r for r in caplog.records if r.name == _LOG.name and "cleanup_failed" in r.getMessage()]
    assert [(r.levelno, bool(r.exc_info)) for r in records] == [
        (logging.WARNING, True), (logging.DEBUG, False), (logging.DEBUG, False)], [(r.levelname, r.getMessage()) for r in records]
    assert all("in-use" in r.getMessage() and str(tmp_path) not in r.getMessage() for r in records), [r.getMessage() for r in records]


def _link_directory(link: Path, target: Path) -> None:
    """A directory link at `link` to `target`: a junction on Windows (no privilege needed), a symlink elsewhere."""

    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_link_out_of_the_runs_directory_is_not_followed_and_not_touched(tmp_path) -> None:
    """Under `runs/` sits a link to a directory outside it, old by every clock. It is not a run directory of this
    store: the retention neither deletes what it points to nor removes or renames the link itself. CONTROL: a real
    run directory of the same age, beside it, goes."""

    outside = tmp_path / "elsewhere" / "precious"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("not the simulator's", encoding="utf-8")
    _age(outside, 30)
    _run_dir(tmp_path, "a-real-run", hours=30)
    link = tmp_path / "runs" / "link-out"
    _link_directory(link, outside)
    assert link.is_dir() and link.resolve() == outside.resolve()  # control: the link is one, and it is followed by is_dir

    _manager(tmp_path).cleanup_old_runs(ttl_hours=1, max_runs=1)

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "not the simulator's"
    assert _run_dirs(tmp_path) == ["link-out"], _run_dirs(tmp_path)


def test_a_leftover_of_an_interrupted_removal_is_removed_by_the_next_call(tmp_path) -> None:
    artifacts = _run_dir(tmp_path, "gone", hours=30)
    os.rename(artifacts.parent, artifacts.parent.with_name("gone.deleting"))
    _run_dir(tmp_path, "fresh", hours=0)

    _manager(tmp_path).cleanup_old_runs(ttl_hours=0, max_runs=5)  # nothing is over any rule

    assert _run_dirs(tmp_path) == ["fresh"], _run_dirs(tmp_path)
