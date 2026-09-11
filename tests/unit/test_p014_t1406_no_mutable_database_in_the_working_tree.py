"""T1406 / F-014-7: a mutable database outside the scratch tree must be RED, not invisible.

WHY THIS MODULE EXISTS, measured rather than asserted. `RT-014-6`, executed 2026-09-11: three
SQLite files were placed in the repository root and `scripts/verify_local.ps1` was run. Not one
step reddened - the test-database guard validates a URL and never looks at the filesystem, the
backend tier passed, both UIs linted, tested and built. `git status` was silent too, because
`.gitignore:47-48` covers `.pytest_*.db`. So the working tree could accumulate live databases and
every instrument in the repository would report health.

TWO GUARDS, BECAUSE ONE OF THEM IS ORDER-DEPENDENT BY CONSTRUCTION.

The filesystem guard finds a file that is there NOW. It is the operational symptom - a crashed run
leaves its database behind - but as a check it is weak in one specific way: if it runs before the
module that creates the stray, it passes. Test order decides its verdict, which is the very class
programme 014 exists to remove, so it is not left to carry this alone.

The source guard is order-independent. It reads `tests/**` and refuses a sqlite URL built from a
path outside the scratch tree, so a NEW module reintroducing the pattern is caught whether or not
it ever ran. That is the guard that closes the class; the filesystem one closes the incident.

Both have counter-tests below. A guard that cannot fail is what this programme is about, and the
first version of the T705 guard in programme 007 passed on a deletion because it matched a word
inside its own comment.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.scratch_db import SCRATCH_ROOT, scratch_db_path

_ROOT = Path(__file__).resolve().parents[2]

#: Directories that are not part of the repository's own sources.
_PRUNED = {
    ".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "htmlcov", ".idea", ".vscode",
}

#: Suffixes SQLite writes. A stray `-wal` with no `.db` beside it is still a live database.
_DB_SUFFIXES = (".db", ".db-journal", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")


def _is_sanctioned(path: Path) -> bool:
    """Where a database file is allowed to be, and why each place is allowed.

    - under `.local-run/`  : the scratch tree, gitignored, and what `verify_local.ps1` itself uses;
    - under `fixtures/`    : checked-in fixture data, which `.gitignore` explicitly un-ignores;
    - under a temp dir     : pytest's `tmp_path`, which is outside the repository anyway.
    """
    try:
        relative = path.relative_to(_ROOT)
    except ValueError:
        return True
    head = relative.parts[0] if relative.parts else ""
    return head in {".local-run", "fixtures"}


def _walk_for_databases(root: Path) -> list[Path]:
    """A pruning walk; `rglob` over this repository spends most of its time in node_modules."""
    found: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _PRUNED:
                    stack.append(entry)
            elif entry.name.endswith(_DB_SUFFIXES):
                found.append(entry)
    return found


def test_no_mutable_database_sits_outside_the_scratch_tree() -> None:
    """The incident guard: nothing is lying around right now."""
    scanned = _walk_for_databases(_ROOT)
    strays = sorted(str(p.relative_to(_ROOT)) for p in scanned if not _is_sanctioned(p))
    assert strays == [], (
        "mutable database files outside .local-run/ and fixtures/: "
        + ", ".join(strays)
        + ". A crashed run leaves these behind and nothing else in the repository notices - "
        "`.gitignore` hides them from `git status` and the test-database guard only reads a URL."
    )


def test_the_filesystem_guard_can_actually_see_a_stray(tmp_path: Path) -> None:
    """Counter-test. Without it the walk above could be pruning everything and still be green."""
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.db").write_bytes(b"x")
    (tmp_path / "visible.db").write_bytes(b"x")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "deep.sqlite3").write_bytes(b"x")

    names = sorted(p.name for p in _walk_for_databases(tmp_path))
    assert names == ["deep.sqlite3", "visible.db"], names


def test_the_scanned_set_is_not_empty_in_this_repository() -> None:
    """An empty search passing for a clean one is how this class survives.

    The scratch tree holds at least this session's own database, so a walk that finds NOTHING
    means the walk is broken, not that the tree is clean.
    """
    scanned = _walk_for_databases(_ROOT)
    assert scanned, "the walk found no database anywhere, including the scratch tree - it is broken"


# ---------------------------------------------------------------------------
# The source guard: order-independent, and the one that closes the class.
# ---------------------------------------------------------------------------

_SQLITE_URL = re.compile(r"sqlite(?:\+\w+)?:///")

#: Expression roots that decide a path OUTSIDE the repository or INSIDE the scratch tree.
#: `tmp_path` is pytest's per-test directory; the other two are `tests.scratch_db`.
_SANCTIONED_ROOTS = {"tmp_path", "scratch_db_path", "scratch_db_url", "SCRATCH_ROOT"}


def _roots(node: ast.AST) -> set[str]:
    """Every name and called function inside an expression, so an interpolation can be judged."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            names.add(inner.id)
        elif isinstance(inner, ast.Attribute):
            names.add(inner.attr)
    return names


def _bindings(tree: ast.Module) -> dict[str, ast.AST]:
    """Every `NAME = <expr>` in the file, module level and inside functions alike.

    Function-local matters: `test_alembic_postgres_only` writes
    `database_path = tmp_path / "local.db"` inside a helper and interpolates it, so a
    module-level-only scan judged it unsanctioned and the guard reported a file that puts its
    database in pytest's temp directory. Names are not scoped here - two functions binding the
    same name would merge - which is imprecision this guard accepts: it decides where a database
    file goes, not what a program means.
    """
    bindings: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
    return bindings


