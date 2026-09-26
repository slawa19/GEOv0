"""Guard (programme 020, stage 2): production code does not import the experimental detectors.

`scripts/p020_experimental_detectors.py` holds the stage-2 candidate detectors, measured OUTSIDE the
production path (020 stage 3 superseded by 023; they stay dated evidence). This guard checks two things and nothing else:

* STATICALLY - no module under `app/` names the experimental module in an `import` / `from ... import`
  (AST walk, so a comment or a string does not count). Anti-vacuum: the same walker, pointed at the
  experiment's own contract test, DOES find the import - it can see the form it looks for;
* AT RUNTIME - a fresh interpreter that imports `app.main` (the whole application graph) has not loaded it.

What it does not see: a dynamic import by a computed string (`importlib.import_module(name)`), or a copy of
the code pasted into `app/`. Stage 3 moves the winner into `app/core/clearing/service.py` on purpose and
deletes this guard together with the experimental module.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = "p020_experimental_detectors"


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_no_app_module_imports_the_experimental_detectors() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "app").rglob("*.py"))
        if any(_MODULE in name for name in _imports_of(path))
    ]
    assert offenders == [], f"production modules import the stage-2 experiment: {offenders}"


def test_the_walker_sees_the_import_it_looks_for() -> None:
    probe = REPO_ROOT / "tests" / "integration" / "test_p020_experimental_detectors_postgres.py"
    assert any(_MODULE in name for name in _imports_of(probe)), "the walker cannot see a real import"


def test_the_application_graph_does_not_load_the_experiment() -> None:
    code = (
        "import sys, app.main; "
        f"print(sorted(m for m in sys.modules if {_MODULE!r} in m))"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip().splitlines()[-1] == "[]", completed.stdout
