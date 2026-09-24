"""017 `T1707`: the application has one dialect, and this guard keeps it that way.

WHY THIS EXISTS. Programme 017 removed SQLite from `app/`, `scripts/` and `migrations/env.py`: 50
dialect branches in eight modules, the SQLite transaction control, the SQLite startup probes and the
`aiosqlite` driver. A second dialect does not come back as a module - it comes back as one
`if engine.dialect.name != "postgresql": return` that quietly skips a lock on some path, which is
exactly how the default tier stopped seeing the equivalent owner lock before 017. So the rule is held
at the source, in executable code, whether or not any test runs that path.

It also succeeds `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py`
(deleted in 017 stage 3, slice S7): that guard required every SQLite engine construction to carry the
SQLite transaction control. With the control and every SQLite engine gone it would have been a guard
over the empty set - the vacuous guard AGENTS.md §9 forbids. What it protected - no SQLite engine
without the control - is now the stronger "no SQLite in executable code at all", held here (a SQLite
engine needs a `sqlite` URL or driver import, both of which this guard reports), and on the test tier
by `tests/conftest.py`, which refuses a non-PostgreSQL `TEST_DATABASE_URL` before collection.

WHAT IS SCANNED. Every `.py` file under `app/` and `scripts/`, and `migrations/env.py`, parsed with
`ast`. Docstrings (the first statement of a module, class or function, exactly what
`ast.get_docstring` reads) and comments are NOT code and are not scanned: dated measurements of SQLite
behaviour stay in them as history (AGENTS.md §1). Every other string literal IS scanned, including
f-string parts and SQL text, so a dialect cannot hide in a string.

FOUR KINDS OF FINDING, each counted per file:

* `sqlite` - an identifier (name, attribute, keyword, import, def/class/argument name) or a
  non-docstring string literal containing `sqlite`, any case. Covers `import aiosqlite`,
  `import sqlite3`, `sqlite_where=`, `sqlite_busy_*`, and `sqlite+aiosqlite://` URLs.
* `dialect-object` - any `.dialect` attribute read.
* `dialect-identity` - a read that names the backend: `.name` / `.driver` on a dialect expression,
  `get_backend_name()`, `get_driver_name()`, `.drivername`, `_is_postgres()`.
* `backend-literal` - a comparison against a backend name literal (`"postgresql"`, `"postgres"`,
  `"sqlite"`, `"asyncpg"`, `"aiosqlite"`, `"postgresql+asyncpg"`), directly or inside a
  set/tuple/list literal, or a `dialect=` keyword with such a literal (`ddl_if(dialect=...)`).

An attribute read spelled `getattr(<expr>, "<literal>")` counts exactly like `<expr>.<literal>`, for
every attribute and call name above (2026-09-24, closing review of 017 stage 3: the guard missed
`getattr(engine, "dialect").name`). A `getattr` whose name is not a string literal is not resolved.

THE ALLOW-LIST is exact counts per (file, kind), each with a reason and a date. Not everything that
reads a dialect is a second dialect: a refusal that enforces the one engine, a wire field, and the
journal handing the dialect object to SQLAlchemy's own compiler and type processors. An exact count
is two-sided (AGENTS.md §6): a new read in an allow-listed file is red, and so is a read that
disappeared without the entry being lowered.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

REPO = Path(__file__).resolve().parents[2]

_BACKEND_NAMES = frozenset(
    {"postgresql", "postgres", "sqlite", "asyncpg", "aiosqlite", "postgresql+asyncpg"}
)
_IDENTITY_CALLS = frozenset({"get_backend_name", "get_driver_name", "_is_postgres"})
_IDENTITY_ATTRS_ON_DIALECT = frozenset({"name", "driver"})

#: What this guard does NOT see - part of every failure message, so its silence is never read as
#: more than it is (AGENTS.md §11, §12).
_BLIND_SPOTS = (
    "WHAT THIS GUARD DOES NOT SEE: non-Python files (`scripts/*.ps1`, `docker/`, workflows, "
    "Dockerfiles, `.env*`), `tests/` (its fixtures spell SQLite URLs as refusal inputs; the tier "
    "itself is guarded by `tests/conftest.py`), comments and docstrings, an attribute named by "
    "anything but a string literal (`getattr(x, attr_name)`, `getattr(x, 'dia' + 'lect')`, `operator.attrgetter('dialect.name')`), "
    "names assembled at runtime (`'sq' + 'lite'`), and a backend name compared through a variable "
    "(`b = 'postgresql'; if name == b`). It checks FORM, not truth: green does not prove the code is "
    "correct on PostgreSQL - the backend tier does that."
)


class Allowed(NamedTuple):
    path: str
    kind: str
    count: int
    reason: str
    date: str


ALLOWED: tuple[Allowed, ...] = (
    Allowed(
        "app/api/v1/health.py",
        "dialect-identity",
        2,
        "`GET /health/db` reports `get_backend_name()` as its `db.dialect` wire field "
        "(`api/openapi.yaml`); a reported value, not a branch",
        "2026-09-24",
    ),
    # `app/core/ledger/journal.py` (x5) LEFT WITH THE LISTENER JOURNAL, 018 stage B1, 2026-09-24.
    # Its one AUTOCOMMIT read moved to the book, below; the other four had no successor.
    Allowed(
        "app/core/ledger/book.py",
        "dialect-object",
        1,
        "one AUTOCOMMIT detection reading the engine-level `_on_connect_isolation_level` "
        "(`_is_autocommit_configured`, moved from the deleted listener journal); never compared "
        "with a backend name, never branched on per dialect",
        "2026-09-24",
    ),
    # REFUSALS THAT ENFORCE THE ONE DIALECT. Each reads the backend of a URL it was handed only to
    # refuse anything that is not PostgreSQL; there is no second arm that runs.
    Allowed(
        "app/config.py",
        "dialect-identity",
        1,
        "`_require_postgresql_database_url` refuses a DATABASE_URL whose `drivername` is not "
        "postgresql+asyncpg (017 T1704)",
        "2026-09-24",
    ),
    Allowed(
        "migrations/env.py",
        "dialect-identity",
        1,
        "`_require_postgresql_migration_url` refuses any non-PostgreSQL URL before a migration runs",
        "2026-09-24",
    ),
    Allowed(
        "migrations/env.py",
        "backend-literal",
        1,
        "the same refusal's comparison with 'postgresql'",
        "2026-09-24",
    ),
    Allowed(
        "scripts/dev_database.py",
        "dialect-identity",
        1,
        "`assert_safe_dev_database_url` refuses a launcher database that is not PostgreSQL",
        "2026-09-24",
    ),
    Allowed(
        "scripts/dev_database.py",
        "backend-literal",
        1,
        "the same refusal's comparison with 'postgresql'",
        "2026-09-24",
    ),
    Allowed(
        "scripts/run_simulator_run_and_analyze.py",
        "dialect-identity",
        2,
        "`_database_dsn` refuses a non-PostgreSQL --database-url, and names its backend in the "
        "refusal message",
        "2026-09-24",
    ),
    Allowed(
        "scripts/run_simulator_run_and_analyze.py",
        "backend-literal",
        1,
        "the same refusal's comparison with 'postgresql'",
        "2026-09-24",
    ),
    Allowed(
        "scripts/seed_recipe.py",
        "dialect-identity",
        1,
        "the seed's disposable-database check accepts only PostgreSQL names and refuses the rest",
        "2026-09-24",
    ),
    Allowed(
        "scripts/seed_recipe.py",
        "backend-literal",
        1,
        "the same check's `backend in {'postgresql', 'postgres'}`",
        "2026-09-24",
    ),
    Allowed(
        "scripts/validate_test_database_url.py",
        "dialect-identity",
        2,
        "the test-database guard refuses a non-PostgreSQL TEST_DATABASE_URL and names the backend "
        "in its dry-run report",
        "2026-09-24",
    ),
    Allowed(
        "scripts/validate_test_database_url.py",
        "backend-literal",
        2,
        "the same guard: `required_backend` may only be 'postgresql', and the URL's backend must be",
        "2026-09-24",
    ),
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of the Constant nodes `ast.get_docstring` would read, and only those."""

    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _attribute_read(node: ast.AST) -> tuple[str, ast.AST] | None:
    """`(name, owner)` for `owner.name` and for `getattr(owner, "name"[, default])`, else None."""

    if isinstance(node, ast.Attribute):
        return node.attr, node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value, node.args[0]
    return None


