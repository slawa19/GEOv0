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
`create_engine`, `async_engine_from_config` or `engine_from_config` must be PAIRED with
`install_sqlite_transaction_control(...)` called on THAT engine object, in the same scope - the
enclosing function, or the module body for a module-level engine.

WHAT "PAIRED" MEANS, and why it is not "an installer exists somewhere in the scope". Until
2026-09-12 this guard credited one installer call to every construction in the same scope, compared
call names as bare text, counted an installer inside `if False:`, and excused any `pytest.mark.postgres`
module that contained no literal sqlite URL. An external review demonstrated all four as executable
bypasses. Each is now a counter-test at the bottom of this module:

* an ALIAS (`from sqlalchemy import create_engine as build`) is resolved to the real constructor;
* TWO constructions with ONE installer leave the unpaired one red - the installer's argument is
  matched to the name the engine was assigned to (`install(eng.sync_engine)` pairs with `eng`);
* an installer in a branch a constant test makes DEAD (`if False:`) is not counted;
* a postgres-marked module is no longer excused merely for lacking a literal sqlite URL.

EXEMPTIONS, each of which must be a refusal that exists in the code:

* a file listed in `_POSTGRESQL_ONLY` together with the refusal that makes it PostgreSQL-only. The
  refusal is part of the entry: if it disappears from the file, the exemption is red.
* a construction that a `sqlite` EARLY RETURN has already excluded - the shape of
  `app/db/session.py`, where `if url.startswith("sqlite"): ... return engine` precedes the
  PostgreSQL construction, so the later one is unreachable for a SQLite URL.
* a postgres-marked test module, but ONLY through a refusal: either the module refuses a
  non-PostgreSQL backend in code (`if "postgresql" not in url: pytest.skip(...)`, or an
  `assert ... == "postgresql"`), or the construction's URL is `TEST_DATABASE_URL`, which
  `tests/conftest.py`'s `pytest_collection_finish` fails closed on for any postgres-marked
  selection. A postgres-marked module that builds an engine from some OTHER url - a helper, a
  literal, an interpolation - is NOT exempt, because the marker constrains the session's backend
  and says nothing about an engine the module builds for itself.

WHAT IT STILL DOES NOT SEE, stated so the promise is not larger than the check:

* it is a per-scope AST check, not dataflow. Rebinding (`f = create_engine; f(...)`), a constructor
  reached through a variable, or an engine built by a helper OUTSIDE the four scanned directories is
  invisible to it;
* pairing is by NAME, so two engines that swap names, or an engine passed through a container
  (`engines["a"] = create_engine(...)`), are not tracked;
* reachability is only constant-folding of `if`/`while` tests. An installer made dead by a runtime
  condition (`if self.enabled:`) still counts as installed;
* it proves an installer CALL is present, never that it ran. `tests/unit/test_p015_t1525_sqlite_transaction_control_is_in_effect.py`
  checks the live test engine at runtime for that reason, and
  `sqlite_transaction_control_is_installed` itself reports only listener registration.
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

#: A URL name the test session itself guarantees to be PostgreSQL whenever a postgres marker is in
#: play: `tests/conftest.py::pytest_collection_finish` raises `pytest.UsageError` if any
#: postgres-marked test is selected while `TEST_DATABASE_URL` is not PostgreSQL.
_POSTGRES_GUARANTEED_URL_NAMES = {"TEST_DATABASE_URL"}

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
    "tests/unit/test_p015_t1525_a_busy_does_not_mask_and_does_not_promise.py",
}


# ---------------------------------------------------------------------------
# Name resolution: an alias is the constructor it was imported as.
# ---------------------------------------------------------------------------


