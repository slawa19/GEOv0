"""Guard (programme 023, slice (c)): no production entrypoint uses the common clearing runner yet.

Slice (c) is ADDITIVE and INACTIVE on production entrypoints (spec "Стадии", row (c); decision 7): the runner, the
periodic loop and the renewable lease exist and are tested directly, but `POST /clearing/auto` and both simulator
callers keep the v1 path, and the periodic loop is not started by default. Slice (d) switches all of them at once
and replaces this guard.

What it checks:

* statically (AST - a comment or a docstring does not count): outside `app/core/clearing/runner.py` and
  `app/main.py`, no module under `app/` imports the runner or names one of its entry points; inside `app/main.py`
  they are named only in the periodic loop's own function;
* behaviourally: with default settings `_start_configured_background_tasks` starts no `clearing` task, the
  setting's default is False, and a refusal of the isolation rule is recorded as a failed job (health `degraded`),
  not swallowed.

Anti-vacuum: the same walker finds the names in the runner's own tests, and with `CLEARING_PERIODIC_ENABLED` set
the loop IS started - the default-off check can see a start when there is one.

What it does not see: a call through `getattr` with a computed string, or a copy of the runner pasted elsewhere.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = REPO_ROOT / "app" / "core" / "clearing" / "runner.py"
_MAIN = REPO_ROOT / "app" / "main.py"
_MODULE = "app.core.clearing.runner"
_SYMBOLS = frozenset(
    {"run_clearing_pass", "run_awaited_clearing", "run_periodic_clearing_pass", "check_periodic_isolation"}
)


def _uses(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SYMBOLS:
            found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in _SYMBOLS:
            found.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            if node.module == _MODULE:
                found.add(_MODULE)
            found.update(alias.name for alias in node.names if alias.name in _SYMBOLS)
        elif isinstance(node, ast.Import):
            found.update(_MODULE for alias in node.names if alias.name == _MODULE)
    return found


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_production_module_but_the_periodic_loop_reaches_the_runner() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(used)
        for path in sorted((REPO_ROOT / "app").rglob("*.py"))
        if path not in (_RUNNER, _MAIN) and (used := _uses(_parse(path)))
    }
    assert offenders == {}, f"slice (c) is inactive; these modules already reach the runner: {offenders}"

    main = _parse(_MAIN)
    reaching = sorted(
        node.name
        for node in ast.walk(main)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _uses(node)
        and not any(
            isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not node and _uses(inner)
            for inner in ast.walk(node)
        )
    )
    assert reaching == ["_run_periodic_clearing_once"], reaching
    assert _uses(main) == {_MODULE, "run_periodic_clearing_pass"}, _uses(main)


def test_the_walker_sees_the_forms_it_looks_for() -> None:
    probe = REPO_ROOT / "tests" / "integration" / "test_p023_c_periodic_isolation_postgres.py"
    assert {"run_periodic_clearing_pass", "check_periodic_isolation"} <= _uses(_parse(probe))
    assert _uses(ast.parse("from app.core.clearing.runner import run_clearing_pass")) == {_MODULE, "run_clearing_pass"}
    assert _uses(ast.parse("import app.core.clearing.runner")) == {_MODULE}
    # Names in strings and comments are not uses.
    assert _uses(ast.parse('"run_clearing_pass app.core.clearing.runner"  # run_periodic_clearing_pass')) == set()


def _started_names(monkeypatch, *, periodic: bool | None) -> list[str]:
    import app.main as main
    from app.config import settings

    if periodic is not None:
        monkeypatch.setattr(settings, "CLEARING_PERIODIC_ENABLED", periodic)
    started: list[str] = []
    monkeypatch.setattr(
        main, "_start_supervised_background_task", lambda app, *, name, coroutine_factory: started.append(name)
    )
    main._start_configured_background_tasks(SimpleNamespace())
    return started


def test_the_periodic_loop_is_off_by_default_and_starts_only_when_configured(monkeypatch) -> None:
    from app.config import Settings

    assert Settings.model_fields["CLEARING_PERIODIC_ENABLED"].default is False
    assert "clearing" not in _started_names(monkeypatch, periodic=None)
    # Anti-vacuum: the same probe sees the start when the setting asks for it.
    assert "clearing" in _started_names(monkeypatch, periodic=True)


@pytest.mark.asyncio
async def test_an_isolation_refusal_is_recorded_as_a_failed_job_not_swallowed(monkeypatch) -> None:
    import app.core.clearing.runner as runner
    import app.main as main
    from app.utils.background_jobs import background_health_status

    async def refuse(_factory, _redis):
        raise runner.ClearingPeriodicRefused("simulator_real_runs_in_database")

    monkeypatch.setattr(runner, "run_periodic_clearing_pass", refuse)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
    await main._run_periodic_clearing_once(app)
    assert app.state.background_jobs["clearing"]["status"] == "failed"
    assert app.state.background_jobs["clearing"]["event"] == "refused_simulator_real_runs_in_database"
    assert background_health_status(app) == "degraded"