def _is_dialect_expr(node: ast.AST) -> bool:
    read = _attribute_read(node)
    return (read is not None and read[0] == "dialect") or (
        isinstance(node, ast.Name) and node.id == "dialect"
    )


def _backend_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.lower() in _BACKEND_NAMES
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return any(_backend_literal(element) for element in node.elts)
    return False


def _mentions_sqlite(text: str | None) -> bool:
    return text is not None and "sqlite" in text.lower()


def scan_source(source: str) -> dict[str, list[int]]:
    """Every finding in `source`, as {kind: [line, ...]}."""

    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    findings: dict[str, list[int]] = {}

    def add(kind: str, node: ast.AST) -> None:
        findings.setdefault(kind, []).append(getattr(node, "lineno", 0))

    for node in ast.walk(tree):
        # --- sqlite, in identifiers and executable strings
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and _mentions_sqlite(node.value):
                add("sqlite", node)
        elif isinstance(node, ast.Name) and _mentions_sqlite(node.id):
            add("sqlite", node)
        elif isinstance(node, ast.Attribute) and _mentions_sqlite(node.attr):
            add("sqlite", node)
        elif isinstance(node, ast.keyword) and _mentions_sqlite(node.arg):
            add("sqlite", node.value)
        elif isinstance(node, ast.alias) and (
            _mentions_sqlite(node.name) or _mentions_sqlite(node.asname)
        ):
            add("sqlite", node)
        elif isinstance(node, ast.ImportFrom) and _mentions_sqlite(node.module):
            add("sqlite", node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
            _mentions_sqlite(node.name)
        ):
            add("sqlite", node)
        elif isinstance(node, ast.arg) and _mentions_sqlite(node.arg):
            add("sqlite", node)

        # --- dialect reads
        read = _attribute_read(node)
        if read is not None:
            attr, owner = read
            if attr == "dialect":
                add("dialect-object", node)
            if attr in _IDENTITY_ATTRS_ON_DIALECT and _is_dialect_expr(owner):
                add("dialect-identity", node)
            if attr == "drivername":
                add("dialect-identity", node)
        if isinstance(node, ast.Call):
            func = node.func
            func_read = _attribute_read(func)
            called = func_read[0] if func_read is not None else getattr(func, "id", None)
            if called in _IDENTITY_CALLS:
                add("dialect-identity", node)
            for keyword in node.keywords:
                if keyword.arg == "dialect" and _backend_literal(keyword.value):
                    add("backend-literal", keyword.value)

        # --- comparisons against a backend name
        if isinstance(node, ast.Compare):
            if any(_backend_literal(side) for side in [node.left, *node.comparators]):
                add("backend-literal", node)

    return findings


