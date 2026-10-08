"""034 S1c: the simulator's state directory is a setting, the test tier uses its own, and the scripts follow it.

WHAT WAS WRONG (on `dd019a31`). The simulator's runtime state - `runs/<run_id>/artifacts`, uploaded `scenarios/` -
was hard-wired to the checkout's `.local-run/simulator`. A test process wrote its runs into that directory, which is
the developer's (a full tier left dozens of run directories there), ignoring the artifact root the canonical runner
gives every task (`GEO_TEST_ARTIFACT_ROOT`; AGENTS.md §7, §12). And two scripts carried the same hard-wired path
of their own.

WHAT IS HELD HERE.
1. ISOLATION (red on `dd019a31`): under the test environment the runtime's state directory is
   `<GEO_TEST_ARTIFACT_ROOT>/simulator`, and a run's artifacts really land there, not in the checkout's directory.
2. ANTI-VACUUM: the rule is a setting and not a test-only switch - empty, the path is the old one; relative, it is
   taken from the repository root whatever the working directory; absolute, it is taken as given.
3. THE SCRIPTS (red on `dd019a31`): `cleanup_simulator_runs.py` and `check_latest_simulator_artifacts.py` work on
   the configured directory, through the application's resolver.

SAFETY OF THIS MODULE ITSELF. The cleanup script deletes directories. Every call of it here is a dry run, except one
that runs only after an assertion has shown that the directory it resolved lies inside the test's temporary
directory - so a tree in which the script ignores the setting fails that assertion and deletes nothing.

WHAT THESE TESTS DO NOT SEE: a process started without `tests/conftest.py` (a dev server, a script run by hand) -
there the directory is whatever `SIMULATOR_STATE_DIR` says, the checkout's by default; and whether a directory given
in the setting is the simulator's own (the setting cannot check that; the documentation requires it).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from app.config import settings
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime import runtime
from app.core.simulator.runtime_utils import local_state_dir, repo_root

_CHECKOUTS = (repo_root() / ".local-run" / "simulator").resolve()


def _expected_test_state_dir() -> Path:
    """What `tests/conftest.py` promises: the task's artifact root (the canonical runner's, or the direct-pytest
    default), relative roots taken from the repository root, plus `simulator`."""

    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/direct-pytest/artifacts")
    return ((root if root.is_absolute() else repo_root() / root) / "simulator").resolve()


# ── 1: isolation ────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_test_process_keeps_its_simulator_state_under_the_tasks_artifact_root() -> None:
    expected = _expected_test_state_dir()
    state = local_state_dir().resolve()
    assert state == expected and state != _CHECKOUTS and not state.is_relative_to(_CHECKOUTS), (
        f"the simulator state directory of this test process is {state}. Expected {expected} (under the task's "
        f"artifact root), and never the checkout's own {_CHECKOUTS}"
    )
    # The runtime the tests drive is wired to that very directory - both of its stores.
    assert runtime._artifacts._local_state_dir().resolve() == expected
    assert Path(runtime._scenario_registry._local_state_dir).resolve() == expected


def test_a_runs_artifacts_are_written_under_the_tasks_artifact_root() -> None:
    """A real write by the runtime's own artifacts manager: the directory it creates for a run."""

    run = RunRecord(run_id="run_p034_s1c_isolation", scenario_id="s", mode="fixtures", state="running")
    runtime._artifacts.init_run_artifacts(run)
    try:
        assert run.artifacts_dir is not None and (run.artifacts_dir / "events.ndjson").is_file()
        written = run.artifacts_dir.resolve()
        assert written.is_relative_to(_expected_test_state_dir() / "runs") and not written.is_relative_to(_CHECKOUTS), (
            f"a run's artifacts were written to {written}. Expected under {_expected_test_state_dir() / 'runs'}, "
            f"not under the checkout's {_CHECKOUTS}"
        )
    finally:
        if run.artifacts_dir is not None and run.artifacts_dir.resolve().is_relative_to(_expected_test_state_dir()):
            import shutil

            shutil.rmtree(run.artifacts_dir.parent, ignore_errors=True)


# ── 2: anti-vacuum - it is a setting ────────────────────────────────────────────────────────────────────────────


def test_without_the_setting_the_state_directory_is_the_checkouts_as_before(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SIMULATOR_STATE_DIR", "")
    assert local_state_dir() == repo_root() / ".local-run" / "simulator"
    monkeypatch.setattr(settings, "SIMULATOR_STATE_DIR", "   ")
    assert local_state_dir() == repo_root() / ".local-run" / "simulator"


def test_a_relative_setting_is_taken_from_the_repository_root_and_an_absolute_one_as_given(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # the working directory must not matter
    monkeypatch.setattr(settings, "SIMULATOR_STATE_DIR", ".local-run/somewhere/simulator")
    assert local_state_dir() == repo_root() / ".local-run" / "somewhere" / "simulator"
    monkeypatch.setattr(settings, "SIMULATOR_STATE_DIR", str(tmp_path / "state"))
    assert local_state_dir() == tmp_path / "state"


# ── 3: the scripts ──────────────────────────────────────────────────────────────────────────────────────────────


def _old_run_dir(state_dir: Path, run_id: str) -> Path:
    artifacts = state_dir / "runs" / run_id / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "events.ndjson").write_text("{}\n", encoding="utf-8")
    long_ago = time.time() - 400 * 24 * 3600
    os.utime(artifacts.parent, (long_ago, long_ago))
    return artifacts.parent


def _run_cleanup(monkeypatch, capsys, *args: str) -> str:
    import scripts.cleanup_simulator_runs as cleanup

    monkeypatch.setattr(sys, "argv", ["cleanup_simulator_runs.py", "--no-db", *args])
    assert asyncio.run(cleanup.main()) == 0
    return capsys.readouterr().out


def test_the_cleanup_script_works_on_the_configured_state_directory(monkeypatch, tmp_path, capsys) -> None:
    import scripts.cleanup_simulator_runs as cleanup

    state = tmp_path / "state"
    old = _old_run_dir(state, "run_old")
    monkeypatch.setattr(settings, "SIMULATOR_STATE_DIR", str(state))

    # A DRY RUN first: it names what it would delete, and deletes nothing wherever it looked.
    out = _run_cleanup(monkeypatch, capsys, "--dry-run", "--retention-days", "30")
    assert old.is_dir()
    resolved = cleanup._local_simulator_runs_dir().resolve()
    assert resolved == (state / "runs").resolve() and "Artifacts: would delete run dirs: 1" in out and str(old) in out, (
        f"with SIMULATOR_STATE_DIR={state} the cleanup script looked at {resolved} and reported:\n{out}\n"
        f"Expected it to look at {state / 'runs'} and to name {old}"
    )

    # Only now a real run of it - on a directory just shown to be inside this test's temporary directory.
    assert resolved.is_relative_to(tmp_path.resolve())
    _run_cleanup(monkeypatch, capsys, "--retention-days", "30")
    assert not old.exists()


def test_the_latest_artifacts_script_looks_in_the_configured_state_directory(monkeypatch, tmp_path) -> None:
    import scripts.check_latest_simulator_artifacts as check

    state = tmp_path / "state"
    _old_run_dir(state, "run_a")
    newest = state / "runs" / "run_b"
    newest.mkdir()
    monkeypatch.setattr(settings, "SIMULATOR_STATE_DIR", str(state))

    assert check._latest_run_dir() == newest, (
        f"with SIMULATOR_STATE_DIR={state} the script picked {check._latest_run_dir()} as the latest run"
    )
