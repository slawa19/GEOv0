"""034 S1c: the two scripts that only need to know WHERE the simulator's state is do not start its runtime.

WHAT WAS WRONG (on `a08f85a4`, found by the review of that commit). To share one path rule with the application the
scripts imported `app.core.simulator.runtime_utils`. Importing anything from that package runs its `__init__`, which
imports the runtime; the runtime module builds its singleton; and the singleton's constructor applies the start-up
cleanup of run directories (`cleanup_old_runs`, when `SIMULATOR_ARTIFACTS_TTL_HOURS` > 0). So the inspecting script,
and `cleanup_simulator_runs.py --dry-run` - even `--help` - deleted old run directories BEFORE their arguments were
read. On `main` neither script imported the simulator package.

WHY A FRESH PROCESS. In this test process `tests/conftest.py` has long imported the application and the package is in
`sys.modules`, so an import here shows nothing: the first version of the script tests was green on the defect. Every
case below is a `subprocess` of the same interpreter.

SAFETY. The subprocess's state directory is this test's temporary directory and nothing else. Before any script is
run, a guard subprocess - which imports only `app.config` - shows that the settings it loads point there, and (once
the path rule lives in `app.config`) that the rule resolves there too. A tree in which either is not so fails at the
guard and runs no script; the checkout's own `.local-run/simulator` is not touched on any outcome.

WHAT THESE TESTS DO NOT SEE: any other script, and any module that reaches the runtime by a route other than
`app.core.simulator` (the last test names the two modules it checks for).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_OLD_DAYS = 400


def _state_with_an_old_run(tmp_path: Path) -> tuple[Path, Path]:
    """A state directory holding one complete run, last modified `_OLD_DAYS` ago: older than a TTL of 1 h and than
    any retention the tests pass. Returns (state dir, run dir)."""

    state = tmp_path / "state"
    artifacts = state / "runs" / "run_old" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "events.ndjson").write_text('{"type":"tx.updated","equivalent":"UAH"}\n', encoding="utf-8")
    (artifacts / "status.json").write_text('{"run_id":"run_old","scenario_id":"s","mode":"real"}', encoding="utf-8")
    (artifacts / "summary.json").write_text("{}", encoding="utf-8")
    long_ago = time.time() - _OLD_DAYS * 24 * 3600
    for path in (artifacts / "events.ndjson", artifacts / "status.json", artifacts / "summary.json", artifacts, artifacts.parent):
        os.utime(path, (long_ago, long_ago))
    return state, artifacts.parent


def _env(state: Path) -> dict[str, str]:
    """The environment of a fresh application process whose start-up cleanup is ON (TTL 1 h) and whose simulator
    state is `state`. The database URL only has to be well-formed: nothing here connects."""

    env = dict(os.environ)
    env.update({
        "ENV": "test",
        "ENVIRONMENT": "test",
        "DATABASE_URL": "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_p034_never_connected",
        "SIMULATOR_STATE_DIR": str(state),
        "SIMULATOR_ARTIFACTS_TTL_HOURS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _python(args: list[str], *, state: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=str(_ROOT), env=_env(state), capture_output=True, text=True,
                          timeout=120)


_GUARD = """
from pathlib import Path
import app.config as config
print("SETTING=" + str(Path(config.settings.SIMULATOR_STATE_DIR).resolve()))
print("TTL=" + str(config.settings.SIMULATOR_ARTIFACTS_TTL_HOURS))
resolver = getattr(config, "simulator_state_dir", None)
print("RESOLVED=" + (str(resolver().resolve()) if resolver else "<no rule in app.config>"))
"""


def _guard(state: Path) -> None:
    """No script is run unless a fresh process, importing `app.config` only, is shown to look at `state`."""

    done = _python(["-c", _GUARD], state=state)
    assert done.returncode == 0, done.stderr
    lines = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
    assert lines["SETTING"] == str(state.resolve()) and lines["TTL"] == "1", lines
    assert lines["RESOLVED"] in (str(state.resolve()), "<no rule in app.config>"), lines


@pytest.mark.parametrize("script_args", [
    ["scripts/cleanup_simulator_runs.py", "--dry-run", "--no-db", "--retention-days", "30"],
    ["scripts/cleanup_simulator_runs.py", "--help"],
    ["scripts/check_latest_simulator_artifacts.py"],
], ids=["cleanup --dry-run", "cleanup --help", "check latest artifacts"])
def test_a_script_that_only_reads_or_plans_deletes_no_run_directory(tmp_path, script_args) -> None:
    """A dry run, a `--help` and a read-only inspection, each in a fresh process whose start-up cleanup would
    remove the run (TTL 1 h, the run is 400 days old): the run directory is still there afterwards."""

    state, old_run = _state_with_an_old_run(tmp_path)
    _guard(state)

    done = _python(script_args, state=state)

    assert done.returncode == 0, f"{script_args} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    left = sorted(p.name for p in (state / "runs").iterdir())
    assert old_run.is_dir() and (old_run / "artifacts" / "events.ndjson").is_file(), (
        f"`python {' '.join(script_args)}` in a fresh process (SIMULATOR_ARTIFACTS_TTL_HOURS=1) removed the run "
        f"directory it was only asked to read or plan about; left under runs/: {left}. Output:\n{done.stdout}"
    )


_IMPORTED = """
import sys
sys.argv = ["p034"]
import scripts.cleanup_simulator_runs
import scripts.check_latest_simulator_artifacts
for name in ("app.core.simulator", "app.core.simulator.runtime_impl"):
    print(name + "=" + str(name in sys.modules))
"""


def test_importing_the_scripts_does_not_import_the_simulator_runtime(tmp_path) -> None:
    """The mechanism, stated directly: after both scripts are imported in a fresh process, neither the simulator
    package (whose `__init__` builds the runtime) nor the runtime module is loaded."""

    state, old_run = _state_with_an_old_run(tmp_path)
    _guard(state)

    done = _python(["-c", _IMPORTED], state=state)

    assert done.returncode == 0, done.stderr
    loaded = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
    assert loaded == {"app.core.simulator": "False", "app.core.simulator.runtime_impl": "False"}, (
        f"modules loaded by importing the two scripts in a fresh process: {loaded}. Expected neither: the simulator "
        f"package builds its runtime on import, and the runtime's constructor applies the start-up cleanup"
    )
    assert old_run.is_dir()
