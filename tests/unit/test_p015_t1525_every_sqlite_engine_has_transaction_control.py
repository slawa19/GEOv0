"""T1525: every SQLite engine the repository builds carries the transaction control.

WHY THIS MODULE EXISTS. `app/db/sqlite_transaction_control.py` makes a SQLite database transaction
start at SQLAlchemy's `begin`. An engine built without it silently falls back to the driver's legacy
mode, where a savepoint opened before the first write is its own transaction and a root rollback does
not undo it - measured on the application's money paths by
`tests/unit/test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py`. Nothing fails when an engine
is missing the control; the damage shows up only as a rollback that did not happen. So the rule is
held at the source: a new engine construction without the installer is red here whether or not any
test ever exercises it.

THE RULE. Under `app/`, `tests/`, `scripts/` and `migrations/`, every call to `create_async_engine`,
`create_engine`, `async_engine_from_config` or `engine_from_config` must have
`install_sqlite_transaction_control(...)` called in the same scope - the enclosing function, or the
module body for a module-level engine. A construction is exempt only when it provably cannot be
SQLite:

* a test module marked `pytestmark = pytest.mark.postgres` (the collection guard in
  `tests/conftest.py` refuses such tests on any other backend) and with no sqlite URL of its own;
* a file listed in `_POSTGRESQL_ONLY` together with the refusal that makes it PostgreSQL-only. The
  refusal is part of the entry: if it disappears from the file, the exemption is red.

WHAT IT DOES NOT SEE. It is a scope check, not data flow: it does not prove the installer is called
on the SAME engine object, and an engine built by a helper outside these directories is invisible to
it. `tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py` checks the live test
engine at runtime for that reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCANNED_DIRS = ("app", "tests", "scripts", "migrations")
_CONSTRUCTORS = {"create_async_engine", "create_engine", "async_engine_from_config", "engine_from_config"}
_INSTALLER = "install_sqlite_transaction_control"

#: Files whose engine can only ever be PostgreSQL, and the text of the refusal that makes it so.
_POSTGRESQL_ONLY = {
    "migrations/env.py": "_require_postgresql_migration_url(database_url)",
    "scripts/measure_clearing_min_amount_plan.py": 'required_backend="postgresql"',
}

#: SQLite engine constructions known on 2026-09-12. Non-vacuity: the scan must still find each of
#: them, and each must be guarded - a scan that finds nothing would otherwise pass for a clean tree.
_KNOWN_SQLITE_CONSTRUCTIONS = {
    "app/db/session.py",
    "tests/conftest.py",
    "tests/integration/test_audit_drift_delta_check_sse_integration.py",
    "tests/integration/test_post_tick_audit_drift_runner_integration.py",
    "tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py",
    "tests/integration/test_simulator_adaptive_clearing_integration.py",
    "tests/integration/test_simulator_clearing_no_deadlock.py",
    "tests/unit/test_p012_numeric_scale_rounding_is_invisible_on_sqlite.py",
    "tests/unit/test_sqlite_dev_schema_repair.py",
    "tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py",
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_postgres_marked(tree: ast.Module) -> bool:
    for statement in tree.body:
        targets: list[ast.AST] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            continue
        for node in ast.walk(statement.value):  # type: ignore[arg-type]
            if isinstance(node, ast.Attribute) and node.attr == "postgres":
                return True
    return False


def _has_sqlite_literal(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and "sqlite" in node.value.lower()
        and ":///" in node.value
        for node in ast.walk(tree)
    )


def _scopes(tree: ast.Module) -> list[ast.AST]:
    return [tree] + [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _own_nodes(scope: ast.AST):
    """Nodes of this scope, not descending into nested functions (they are scopes of their own)."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _unguarded_constructions(source: str, relative: str) -> tuple[list[str], int]:
    """(unguarded construction sites, number of constructions that had to be guarded)."""
    tree = ast.parse(source)
    if relative in _POSTGRESQL_ONLY:
        return [], 0
    if _is_postgres_marked(tree) and not _has_sqlite_literal(tree):
        return [], 0

    unguarded: list[str] = []
    guarded_needed = 0
    for scope in _scopes(tree):
        nodes = list(_own_nodes(scope))
        constructions = [
            n for n in nodes if isinstance(n, ast.Call) and _call_name(n) in _CONSTRUCTORS
        ]
        if not constructions:
            continue
        installed = any(isinstance(n, ast.Call) and _call_name(n) == _INSTALLER for n in nodes)
        for call in constructions:
            guarded_needed += 1
            if not installed:
                scope_name = getattr(scope, "name", "<module>")
                unguarded.append(f"{relative}:{call.lineno} in {scope_name}")
    return unguarded, guarded_needed


