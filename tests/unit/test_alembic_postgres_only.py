from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_MESSAGE = (
    "Alembic migrations support PostgreSQL only. "
    "For a local SQLite database, run: python scripts/init_sqlite_db.py"
)


def _test_env(*, database_url: str) -> dict[str, str]:
    env = os.environ.copy()
    env["ENV"] = "test"
    env["DEBUG"] = "false"
    env["DATABASE_URL"] = database_url
    env.pop("ENVIRONMENT", None)
    return env


@pytest.mark.parametrize("extra_args", [[], ["--sql"]])
def test_sqlite_alembic_fails_before_executing_revisions(extra_args: list[str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "migrations/alembic.ini",
            "upgrade",
            "head",
            *extra_args,
        ],
        cwd=_ROOT,
        env=_test_env(database_url="sqlite+aiosqlite:///:memory:"),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert _MESSAGE in output
    assert "Running upgrade" not in output
    assert "CREATE EXTENSION" not in output


def test_supported_sqlite_initializer_remains_available(tmp_path: Path) -> None:
    database_path = tmp_path / "local.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"

    result = subprocess.run(
        [sys.executable, "scripts/init_sqlite_db.py"],
        cwd=_ROOT,
        env=_test_env(database_url=database_url),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"participants", "trust_lines", "transactions"} <= tables