def _alias_map(tree: ast.Module) -> dict[str, str]:
    """Local name -> canonical name, for `from ... import create_engine as build` and friends."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name in _CONSTRUCTORS or imported.name == _INSTALLER:
                    aliases[imported.asname or imported.name] = imported.name
    return aliases


def _call_name(node: ast.Call, aliases: dict[str, str]) -> str | None:
    if isinstance(node.func, ast.Name):
        return aliases.get(node.func.id, node.func.id)
    if isinstance(node.func, ast.Attribute):
        # `sqlalchemy.create_engine(...)` / `sa.create_engine(...)`
        return node.func.attr
    return None


def _base_name(expr: ast.AST) -> str | None:
    """The leftmost Name of an expression: `eng.sync_engine` -> 'eng', `engine` -> 'engine'."""
    node = expr
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Await)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


# ---------------------------------------------------------------------------
# Reachability: an installer a constant test makes dead is not an installer.
# ---------------------------------------------------------------------------


def _constant_truth(test: ast.AST) -> bool | None:
    """True/False for a literal test, None when the test is decided at runtime."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    return None


def _reachable_children(node: ast.AST):
    if isinstance(node, ast.If):
        yield node.test
        truth = _constant_truth(node.test)
        if truth is True:
            yield from node.body
        elif truth is False:
            yield from node.orelse
        else:
            yield from node.body
            yield from node.orelse
        return
    if isinstance(node, ast.While) and _constant_truth(node.test) is False:
        yield node.test
        yield from node.orelse
        return
    yield from ast.iter_child_nodes(node)


