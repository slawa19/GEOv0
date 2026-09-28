"""Guard (programme 023, slice (b)): no production entrypoint reaches the v2 occurrence executor yet.

Slice (b) is ADDITIVE (spec "Стадии", row (b); decision 7: activation only in slice (d)). The v2 surface -
`ClearingService.execute_occurrence` and the descriptor `ClearingOccurrence` - lives in
`app/core/clearing/service.py`; `/clearing/auto`, the periodic loop and both simulator callers keep the v1
path until the atomic switch of slice (d), which deletes this guard.

What it checks, statically (AST, so a comment or a docstring does not count): outside the defining module,
no module under `app/` names either symbol - as an attribute (`service.execute_occurrence(...)`), a bare
name, or an import. Anti-vacuum: the same walker, pointed at the slice's own integration tests, DOES find
both - it sees the forms it looks for.

What it does not see: a call through `getattr` with a computed string, or a copy of the code pasted into
another module. It says nothing about v1 behaviour; that is what the 019/020 selectors and
`test_p023_b_occurrence_execution_postgres.py`'s v1 control are for.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFINING_MODULE = REPO_ROOT / "app" / "core" / "clearing" / "service.py"
#: Slice (c), 2026-09-28: the common runner is the one module that reaches the executor; that it is itself reached
#: by no production entrypoint is `tests/unit/test_p023_c_runner_is_not_wired.py`.
_RUNNER_MODULE = REPO_ROOT / "app" / "core" / "clearing" / "runner.py"
_SYMBOLS = frozenset({"execute_occurrence", "ClearingOccurrence"})


def _names_used(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SYMBOLS:
            found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in _SYMBOLS:
            found.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names if alias.name in _SYMBOLS)
    return found


def test_no_production_module_reaches_the_v2_occurrence_executor() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(used)
        for path in sorted((REPO_ROOT / "app").rglob("*.py"))
        if path not in (_DEFINING_MODULE, _RUNNER_MODULE) and (used := _names_used(path))
    }
    assert offenders == {}, f"slice (b) is additive; these modules already reach the v2 executor: {offenders}"


def test_the_walker_sees_the_forms_it_looks_for() -> None:
    probe = REPO_ROOT / "tests" / "integration" / "test_p023_b_occurrence_execution_postgres.py"
    assert _names_used(probe) == set(_SYMBOLS), _names_used(probe)
    # The runner's exemption is not vacuous: it is the module that does reach the executor.
    assert _names_used(_RUNNER_MODULE) == set(_SYMBOLS), _names_used(_RUNNER_MODULE)
    synthetic = REPO_ROOT / "tests" / "unit" / "test_p023_b_occurrence_is_not_wired.py"
    # This module names the symbols only in strings and a frozenset literal: the walker must not count them.
    assert _names_used(synthetic) == set(), _names_used(synthetic)
