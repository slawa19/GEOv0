from pathlib import Path

import pytest

from scripts.validate_test_database_url import (
    UnsafeTestDatabaseError,
    assert_safe_test_database_url,
    main,
)


@pytest.mark.parametrize(
    "database_url",
    [
        # The three shapes the guard ACCEPTED until programme 017 stage 3, and one it refused.
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///./.pytest_geov0.db",
        "sqlite+aiosqlite:///./.local-run/test-runs/agent_guard/test.db",
        "sqlite+aiosqlite:///./geov0.db",
    ],
)
@pytest.mark.parametrize("required_backend", [None, "postgresql"])
def test_rejects_every_sqlite_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    required_backend: str | None,
) -> None:
    """SQLite left the test tier (017 stage 3): no path shape and no opt-in makes one safe."""

    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        UnsafeTestDatabaseError, match="Unsupported test database backend: sqlite"
    ):
        assert_safe_test_database_url(
            database_url,
            allow_destructive_reset="1",
            repo_root=tmp_path,
            required_backend=required_backend,
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


def test_accepts_safe_postgres_when_postgresql_backend_is_required(
    tmp_path: Path,
) -> None:
    parsed = assert_safe_test_database_url(
        "postgresql+asyncpg://geo:secret@localhost/geov0_test_agent_guard",
        allow_destructive_reset="1",
        repo_root=tmp_path,
        required_backend="postgresql",
    )

    assert parsed.get_backend_name() == "postgresql"


def test_rejects_unknown_required_backend_before_postgres_reset_guidance(
    tmp_path: Path,
) -> None:
    database_url = (
        "postgresql+asyncpg://geo:secret@localhost/geov0_test_agent_guard"
    )

    with pytest.raises(
        UnsafeTestDatabaseError,
        match="Unsupported required test database backend: postgres",
    ) as exc_info:
        assert_safe_test_database_url(
            database_url,
            allow_destructive_reset=None,
            repo_root=tmp_path,
            required_backend="postgres",
        )

    error_message = str(exc_info.value)
    assert "GEO_TEST_ALLOW_DB_RESET" not in error_message
    assert "secret" not in error_message


def test_cli_accepts_required_postgresql_without_printing_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://geo:secret@localhost/geov0_test_agent_guard",
    )
    monkeypatch.setenv("GEO_TEST_ALLOW_DB_RESET", "1")

    assert main(["--require-backend", "postgresql"]) == 0

    captured = capsys.readouterr()
    assert "backend=postgresql" in captured.out
    assert "secret" not in captured.out
    assert "secret" not in captured.err


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
