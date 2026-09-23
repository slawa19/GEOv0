"""The test tier refuses to start on anything but PostgreSQL (017 stage 2c, T1702).

WHAT THIS REPLACES, AND WHAT IT KEEPS. Until stage 2c this module was
`test_postgres_marker_fail_closed.py`: it proved that a direct pytest run SELECTING `postgres`-marked
tests on a SQLite `TEST_DATABASE_URL` ended in a usage error instead of a green skip. The marker is
gone - every database test is a PostgreSQL test now - so "the selection needs PostgreSQL" became "the
tier needs PostgreSQL", and the refusal moved from collection to the moment `tests/conftest.py` builds
the tier's engine. What survives unchanged is the point of the old test: a run on the wrong backend
must END, loudly, with exit 4 and a reason - never pass, never skip.

The three cases are the three ways a URL can arrive: SQLite (the old default, still accepted by the
URL guard for the SQLite stands of `tests/scratch_db.py`, which is why the TIER has to refuse it
itself), no URL at all, and a PostgreSQL URL - the control, collected without a server, so the refusal
is shown to be about the backend and not about everything.

It runs pytest in a subprocess and only `--collect-only`: no database is opened in any case.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_ROOT = Path(__file__).resolve().parents[2]
_SELECTOR = "tests/integration/test_payment_engine_uow_retry_postgres.py"
_HOW_TO = "docs/ru/backend/postgres-local-portable.md"


def _collect(*, database_url: str | None, allow_reset: bool) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ENV"] = "test"
    env["DEBUG"] = "false"
    env.pop("TEST_DATABASE_URL", None)
    if database_url is not None:
        env["TEST_DATABASE_URL"] = database_url
    env.pop("GEO_TEST_USE_MIGRATED_SCHEMA", None)
    env.pop("PYTEST_ADDOPTS", None)
    if allow_reset:
        env["GEO_TEST_ALLOW_DB_RESET"] = "1"
    else:
        env.pop("GEO_TEST_ALLOW_DB_RESET", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", _SELECTOR],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_sqlite_url_ends_the_tier_before_a_single_test_is_collected() -> None:
    result = _collect(
        database_url="sqlite+aiosqlite:///./.local-run/test-runs/tier-refusal-probe/test.db",
        allow_reset=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "The test tier runs only on PostgreSQL" in output, output
    assert "'sqlite'" in output, output
    assert _HOW_TO in output, output
    assert "tests collected" not in output, output


def test_no_url_at_all_ends_the_tier_and_says_what_to_set() -> None:
    result = _collect(database_url=None, allow_reset=False)

    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "TEST_DATABASE_URL is not set" in output, output
    assert "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_" in output, output
    assert _HOW_TO in output, output
    assert "tests collected" not in output, output


def test_control_a_postgres_url_is_collected_without_opening_the_database() -> None:
    # Port 1: nothing listens there, and collection must not need it to.
    result = _collect(
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/geov0_test_wave5_t605_collect",
        allow_reset=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "2 tests collected" in output, output


# ---------------------------------------------------------------------------------------------------
# The runner's derived default, and why its opt-in cannot leak to a URL someone else chose.
# ---------------------------------------------------------------------------------------------------

_RUNNER = _ROOT / "scripts" / "verify_local.ps1"


def _runner_text() -> str:
    return _RUNNER.read_text(encoding="utf-8")


def test_the_runner_sets_the_reset_opt_in_only_for_the_name_it_derived() -> None:
    """FORM, not truth: this reads the script. The behaviour is the next test.

    The destructive-reset opt-in may be set by the runner in exactly one place: the branch that has
    just put the DERIVED URL into an EMPTY `TEST_DATABASE_URL`. Anywhere else it would reach a URL the
    operator supplied, which must keep requiring the operator's own opt-in.
    """

    import re

    runner = _runner_text()
    code = "\n".join(line for line in runner.splitlines() if not line.lstrip().startswith("#"))
    assignments = re.findall(r"\$env:GEO_TEST_ALLOW_DB_RESET\s*=\s*([^\n]+)", code)
    assert assignments == ["'1'", "$previousAllowDbReset"], assignments

    derived = re.search(
        r"if \(-not \$env:TEST_DATABASE_URL\) \{\s*"
        r"\$env:TEST_DATABASE_URL = \$derivedTestDb\s*"
        r"\$env:GEO_TEST_ALLOW_DB_RESET = '1'",
        code,
    )
    assert derived, "the opt-in is no longer set inside, and only inside, the derived-URL branch"
    assert (
        '$derivedTestDb = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_$TaskSlug"' in code
    )
    assert "[ValidatePattern('^[A-Za-z0-9_-]+$')]\n    [string]$TaskSlug" in runner


def test_every_slug_the_runner_admits_derives_a_name_the_guard_owns_or_refuses() -> None:
    """The derived name is `geov0_test_` + a slug of [A-Za-z0-9_-]: by construction a test database.

    The only slugs the guard still refuses are the ones with the reserved `__` (or a leading or
    trailing underscore), and it refuses them WITH the opt-in set - the runner's opt-in cannot widen
    what the guard accepts.
    """

    import pytest

    from scripts.validate_test_database_url import (
        UnsafeTestDatabaseError,
        assert_safe_test_database_url,
    )

    def derived(slug: str) -> str:
        return f"postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_{slug}"

    for slug in ("verify-local", "p017s2c", "ci-required-backend", "a", "A-1_b"):
        url = assert_safe_test_database_url(
            derived(slug), allow_destructive_reset="1", repo_root=_ROOT, required_backend="postgresql"
        )
        assert url.database == f"geov0_test_{slug}"
        assert url.host == "127.0.0.1"
    for slug in ("a__b", "_a", "a_"):
        with pytest.raises(UnsafeTestDatabaseError):
            assert_safe_test_database_url(
                derived(slug), allow_destructive_reset="1", repo_root=_ROOT, required_backend="postgresql"
            )


def test_a_url_handed_to_the_runner_still_needs_its_own_opt_in() -> None:
    """BEHAVIOUR: the runner, given an explicit PostgreSQL URL and no opt-in, refuses at its guard step.

    Nothing is collected and no database is opened: the guard runs before pytest.
    """

    import shutil

    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "PowerShell is required to run scripts/verify_local.ps1 (CI's backend job uses pwsh)"
    env = os.environ.copy()
    env["TEST_DATABASE_URL"] = "postgresql+asyncpg://geo:geo@127.0.0.1:1/geov0_test_opt_in_probe"
    env.pop("GEO_TEST_ALLOW_DB_RESET", None)
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            shell, "-NoProfile", "-NonInteractive", "-File", str(_RUNNER),
            "-Python", sys.executable, "-TaskSlug", "opt-in-probe", "-BackendOnly",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "GEO_TEST_ALLOW_DB_RESET=1" in output, output
    assert "Backend tests (pytest)" not in output, output
