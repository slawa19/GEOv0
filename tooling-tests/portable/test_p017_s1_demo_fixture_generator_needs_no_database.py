"""Building the Simulator UI must not need a database.

Programme 017, stage 3, slice S1 (precondition of `T1704`). The `prebuild` of Simulator UI v2 runs
`scripts/sync_demo_fixtures.ps1 -Strict`, which runs
`admin-fixtures/tools/generate_simulator_demo_snapshots.py`. The generator only needs the pure
visualisation rules in `app/core/simulator/viz_rules.py` (stdlib only), but it used to reach them as
`from app.core.simulator import viz_rules`, which executes the package `__init__` and through it
`app.db.session` (engine built at import), `app.config` (settings built at import, `ENV` required)
and the SQLite driver of the default URL. Once `T1704` removes the default `DATABASE_URL`, that
import fails, `-Strict` turns it into exit 1 and the required `required-ui` job goes red.

What these tests check, and what they do not:

- they run the generator in a child interpreter whose environment has no `ENV`, `ENVIRONMENT`,
  `DATABASE_URL` or `TEST_DATABASE_URL`, and in which importing `app.db`, `app.config`,
  `sqlalchemy` or `aiosqlite` raises `ImportError` even though they are installed. So "passes" means
  "never tried to load them", not "loaded them successfully";
- the first test is the counter-check (AGENTS.md section 9): the same blocker applied to the
  package `app.core.simulator` must fail, otherwise a green run would only prove that the blocker
  is blind;
- they do not check that the generated fixtures are correct or unchanged - byte equality before and
  after this slice was measured separately, and the committed fixtures are validated by the UI
  build and its tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO_ROOT / "admin-fixtures" / "tools" / "generate_simulator_demo_snapshots.py"

_BLOCKED = ("app.db", "app.config", "sqlalchemy", "aiosqlite")

_BLOCKER = textwrap.dedent(
    f"""
    import sys

    _BLOCKED = {_BLOCKED!r}

    class _RefuseDatabase:
        def find_spec(self, name, path=None, target=None):
            for root in _BLOCKED:
                if name == root or name.startswith(root + "."):
                    raise ImportError(f"blocked for the UI build: {{name}}")
            return None

    sys.meta_path.insert(0, _RefuseDatabase())
    """
)


def _run(body: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"ENV", "ENVIRONMENT", "DATABASE_URL", "TEST_DATABASE_URL"}
    }
    return subprocess.run(
        [sys.executable, "-c", _BLOCKER + textwrap.dedent(body), *args],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_blocker_does_see_the_database_behind_the_simulator_package() -> None:
    result = _run(
        f"""
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        from app.core.simulator import viz_rules
        """
    )

    assert result.returncode != 0, "importing the simulator package no longer reaches the database; this check is blind"
    assert "blocked for the UI build: " in result.stderr, result.stderr


def test_the_generator_imports_without_a_database(tmp_path: Path) -> None:
    result = _run(
        f"""
        import runpy
        runpy.run_path({str(_GENERATOR)!r}, run_name="generator_under_test")
        leaked = sorted(m for m in sys.modules if m == "app" or m.startswith("app."))
        assert not leaked, leaked
        print("IMPORT-OK")
        """
    )

    assert result.returncode == 0, result.stderr
    assert "IMPORT-OK" in result.stdout


def test_the_generator_writes_fixtures_without_a_database(tmp_path: Path) -> None:
    result = _run(
        f"""
        import runpy
        sys.argv = [{str(_GENERATOR)!r}, "--eq", "UAH", "--out-root", {str(tmp_path)!r}]
        runpy.run_path({str(_GENERATOR)!r}, run_name="__main__")
        """
    )

    assert result.returncode == 0, result.stderr
    for relative in ("snapshot.json", "events/demo-tx.json", "events/demo-clearing.json"):
        assert (tmp_path / "UAH" / relative).stat().st_size > 0, relative