def _resolves_to_sanctioned(name: str, bindings: dict[str, ast.AST], seen: set[str]) -> bool:
    """Follow a module-level name to whatever decides its value.

    This is the whole point of the guard. The five modules it was written for all interpolated a
    name - `f"sqlite+aiosqlite:///{_TEST_DB_PATH}"` - so a guard reading only the literal saw
    `sqlite+aiosqlite:///` and no path at all, and would have passed on every one of them. What
    separates `_TEST_DB_PATH = ".pytest_deadlock_test.db"` from
    `_TEST_DB_PATH = str(scratch_db_path(...))` is not the URL, it is this binding.
    """
    if name in _SANCTIONED_ROOTS:
        return True
    if name in seen or name not in bindings:
        return False
    seen.add(name)
    value = bindings[name]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        # A bare string: sanctioned only if it names the scratch tree itself.
        return ".local-run" in value.value
    roots = _roots(value)
    if roots & _SANCTIONED_ROOTS:
        return True
    return any(_resolves_to_sanctioned(r, bindings, seen) for r in roots)


def _unsanctioned_sqlite_urls(source: str) -> list[str]:
    """sqlite URLs whose path is decided outside the scratch tree.

    Skips `:memory:`, anything naming `.local-run`, and any interpolation that resolves to
    `tmp_path` or `tests.scratch_db`.
    """
    tree = ast.parse(source)
    bindings = _bindings(tree)

    # An f-string is visited by `ast.walk` together with its own literal parts, so collect the
    # JoinedStr nodes first and exclude their descendants - otherwise every f-string counts twice,
    # which the counter-test below caught on the first edition of this file.
    joined = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]
    inside_joined = {id(d) for j in joined for d in ast.walk(j) if d is not j}

    offenders: list[str] = []

    for node in joined:
        literal = "".join(
            part.value for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        if not _SQLITE_URL.search(literal) or ":memory:" in literal or ".local-run" in literal:
            continue
        interpolations = [p for p in node.values if isinstance(p, ast.FormattedValue)]
        if interpolations and all(
            any(_resolves_to_sanctioned(r, bindings, set()) for r in _roots(p.value))
            for p in interpolations
        ):
            continue
        offenders.append(literal + "{...}")

    for node in ast.walk(tree):
        if id(node) in inside_joined:
            continue
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value
        if not _SQLITE_URL.search(text) or ":memory:" in text or ".local-run" in text:
            continue
        # A bare `sqlite:///` prefix with nothing after it is a fragment, not a location; it is
        # judged through the f-string branch above or through concatenation this guard does not
        # model. Only a literal that carries its own path is an offence here.
        if text.rstrip().endswith("///"):
            continue
        offenders.append(text)

    return offenders


def test_no_test_module_builds_a_sqlite_url_outside_the_scratch_tree() -> None:
    findings: list[str] = []
    scanned = 0
    for path in sorted((_ROOT / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        # This module and the guardrail modules quote such URLs as DATA - they assert what the
        # settings guard and the database guard REFUSE. Listing them here is a claim about each
        # file, which is the point: a new name cannot be added without saying why.
        # These quote a rejected or redacted URL as DATA - they assert what the settings guard and
        # the database guard REFUSE, so the string is the subject rather than a location. Naming
        # each one is a claim about that file; a new name cannot be added without making it.
        if path.name in {
            "test_p014_t1406_no_mutable_database_in_the_working_tree.py",
            "test_settings_guardrails.py",
            "test_test_database_guard.py",
            "test_run_full_stack_database_url_redaction.py",
        }:
            continue
        scanned += 1
        for offender in _unsanctioned_sqlite_urls(path.read_text(encoding="utf-8")):
            findings.append(f"{path.relative_to(_ROOT)}: {offender!r}")

    assert scanned > 50, scanned
    assert findings == [], (
        "test modules building a sqlite URL outside `.local-run/`: "
        + "; ".join(findings)
        + ". Use `tests.scratch_db.scratch_db_url(<slug>)`."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ('URL = "sqlite+aiosqlite:///.pytest_stray.db"', 1),
        ('P = ".pytest_stray.db"\nURL = f"sqlite+aiosqlite:///{P}"', 1),
        ('URL = "sqlite+aiosqlite:///./.local-run/test-runs/x/test.db"', 0),
        ('URL = "sqlite+aiosqlite:///:memory:"', 0),
        # The two forms that made the first edition of this guard wrong, kept as cases rather
        # than as a comment: a path handed in by pytest, and the helper this task introduced.
        ('URL = f"sqlite:///{tmp_path / \'stale.db\'}"', 0),
        ('P = str(scratch_db_path("x"))\nURL = f"sqlite+aiosqlite:///{P}"', 0),
    ),
    ids=("literal-relative", "f-string-relative", "scratch-tree", "memory",
         "tmp-path", "through-the-helper"),
)
def test_the_source_guard_detects_what_it_claims_to(source: str, expected: int) -> None:
    """Counter-test, including the f-string form the five offending modules actually used.

    The f-string case matters: `f"sqlite+aiosqlite:///{_TEST_DB_PATH}"` carries no path in its own
    literal, so a guard written against the whole string would have passed on every one of them.
    What it does carry is a sqlite URL with nothing sanctioned in it, which is what this matches.
    """
    assert len(_unsanctioned_sqlite_urls(source)) == expected


def test_the_scratch_helper_puts_the_file_where_the_guard_expects() -> None:
    path = scratch_db_path("t1406-self-check")
    assert path.parent.parent == SCRATCH_ROOT
    assert _is_sanctioned(path)
    assert path.parent.is_dir()
