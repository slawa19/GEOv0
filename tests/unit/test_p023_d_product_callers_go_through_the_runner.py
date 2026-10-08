"""Guard (programme 023, slice (d)): every product caller of clearing EXECUTION goes through the common runner.

Replaces the slice (c) "not wired" guard (`test_p023_c_runner_is_not_wired.py`, whose first check it inverts) and
narrows the slice (b) one (spec (d), decision R4, 2026-09-28): the cutover forbids EXTERNAL PRODUCT CALLERS from
bypassing the runner; it does not forbid the runner's internal call of the shared executor.

What it checks, statically (AST - a comment or a docstring does not count):

* the three product callers each name their runner entry - `POST /clearing/auto` (`app/api/v1/clearing.py::
  auto_clear`) `run_awaited_clearing`; the Interact action (`app/api/v1/simulator.py::action_clearing_real`) and
  the tick (`app/core/simulator/tick.py::RealTick._run_clearing`; until 021 `T2109` the driver
  `real_clearing_engine.py::RealClearingEngine.tick_real_mode_clearing`) `run_clearing_pass` - and none of them calls an executor or a detector itself (`execute_clearing_with_amount`,
  `execute_clearing`, `execute_occurrence`, `find_cycles`, `auto_clear`);
* under `app/`, only `app/core/clearing/service.py` calls the shared executor `execute_clearing_with_amount`, only
  `service.py` and `runner.py` call `execute_occurrence`, and nothing calls `auto_clear` or the compatibility
  wrapper `execute_clearing` - both gone from `ClearingService` (R4: safe delete; the wrapper: 024 `T2417`);
* `find_cycles` (the retired detectors) is called by nothing under `app/` outside `service.py` itself: since 035 A1
  (owner decision П1-(а), 2026-10-08) `GET /clearing/cycles` answers with the flow plan through the runner, and
  the admin copy of the route left in 032 S5. Its remaining callers are the seed tool and tests, until they move.

Anti-vacuum: the walker sees each form it looks for in a synthetic snippet.

What it does not see: a call through `getattr` with a computed string, or a copy of an executor pasted elsewhere;
`scripts/` (the seed tool's executing call, `scripts/seed_recipe.py` - `execute_occurrence` since 024 `T2417` - is a
recorded R4 keep, not a product caller).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.p023_support import require_target

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "app"
_EXECUTORS = frozenset({"execute_clearing_with_amount", "execute_clearing", "execute_occurrence", "find_cycles", "auto_clear"})
_CALLERS = {
    ("app/api/v1/clearing.py", "auto_clear"): "run_awaited_clearing",
    ("app/api/v1/simulator.py", "action_clearing_real"): "run_clearing_pass",
    ("app/core/simulator/tick.py", "_run_clearing"): "run_clearing_pass",
}


def _called(tree: ast.AST) -> set[str]:
    """Names CALLED (`x.name(...)` or `name(...)`), not merely mentioned."""

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                found.add(func.attr)
            elif isinstance(func, ast.Name):
                found.add(func.id)
    return found


def _named(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names)
    return found


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree: ast.AST, name: str) -> ast.AST:
    matches = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1, f"{name}: {len(matches)} definitions"
    return matches[0]


def test_each_product_caller_goes_through_its_runner_entry_and_calls_no_executor_itself() -> None:
    problems = []
    for (relative, function), entry in _CALLERS.items():
        body = _function(_parse(REPO_ROOT / relative), function)
        called = _called(body)
        if entry not in _named(body):
            problems.append(f"{relative}::{function} does not use {entry}")
        if bypass := sorted(called & _EXECUTORS):
            problems.append(f"{relative}::{function} calls {bypass} itself")
    require_target(problems == [], "; ".join(problems))


def test_only_the_service_and_the_runner_reach_the_executors_and_auto_clear_is_gone() -> None:
    from app.core.clearing.service import ClearingService

    offenders: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        called = _called(_parse(path))
        allowed = set()
        if relative == "app/core/clearing/service.py":
            allowed = {"execute_clearing_with_amount", "execute_occurrence", "find_cycles"}
        elif relative == "app/core/clearing/runner.py":
            allowed = {"execute_occurrence"}
        if bad := sorted((called & _EXECUTORS) - allowed):
            offenders[relative] = bad
    gone = ("auto_clear", "execute_clearing")
    require_target(
        offenders == {} and not any(hasattr(ClearingService, name) for name in gone),
        f"executor calls outside their owners: {offenders}; present on ClearingService: "
        f"{[name for name in gone if hasattr(ClearingService, name)]}",
    )


def test_the_walker_sees_the_forms_it_looks_for() -> None:
    assert _called(ast.parse("service.execute_clearing_with_amount(c)")) == {"execute_clearing_with_amount"}
    assert "find_cycles" in _called(ast.parse("async def f(s):\n    await s.find_cycles('X')"))
    assert _called(ast.parse("run_clearing_pass(f, 'X')")) == {"run_clearing_pass"}
    # A mention is not a call; a string or a comment is neither.
    assert _called(ast.parse("x = ClearingService.execute_clearing  # auto_clear()")) == set()
    assert "run_awaited_clearing" in _named(ast.parse("from app.core.clearing.runner import run_awaited_clearing"))
    assert _named(ast.parse('"run_clearing_pass"')) == set()