def _scanned_files() -> list[Path]:
    files = sorted((REPO / "app").rglob("*.py")) + sorted((REPO / "scripts").rglob("*.py"))
    files.append(REPO / "migrations" / "env.py")
    return [path for path in files if "__pycache__" not in path.parts]


def _repository_findings() -> dict[tuple[str, str], list[int]]:
    counted: dict[tuple[str, str], list[int]] = {}
    for path in _scanned_files():
        relative = path.relative_to(REPO).as_posix()
        for kind, lines in scan_source(path.read_text(encoding="utf-8")).items():
            counted[(relative, kind)] = lines
    return counted


def test_the_application_has_no_second_dialect() -> None:
    """(a) dialect reads and (b) `sqlite` in executable code: zero outside the exact allow-list."""

    findings = _repository_findings()
    allowed = {(entry.path, entry.kind): entry for entry in ALLOWED}

    unexpected = {
        key: lines
        for key, lines in findings.items()
        if key not in allowed or len(lines) > allowed[key].count
    }
    stale = {
        key: (entry.count, len(findings.get(key, [])))
        for key, entry in allowed.items()
        if len(findings.get(key, [])) < entry.count
    }

    problems: list[str] = []
    for (path, kind), lines in sorted(unexpected.items()):
        limit = allowed[(path, kind)].count if (path, kind) in allowed else 0
        problems.append(f"  {path}: {kind} x{len(lines)} (allowed {limit}) at lines {lines}")
    for (path, kind), (expected, actual) in sorted(stale.items()):
        problems.append(
            f"  {path}: {kind} allow-listed x{expected} but found x{actual} - the read went away; "
            f"lower or remove the entry (an improvement is recorded, never left to drift)"
        )
    assert not problems, (
        "A second database dialect is creeping back into executable code (programme 017 made "
        "PostgreSQL the only engine):\n"
        + "\n".join(problems)
        + "\nRemove the read or the SQLite reference. If it is not a second dialect - a refusal that "
        "enforces PostgreSQL, a wire field - add an exact-count entry to ALLOWED in this file with "
        "its reason and date.\n" + _BLIND_SPOTS
    )


def test_every_allow_list_entry_names_a_reason_and_a_date() -> None:
    for entry in ALLOWED:
        assert entry.count > 0 and len(entry.reason) > 20 and len(entry.date) == 10, entry
        assert (REPO / entry.path).is_file(), f"{entry.path} is allow-listed but does not exist"


