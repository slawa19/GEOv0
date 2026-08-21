from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_ROOT = Path(__file__).resolve().parents[2]
_POSTGRES_SELECTOR = "tests/integration/test_payment_engine_uow_retry_postgres.py"


def _collect_postgres_tier(*, database_url: str, allow_reset: bool) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ENV"] = "test"
    env["DEBUG"] = "false"
    env["TEST_DATABASE_URL"] = database_url
    env.pop("GEO_TEST_USE_MIGRATED_SCHEMA", None)
    if allow_reset:
        env["GEO_TEST_ALLOW_DB_RESET"] = "1"
    else:
        env.pop("GEO_TEST_ALLOW_DB_RESET", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "postgres",
            _POSTGRES_SELECTOR,
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_direct_postgres_marker_fails_closed_on_sqlite() -> None:
    result = _collect_postgres_tier(
        database_url="sqlite+aiosqlite:///:memory:",
        allow_reset=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "PostgreSQL-marked tests were selected" in output
    assert "TEST_DATABASE_URL does not use PostgreSQL" in output


def test_direct_postgres_marker_accepts_safe_postgres_backend_for_collection() -> None:
    result = _collect_postgres_tier(
        database_url=(
            "postgresql+asyncpg://unused:unused@127.0.0.1:1/"
            "geov0_test_wave5_t605_collect"
        ),
        allow_reset=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "2 tests collected" in output
