"""Fail-closed validation for database URLs used by the test harness.

The command-line entrypoint reads environment variables so credentials are not
copied into process arguments or logs.  Importers can use
``assert_safe_test_database_url`` without opening a database connection.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence

from sqlalchemy.engine import URL, make_url


_POSTGRES_TEST_DATABASE_RE = re.compile(r"^geov0_test_[A-Za-z0-9_-]+$")
_TASK_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

#: The separator provisioning puts between a tier database and the scratch databases it derives from
#: it (``tests/migrated_schema.py::SCRATCH_SEPARATOR``).  IT IS RESERVED, AND THIS IS WHERE THE
#: RESERVATION IS MADE (2026-09-22, Codex external review of ``e2e1380..37fec08``).  Until then both
#: ``geov0_test_a`` and ``geov0_test_a__b`` were accepted as tier databases, so ``geov0_test_a__b``
#: was simultaneously task ``a__b``'s own database and, to the stale-database sweep run for task
#: ``a``, that task's orphaned scratch database - which it disconnected and dropped.  Reproduced on
#: PostgreSQL 16 with two disposable databases.  Refusing the ambiguous TIER name here is what makes
#: ``<tier>__<rest>`` mean exactly one thing, so the sweep cannot reach another agent's run
#: (``AGENTS.md`` §7).
_SCRATCH_SEPARATOR = "__"

#: A task slug or a scratch suffix: alphanumeric/dash groups joined by SINGLE underscores.  What it
#: forbids is the doubled underscore, at either end of the name and in any position.
_UNDOUBLED_RE = re.compile(r"^[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*$")


class UnsafeTestDatabaseError(ValueError):
    """Raised when a URL is not provably dedicated to tests."""


def _parse_url(database_url: str) -> URL:
    if not database_url or not database_url.strip():
        raise UnsafeTestDatabaseError("TEST_DATABASE_URL is empty.")
    try:
        return make_url(database_url)
    except Exception as exc:
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL is not a valid SQLAlchemy URL."
        ) from exc


def _is_explicit_local_test_database(database: str, *, repo_root: Path) -> bool:
    if database == ":memory:":
        return True

    normalized = database.replace("\\", "/")
    if ".." in PurePosixPath(normalized).parts:
        return False

    resolved_repo_root = repo_root.resolve()
    database_path = Path(database)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    resolved_database_path = database_path.resolve()

    if resolved_database_path == (resolved_repo_root / ".pytest_geov0.db").resolve():
        return True

    task_runs_root = (resolved_repo_root / ".local-run" / "test-runs").resolve()
    try:
        relative_path = resolved_database_path.relative_to(task_runs_root)
    except ValueError:
        return False

    return (
        len(relative_path.parts) == 2
        and _TASK_SLUG_RE.fullmatch(relative_path.parts[0]) is not None
        and relative_path.parts[1] == "test.db"
    )


def assert_safe_test_database_url(
    database_url: str,
    *,
    allow_destructive_reset: str | None,
    repo_root: Path,
    required_backend: str | None = None,
    allow_scratch_suffix: bool = False,
) -> URL:
    """Validate that a test URL cannot silently target developer data.

    SQLite is accepted only for an in-memory DB, the legacy explicitly pytest
    DB, or the canonical task-local ``.../test.db`` layout.  PostgreSQL also
    requires the destructive-reset opt-in, but the opt-in alone is never enough:
    the database name must match ``geov0_test_*``.  ``required_backend`` lets a
    test tier reject an otherwise safe URL for the wrong database backend.

    ``allow_scratch_suffix`` is for the one caller that DERIVES a name carrying
    the reserved ``__`` separator - ``tests/migrated_schema.py`` building a
    template or a clone.  It is off by default so that a tier's own
    ``TEST_DATABASE_URL`` can never be a name another task's sweep is entitled
    to drop; see ``_SCRATCH_SEPARATOR`` above for what that cost before.
    """

    if required_backend not in (None, "postgresql"):
        raise UnsafeTestDatabaseError(
            f"Unsupported required test database backend: {required_backend}."
        )

    url = _parse_url(database_url)
    backend = url.get_backend_name()
    database = url.database or ""

    if backend == "sqlite":
        if not _is_explicit_local_test_database(database, repo_root=repo_root):
            raise UnsafeTestDatabaseError(
                "SQLite test DB must be :memory: or resolve inside the repository "
                "to .pytest_geov0.db or .local-run/test-runs/<task>/test.db."
            )
        if required_backend == "postgresql":
            raise UnsafeTestDatabaseError(
                "This test tier requires the PostgreSQL database backend."
            )
        return url

    if backend != "postgresql":
        raise UnsafeTestDatabaseError(
            f"Unsupported test database backend: {backend or '<missing>'}."
        )

    if not _POSTGRES_TEST_DATABASE_RE.fullmatch(database):
        raise UnsafeTestDatabaseError(
            "PostgreSQL test DB name must match geov0_test_<task>; "
            "the reset opt-in cannot override this rule."
        )

    task_part, separator, scratch_part = database[len("geov0_test_") :].partition(
        _SCRATCH_SEPARATOR
    )
    if separator and not allow_scratch_suffix:
        raise UnsafeTestDatabaseError(
            f"PostgreSQL test DB name {database!r} contains the doubled underscore that "
            f"provisioning reserves as the separator between a tier database and its scratch "
            f"databases. It cannot be told apart from scratch database "
            f"{scratch_part!r} of the tier 'geov0_test_{task_part}', which that tier's run DROPS "
            f"when it sweeps orphans - so a task slug may not contain '__'."
        )
    if not _UNDOUBLED_RE.fullmatch(task_part) or (
        separator and not _UNDOUBLED_RE.fullmatch(scratch_part)
    ):
        raise UnsafeTestDatabaseError(
            f"PostgreSQL test DB name {database!r} is not geov0_test_<task> with single "
            f"underscores inside <task>: a leading, trailing or doubled underscore makes it "
            f"ambiguous with a scratch database of a shorter task slug."
        )

    if allow_destructive_reset != "1":
        raise UnsafeTestDatabaseError(
            "PostgreSQL test schema reset requires GEO_TEST_ALLOW_DB_RESET=1."
        )
    return url


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-backend",
        choices=("postgresql",),
        help="Reject a safe test URL unless it uses this database backend.",
    )
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        url = assert_safe_test_database_url(
            os.environ.get("TEST_DATABASE_URL", ""),
            allow_destructive_reset=os.environ.get("GEO_TEST_ALLOW_DB_RESET"),
            repo_root=repo_root,
            required_backend=args.require_backend,
        )
    except UnsafeTestDatabaseError as exc:
        print(f"Unsafe test database configuration: {exc}", file=sys.stderr)
        return 2

    print(
        "Test database guard passed "
        f"(backend={url.get_backend_name()}, database={url.database!r})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
