from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]
# Since 017 T1704 a SQLite URL is refused one step earlier than `migrations/env.py`'s own
# `_require_postgresql_migration_url`: `env.py` imports `app.config`, whose settings refuse any
# `DATABASE_URL` that is not `postgresql+asyncpg`. The refusal, the non-zero exit and "no revision
# ran" are what this test holds; which of the two checks speaks first is not.
_MESSAGE = "the application runs only on PostgreSQL through asyncpg"


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