def test_the_scan_is_not_vacuous() -> None:
    """The scanner reads a real tree: many files, and the allow-listed reads are actually found.

    A guard that parsed nothing, or scanned the wrong directory, would report zero findings - the
    same result as a clean tree. Counting files and re-finding the known reads tells the two apart.
    """

    files = _scanned_files()
    assert len(files) >= 100, f"only {len(files)} files scanned; the scan roots are wrong"
    assert any(path.as_posix().endswith("app/core/payments/engine.py") for path in files)
    findings = _repository_findings()
    for entry in ALLOWED:
        assert len(findings.get((entry.path, entry.kind), [])) == entry.count, (
            f"the scanner no longer finds the allow-listed {entry.kind} in {entry.path}: "
            f"either the read moved or the scanner went blind"
        )


# Positive counter-checks (AGENTS.md §9): every kind is FOUND in a planted snippet.
@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ('if engine.dialect.name != "postgresql":\n    pass\n', "dialect-identity"),
        ('if engine.dialect.name != "postgresql":\n    pass\n', "dialect-object"),
        ('if engine.dialect.name != "postgresql":\n    pass\n', "backend-literal"),
        ("def f(dialect):\n    return dialect.name\n", "dialect-identity"),
        ("x = conn.dialect.driver\n", "dialect-identity"),
        ("backend = make_url(u).get_backend_name()\n", "dialect-identity"),
        ("driver = url.get_driver_name()\n", "dialect-identity"),
        ("driver = make_url(u).drivername\n", "dialect-identity"),
        ("if self._is_postgres():\n    pass\n", "dialect-identity"),
        ('if backend in {"postgresql", "postgres"}:\n    pass\n', "backend-literal"),
        ('ok = "sqlite" == name\n', "backend-literal"),
        ('c = CheckConstraint("x").ddl_if(dialect="postgresql")\n', "backend-literal"),
        ("import aiosqlite\n", "sqlite"),
        ("import sqlite3\n", "sqlite"),
        ("from sqlite3 import connect\n", "sqlite"),
        ("from app.db.x import sqlite_busy_error_name\n", "sqlite"),
        ('Index("i", c, postgresql_where=w, sqlite_where=w)\n', "sqlite"),
        ('URL = "sqlite+aiosqlite:///:memory:"\n', "sqlite"),
        ('msg = f"refused {x}: SQLite is gone"\n', "sqlite"),
        ("def use_sqlite():\n    pass\n", "sqlite"),
        # A string that is NOT the first statement is code, however it is quoted.
        ('def f():\n    x = 1\n    """on SQLite this differs"""\n', "sqlite"),
        ('SQL = """SELECT sqlite_version()"""\n', "sqlite"),
        # `getattr` with a literal name is the attribute read it spells (2026-09-24).
        ('if getattr(engine, "dialect").name != "postgresql":\n    pass\n', "dialect-identity"),
        ('if getattr(engine, "dialect").name != "postgresql":\n    pass\n', "dialect-object"),
        ('x = getattr(getattr(engine, "dialect"), "name")\n', "dialect-identity"),
        ('x = getattr(engine.dialect, "name")\n', "dialect-identity"),
        ('x = getattr(conn.dialect, "driver", None)\n', "dialect-identity"),
        ('x = getattr(url, "drivername")\n', "dialect-identity"),
        ('x = getattr(url, "get_backend_name")()\n', "dialect-identity"),
    ],
)
def test_the_scanner_finds_a_planted_reference(source: str, kind: str) -> None:
    assert scan_source(source).get(kind), f"{kind} not found in {source!r}"


# Negative counter-checks: what the guard deliberately does NOT count.
@pytest.mark.parametrize(
    "source",
    [
        '"""Module docstring: measured on SQLite 2026-09-12."""\n',
        'class C:\n    """On SQLite this used to differ."""\n',
        'async def f():\n    """aiosqlite forwarded it; see dialect.name history."""\n    return 1\n',
        "# a comment about sqlite and engine.dialect.name\nx = 1\n",
        'Index("i", c, postgresql_where=text("state = \'OPEN\'"))\n',
        'url = URL.create("postgresql", host="h")\n',
        "participant.name\n",
        'getattr(participant, "name")\n',
        'getattr(getattr(x, "participant"), "name")\n',
        'await session.connection(execution_options={"postgresql_readonly": True})\n',
    ],
)
def test_the_scanner_ignores_what_is_not_a_second_dialect(source: str) -> None:
    assert scan_source(source) == {}, scan_source(source)
