"""Where a test is allowed to put a mutable SQLite file, and why it is not the repo root.

T1406 of programme 014 (`F-014-7`), carried into programme 015 because 015 writes many new checks
and its runs need this.

THE PROBLEM. Five test modules built their own SQLite engine from a RELATIVE path - values like
`".pytest_deadlock_test.db"` - so the file landed in whatever directory the run started from,
which in practice is the repository root. That violates `AGENTS.md` §7 and §12, and nothing
noticed: the test-database guard in `scripts/verify_local.ps1` validates the URL and never looks at
the filesystem, and `.gitignore:47-48` covers `.pytest_*.db`, so `git status` is silent too.
Measured 2026-09-11 as `RT-014-6`: three such files placed in the repository root survived every
step of the canonical milestone command without one of them reddening.

The finding recorded two modules. There were five. Re-counted by grep over `tests/**` for a
relative sqlite URL, which is also what the source guard below now enforces.

WHY A DIRECTORY PER SLUG, and not one shared file: the working tree is shared by several concurrent
sessions, and `.local-run/test-runs/<slug>/` is the layout `scripts/verify_local.ps1` already uses
for exactly that reason. A module that takes its own slug cannot collide with a neighbour's run.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: The one directory tree a test may write a mutable database into.
SCRATCH_ROOT = _ROOT / ".local-run" / "test-runs"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")

#: SQLite writes these beside the database file itself.
SIDECAR_SUFFIXES = ("", "-journal", "-wal", "-shm")


def scratch_db_path(slug: str) -> Path:
    """An absolute path to this module's own database file, its directory created.

    `slug` identifies the module, not the test: the fixtures that use this create and drop the
    schema per test and want one stable location to clean.
    """
    if not _SLUG_RE.match(slug):
        raise ValueError(f"scratch db slug must be lowercase kebab-case: {slug!r}")
    directory = SCRATCH_ROOT / slug
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "test.db"


def scratch_db_url(slug: str) -> str:
    """The aiosqlite URL for `slug`, as a POSIX path so the URL is well-formed on Windows."""
    return f"sqlite+aiosqlite:///{scratch_db_path(slug).as_posix()}"


def remove_scratch_db(slug: str) -> None:
    """Delete the database and every sidecar SQLite may have left beside it.

    Swallows errors on purpose: a file still held open by a disposed engine must not turn a
    teardown into a failure, and the guard in
    `tests/unit/test_p014_t1406_no_mutable_database_in_the_working_tree.py` is what notices if one
    is genuinely left behind.
    """
    base = scratch_db_path(slug)
    for suffix in SIDECAR_SUFFIXES:
        try:
            Path(str(base) + suffix).unlink(missing_ok=True)
        except OSError:
            pass
