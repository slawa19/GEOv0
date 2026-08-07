"""Fail-closed validation for database URLs used by the test harness.

The command-line entrypoint reads environment variables so credentials are not
copied into process arguments or logs.  Importers can use
``assert_safe_test_database_url`` without opening a database connection.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePosixPath

from sqlalchemy.engine import URL, make_url


_POSTGRES_TEST_DATABASE_RE = re.compile(r"^geov0_test_[A-Za-z0-9_-]+$")
_TASK_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


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
) -> URL:
    """Validate that a test URL cannot silently target developer data.

    SQLite is accepted only for an in-memory DB, the legacy explicitly pytest
    DB, or the canonical task-local ``.../test.db`` layout.  PostgreSQL also
    requires the destructive-reset opt-in, but the opt-in alone is never enough:
    the database name must match ``geov0_test_*``.
    """

    url = _parse_url(database_url)
    backend = url.get_backend_name()
    database = url.database or ""

    if backend == "sqlite":
        if not _is_explicit_local_test_database(database, repo_root=repo_root):
            raise UnsafeTestDatabaseError(
                "SQLite test DB must be :memory: or resolve inside the repository "
                "to .pytest_geov0.db or .local-run/test-runs/<task>/test.db."
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
    if allow_destructive_reset != "1":
        raise UnsafeTestDatabaseError(
            "PostgreSQL test schema reset requires GEO_TEST_ALLOW_DB_RESET=1."
        )
    return url


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        url = assert_safe_test_database_url(
            os.environ.get("TEST_DATABASE_URL", ""),
            allow_destructive_reset=os.environ.get("GEO_TEST_ALLOW_DB_RESET"),
            repo_root=repo_root,
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