def _scopes(tree: ast.Module) -> list[ast.AST]:
    return [tree] + [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _own_nodes(scope: ast.AST):
    """Reachable nodes of this scope, not descending into nested functions (own scopes)."""
    stack = list(_reachable_children(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield node
        stack.extend(_reachable_children(node))


# ---------------------------------------------------------------------------
# The PostgreSQL exemptions, each tied to a refusal that exists in the code.
# ---------------------------------------------------------------------------


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


def _mentions_postgres(node: ast.AST) -> bool:
    return any(
        isinstance(inner, ast.Constant)
        and isinstance(inner.value, str)
        and "postgres" in inner.value.lower()
        for inner in ast.walk(node)
    )


def _refuses_non_postgres(tree: ast.Module) -> bool:
    """A refusal in code: skip/raise under a postgres test, or an assert naming postgresql."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _mentions_postgres(node.test):
            for statement in node.body:
                for inner in ast.walk(statement):
                    if isinstance(inner, ast.Raise):
                        return True
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "skip"
                    ):
                        return True
        if isinstance(node, ast.Assert) and _mentions_postgres(node.test):
            return True
    return False


def _url_is_session_guaranteed_postgres(call: ast.Call) -> bool:
    """The engine is built from `TEST_DATABASE_URL`, which the collection guard fails closed on."""
    if not call.args:
        return False
    return _base_name(call.args[0]) in _POSTGRES_GUARANTEED_URL_NAMES


def _excluded_by_a_sqlite_early_return(scope: ast.AST, call: ast.Call) -> bool:
    """`app/db/session.py`'s shape: the SQLite branch returns, so what follows cannot be SQLite."""
    body = getattr(scope, "body", None)
    if not isinstance(body, list):
        return False
    for statement in body:
        if not isinstance(statement, ast.If) or not _mentions_sqlite(statement.test):
            continue
        returns = any(
            isinstance(inner, ast.Return) for item in statement.body for inner in ast.walk(item)
        )
        end = getattr(statement, "end_lineno", None)
        if returns and end is not None and call.lineno > end:
            return True
    return False


def _mentions_sqlite(node: ast.AST) -> bool:
    return any(
        isinstance(inner, ast.Constant)
        and isinstance(inner.value, str)
        and "sqlite" in inner.value.lower()
        for inner in ast.walk(node)
    )


# ---------------------------------------------------------------------------
# The scan.
# ---------------------------------------------------------------------------


def _unguarded_constructions(source: str, relative: str) -> tuple[list[str], int]:
    """(unguarded construction sites, number of constructions that had to be guarded)."""
    tree = ast.parse(source)
    if relative in _POSTGRESQL_ONLY:
        return [], 0

    aliases = _alias_map(tree)
    postgres_marked = _is_postgres_marked(tree)
    module_refuses = _refuses_non_postgres(tree)

    unguarded: list[str] = []
    guarded_needed = 0

    for scope in _scopes(tree):
        nodes = list(_own_nodes(scope))
        constructions = [
            n for n in nodes if isinstance(n, ast.Call) and _call_name(n, aliases) in _CONSTRUCTORS
        ]
        if not constructions:
            continue

        # Which name each construction was assigned to, so an installer can be paired with it.
        assigned_name: dict[int, str] = {}
        for node in nodes:
            if isinstance(node, ast.Assign):
                targets: list[ast.AST] = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            value = getattr(node, "value", None)
            if isinstance(value, ast.Call) and _call_name(value, aliases) in _CONSTRUCTORS:
                for target in targets:
                    if isinstance(target, ast.Name):
                        assigned_name[id(value)] = target.id

        installed_names: set[str] = set()
        installed_inline: set[int] = set()
        for node in nodes:
            if not isinstance(node, ast.Call) or _call_name(node, aliases) != _INSTALLER:
                continue
            if not node.args:
                continue
            argument = node.args[0]
            base = _base_name(argument)
            if base is not None:
                installed_names.add(base)
            # `install_sqlite_transaction_control(create_engine(...).sync_engine)`
            for inner in ast.walk(argument):
                if isinstance(inner, ast.Call) and _call_name(inner, aliases) in _CONSTRUCTORS:
                    installed_inline.add(id(inner))

        for call in constructions:
            if _excluded_by_a_sqlite_early_return(scope, call):
                continue
            if postgres_marked and (module_refuses or _url_is_session_guaranteed_postgres(call)):
                continue

            guarded_needed += 1
            name = assigned_name.get(id(call))
            paired = (name is not None and name in installed_names) or id(call) in installed_inline
            if not paired:
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
        aliases = _alias_map(tree)
        constructions_seen += sum(
            1
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _call_name(n, aliases) in _CONSTRUCTORS
        )
        unguarded, needed = _unguarded_constructions(source, relative)
        findings.extend(unguarded)
        if needed and not unguarded:
            guarded_files.add(relative)

    missing_known = sorted(_KNOWN_SQLITE_CONSTRUCTIONS - guarded_files)
    assert not findings, (
        "SQLite-capable engine constructions not PAIRED with `install_sqlite_transaction_control` "
        f"on the same engine in the same scope: {findings}. Without it a savepoint opened before "
        "the first write is its own transaction on SQLite and a root rollback does not undo it "
        "(T1525). Install it right after the construction, on that engine; if the engine can only "
        "ever be PostgreSQL, make that a refusal in code and record it in `_POSTGRESQL_ONLY` with "
        "the refusal's text."
    )
    assert not missing_known, (
        f"non-vacuity: known SQLite engine constructions not found guarded by the scan: {missing_known}"
    )
    assert constructions_seen > len(_KNOWN_SQLITE_CONSTRUCTIONS), constructions_seen


def test_every_postgresql_only_exemption_still_carries_its_refusal() -> None:
    for relative, refusal in _POSTGRESQL_ONLY.items():
        source = (_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        aliases = _alias_map(tree)
        assert any(
            isinstance(n, ast.Call) and _call_name(n, aliases) in _CONSTRUCTORS
            for n in ast.walk(tree)
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
        # The postgres marker does not excuse an engine that names SQLite itself.
        (
            "import pytest\npytestmark = pytest.mark.postgres\n"
            "async def f():\n    create_async_engine('sqlite+aiosqlite:///:memory:')\n",
            "tests/integration/planted_postgres.py",
            1,
        ),
        ("async def f():\n    async_engine_from_config({}, prefix='x.')\n", "scripts/planted.py", 1),
        # --- BYPASS 1: an aliased constructor was invisible (reviewer probe: findings=[]).
        # `:memory:` deliberately: `tests/unit/test_p014_t1406_...` scans `tests/**` for sqlite URLs
        # outside the scratch tree and cannot tell a quoted counter-test fixture from a real one.
        (
            "from sqlalchemy import create_engine as build\n"
            "def f():\n    eng = build('sqlite:///:memory:')\n",
            "tests/unit/planted.py",
            1,
        ),
        (
            "from sqlalchemy import create_engine as build\n"
            "def f():\n    eng = build('sqlite:///:memory:')\n"
            "    install_sqlite_transaction_control(eng)\n",
            "tests/unit/planted.py",
            0,
        ),
        # --- BYPASS 2: one installer credited to two engines (reviewer probe: findings=[], 2).
        (
            "def f():\n    a = create_engine(URL)\n    b = create_engine(URL)\n"
            "    install_sqlite_transaction_control(a.sync_engine)\n",
            "tests/unit/planted.py",
            1,
        ),
        (
            "def f():\n    a = create_engine(URL)\n    b = create_engine(URL)\n"
            "    install_sqlite_transaction_control(a.sync_engine)\n"
            "    install_sqlite_transaction_control(b.sync_engine)\n",
            "tests/unit/planted.py",
            0,
        ),
        # --- BYPASS 3: an installer that can never run (reviewer probe: findings=[], 1).
        (
            "def f():\n    eng = create_engine(URL)\n    if False:\n"
            "        install_sqlite_transaction_control(eng.sync_engine)\n",
            "tests/unit/planted.py",
            1,
        ),
        (
            "def f():\n    eng = create_engine(URL)\n    if True:\n"
            "        install_sqlite_transaction_control(eng.sync_engine)\n",
            "tests/unit/planted.py",
            0,
        ),
        # --- BYPASS 4: postgres-marked, SQLite URL from a helper (reviewer probe: findings=[], 0).
        (
            "import pytest\npytestmark = pytest.mark.postgres\n"
            "from tests.scratch_db import scratch_db_url\n"
            "def f():\n    eng = create_async_engine(scratch_db_url('x'))\n",
            "tests/integration/planted_postgres.py",
            1,
        ),
        # ... and the two shapes that legitimately stay exempt, so the fix is not a blanket ban.
        (
            "import pytest\npytestmark = pytest.mark.postgres\n"
            "def f():\n    url = os.environ.get('TEST_DATABASE_URL', '')\n"
            "    if 'postgresql' not in url:\n        pytest.skip('postgres only')\n"
            "    eng = create_async_engine(url)\n",
            "tests/integration/planted_postgres.py",
            0,
        ),
        (
            "import pytest\npytestmark = pytest.mark.postgres\n"
            "def f():\n    eng = create_async_engine(TEST_DATABASE_URL, pool_size=2)\n",
            "tests/integration/planted_postgres.py",
            0,
        ),
        # The `app/db/session.py` shape: the SQLite branch returns before the PostgreSQL engine.
        (
            "def _create_engine():\n    url = settings.DATABASE_URL\n"
            "    if url.startswith('sqlite'):\n        engine = create_async_engine(url)\n"
            "        install_sqlite_transaction_control(engine.sync_engine)\n"
            "        return engine\n"
            "    return create_async_engine(url, pool_size=5)\n",
            "app/planted.py",
            0,
        ),
    ),
    ids=(
        "planted-unguarded", "guarded", "installed-elsewhere", "module-level-guarded",
        "attribute-form", "postgres-marked-but-sqlite", "from-config",
        "bypass1-alias-unguarded", "bypass1-alias-guarded",
        "bypass2-two-engines-one-installer", "bypass2-two-engines-two-installers",
        "bypass3-installer-in-dead-branch", "bypass3-installer-in-live-branch",
        "bypass4-postgres-marked-sqlite-from-helper",
        "postgres-marked-with-refusal", "postgres-marked-test-database-url",
        "sqlite-early-return-then-postgres",
    ),
)
def test_the_guard_goes_red_on_a_planted_unguarded_construction(source, relative, expected) -> None:
    """Counter-test: a guard that cannot fail is the class of defect programme 015 exists to remove.

    The `bypass*` cases are the four the external review of T1525 demonstrated against the previous
    edition of this guard, each of which it reported as clean.
    """
    unguarded, _needed = _unguarded_constructions(source, relative)
    assert len(unguarded) == expected, unguarded
