"""Guard (programme 023, slice (c), narrowed in slice (d)): the periodic loop is off by default and a refusal is loud.

HISTORY: until slice (d) this module also asserted that no production entrypoint used the runner; the atomic switch
inverted that check into `test_p023_d_product_callers_go_through_the_runner.py` (recorded below and in the spec).

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

from tests.p023_support import require_target

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


# REMOVED 2026-09-28, slice (d): `test_no_production_module_but_the_periodic_loop_reaches_the_runner` asserted that
# nothing but the periodic loop reached the runner - the (c) state the atomic switch ends. Its assertion is INVERTED,
# not dropped, in `tests/unit/test_p023_d_product_callers_go_through_the_runner.py` (each product caller must go
# through its runner entry and call no executor itself). The default-off checks below stay: decision R1 keeps the
# loop off by default.


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


# ------------------------------------------------------------------------------ fix-delta (review P2-4), red-first


def _result(runner, reason):
    return runner.ClearingPassResult(
        equivalent="PQH",
        status="interrupted",
        reason=reason,
        committed=(),
        remaining_cycles=None,
        remaining_v_edge_atoms=None,
        plans=0,
        distributed_exclusive=False,
    )


async def _health_after(monkeypatch, results) -> str:
    import app.core.clearing.runner as runner
    import app.main as main
    from app.utils.background_jobs import background_health_status

    async def periodic(_factory, _redis):
        return results

    monkeypatch.setattr(runner, "run_periodic_clearing_pass", periodic)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, background_jobs={}))
    await main._run_periodic_clearing_once(app)
    return background_health_status(app)


@pytest.mark.asyncio
async def test_an_equivalent_pass_error_degrades_health(monkeypatch) -> None:
    import app.core.clearing.runner as runner

    status = await _health_after(monkeypatch, {"PQH": _result(runner, runner.InterruptReason.ERROR)})
    require_target(status == "degraded", f"a pass that stopped on an error left health {status!r}")


@pytest.mark.parametrize("reason", ["LEASE_LOST", "BUDGET_EXHAUSTED", "REPLAN_LIMIT", "OPERATIONAL_LIMIT"])
@pytest.mark.asyncio
async def test_counter_check_ordinary_interruptions_keep_health_ok(monkeypatch, reason) -> None:
    import app.core.clearing.runner as runner

    status = await _health_after(monkeypatch, {"PQH": _result(runner, getattr(runner.InterruptReason, reason))})
    assert status == "ok", (reason, status)