def _python_files() -> list[Path]:
    files: list[Path] = []
    for directory in _SCANNED_DIRS:
        for path in sorted((_ROOT / directory).rglob("*.py")):
            if "__pycache__" not in path.parts:
                files.append(path)
    return files


def test_every_sqlite_engine_construction_installs_the_transaction_control() -> None:
    findings: list[str] = []
    guarded_files: set[str] = set()
    constructions_seen = 0
    for path in _python_files():
        relative = path.relative_to(_ROOT).as_posix()
        if relative == "tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py":
            continue  # this module quotes constructions as DATA in its counter-tests
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        constructions_seen += sum(
            1 for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) in _CONSTRUCTORS
        )
        unguarded, needed = _unguarded_constructions(source, relative)
        findings.extend(unguarded)
        if needed and not unguarded:
            guarded_files.add(relative)

    missing_known = sorted(_KNOWN_SQLITE_CONSTRUCTIONS - guarded_files)
    assert not findings, (
        "SQLite-capable engine constructions without `install_sqlite_transaction_control` in the "
        f"same scope: {findings}. Without it a savepoint opened before the first write is its own "
        "transaction on SQLite and a root rollback does not undo it (T1525). Install it right after "
        "the construction; if the engine can only ever be PostgreSQL, make that a refusal in code "
        "and record it in `_POSTGRESQL_ONLY` with the refusal's text."
    )
    assert not missing_known, (
        f"non-vacuity: known SQLite engine constructions not found guarded by the scan: {missing_known}"
    )
    assert constructions_seen > len(_KNOWN_SQLITE_CONSTRUCTIONS), constructions_seen


def test_every_postgresql_only_exemption_still_carries_its_refusal() -> None:
    for relative, refusal in _POSTGRESQL_ONLY.items():
        source = (_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert any(
            isinstance(n, ast.Call) and _call_name(n) in _CONSTRUCTORS for n in ast.walk(tree)
        ), f"{relative} no longer builds an engine; drop its exemption"
        assert refusal in source, (
            f"{relative} is exempt as PostgreSQL-only because of `{refusal}`, which is gone"
        )


@pytest.mark.parametrize(
    ("source", "relative", "expected"),
    (
        # A planted unguarded construction, the exact failure this guard exists for.
        (
            "from sqlalchemy.ext.asyncio import create_async_engine\n"
            "async def f():\n    eng = create_async_engine('sqlite+aiosqlite:///:memory:')\n",
            "tests/unit/planted.py",
            1,
        ),
        (
            "async def f():\n    eng = create_async_engine(URL)\n"
            "    install_sqlite_transaction_control(eng.sync_engine)\n",
            "tests/unit/planted.py",
            0,
        ),
        # Installed in ANOTHER function does not count: scopes are not merged.
        (
            "def g(e):\n    install_sqlite_transaction_control(e)\n"
            "def f():\n    return create_engine('sqlite:///:memory:')\n",
            "tests/unit/planted.py",
            1,
        ),
        (
            "engine = create_async_engine(URL)\nif SQLITE:\n"
            "    install_sqlite_transaction_control(engine.sync_engine)\n",
            "tests/planted.py",
            0,
        ),
        ("import sqlalchemy\nE = sqlalchemy.create_engine(URL)\n", "scripts/planted.py", 1),
        (
            "import pytest\npytestmark = pytest.mark.postgres\n"
            "async def f():\n    create_async_engine(URL)\n",
            "tests/integration/planted_postgres.py",
            0,
        ),
        # The postgres marker does not excuse an engine that names SQLite itself.
        (
            "import pytest\npytestmark = pytest.mark.postgres\n"
            "async def f():\n    create_async_engine('sqlite+aiosqlite:///:memory:')\n",
            "tests/integration/planted_postgres.py",
            1,
        ),
        ("async def f():\n    async_engine_from_config({}, prefix='x.')\n", "scripts/planted.py", 1),
    ),
    ids=(
        "planted-unguarded", "guarded", "installed-elsewhere", "module-level-guarded",
        "attribute-form", "postgres-marked", "postgres-marked-but-sqlite", "from-config",
    ),
)
def test_the_guard_goes_red_on_a_planted_unguarded_construction(source, relative, expected) -> None:
    """Counter-test: a guard that cannot fail is the class of defect programme 015 exists to remove."""
    unguarded, _needed = _unguarded_constructions(source, relative)
    assert len(unguarded) == expected, unguarded
