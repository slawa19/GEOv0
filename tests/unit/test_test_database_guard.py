from pathlib import Path

import pytest

from scripts.validate_test_database_url import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
)


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///./.pytest_geov0.db",
        "sqlite+aiosqlite:///./.local-run/test-runs/agent_guard/test.db",
    ],
)
def test_accepts_explicit_local_test_databases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    parsed = assert_safe_test_database_url(
        database_url,
        allow_destructive_reset=None,
        repo_root=tmp_path,
    )

    assert parsed.get_backend_name() == "sqlite"


def test_accepts_absolute_task_database_inside_repo(tmp_path: Path) -> None:
    database_url = _sqlite_url(
        tmp_path / ".local-run" / "test-runs" / "ci-required" / "test.db"
    )

    parsed = assert_safe_test_database_url(
        database_url,
        allow_destructive_reset=None,
        repo_root=tmp_path,
    )

    assert parsed.get_backend_name() == "sqlite"


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///./geov0.db",
        "sqlite+aiosqlite:///./.local-run/test-runs/agent_guard/developer.db",
        "sqlite+aiosqlite:///../repo/.pytest_geov0.db",
    ],
)
def test_rejects_non_test_and_parent_relative_sqlite_databases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)

    with pytest.raises(UnsafeTestDatabaseError, match="SQLite test DB"):
        assert_safe_test_database_url(
            database_url,
            allow_destructive_reset=None,
            repo_root=repo_root,
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        Path(".pytest_geov0.db"),
        Path(".local-run/test-runs/agent_guard/test.db"),
    ],
)
def test_rejects_test_shaped_absolute_database_outside_repo(
    tmp_path: Path, relative_path: Path
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    external_database = tmp_path / "shared" / relative_path

    with pytest.raises(UnsafeTestDatabaseError, match="SQLite test DB"):
        assert_safe_test_database_url(
            _sqlite_url(external_database),
            allow_destructive_reset=None,
            repo_root=repo_root,
        )


def test_rejects_task_database_symlink_that_escapes_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    task_root = repo_root / ".local-run" / "test-runs" / "agent_guard"
    task_root.mkdir(parents=True)
    external_database = tmp_path / "shared" / "test.db"
    external_database.parent.mkdir()
    external_database.touch()
    database_link = task_root / "test.db"
    try:
        database_link.symlink_to(external_database)
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable in this environment: {exc}")

    with pytest.raises(UnsafeTestDatabaseError, match="SQLite test DB"):
        assert_safe_test_database_url(
            _sqlite_url(database_link),
            allow_destructive_reset=None,
            repo_root=repo_root,
        )


def test_accepts_explicit_postgres_test_database_with_reset_opt_in(
    tmp_path: Path,
) -> None:
    parsed = assert_safe_test_database_url(
        "postgresql+asyncpg://geo:secret@localhost/geov0_test_agent_guard",
        allow_destructive_reset="1",
        repo_root=tmp_path,
    )

    assert parsed.database == "geov0_test_agent_guard"


def test_rejects_postgres_test_database_without_reset_opt_in(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        UnsafeTestDatabaseError, match="requires GEO_TEST_ALLOW_DB_RESET=1"
    ):
        assert_safe_test_database_url(
            "postgresql+asyncpg://geo:secret@localhost/geov0_test_agent_guard",
            allow_destructive_reset=None,
            repo_root=tmp_path,
        )


@pytest.mark.parametrize("database_name", ["geov0", "geov0_test", "production"])
def test_reset_opt_in_cannot_override_unsafe_postgres_name(
    tmp_path: Path, database_name: str
) -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="must match geov0_test_<task>"):
        assert_safe_test_database_url(
            f"postgresql+asyncpg://geo:secret@localhost/{database_name}",
            allow_destructive_reset="1",
            repo_root=tmp_path,
        )


def test_rejects_unsupported_backend_even_with_test_like_name(tmp_path: Path) -> None:
    with pytest.raises(
        UnsafeTestDatabaseError, match="Unsupported test database backend"
    ):
        assert_safe_test_database_url(
            "mysql+pymysql://geo:secret@localhost/geov0_test_agent_guard",
            allow_destructive_reset="1",
            repo_root=tmp_path,
        )
