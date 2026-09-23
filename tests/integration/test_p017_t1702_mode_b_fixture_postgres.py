"""T1702 (stage 2a): the tier creates its own database, and mode B is a real disposable database.

WHAT THIS MODULE MEASURES, each half against its own counter-check (`AGENTS.md` §9):

1. THE TIER PROVIDES ITS OWN DATABASE. Measured 2026-09-23 before this slice: the default tier
   pointed at a PostgreSQL database that did not exist produced 705 errors with one cause,
   `InvalidCatalogNameError`. The reproducer below runs a child pytest against a database that
   certainly does not exist and asserts it passes and leaves the database behind; on the tree before
   `tests/conftest.py::init_db` created it, the same child errors on its first connection.
2. MODE B GIVES WHAT MODE A CANNOT, and the difference is measured, not argued. Three properties,
   each asserted in both modes: a commit is visible to ANOTHER connection (mode A: invisible, because
   the savepoint's "commit" never leaves the outer transaction); clearing accepts the session
   (mode A: refused by `app/core/clearing/service.py:1530-1539` before it touches data); and nothing a
   mode-B test commits survives into the next clone.

The counter-checks in mode A are what keep the mode-B assertions from being vacuous: if the three
properties held in mode A as well, mode B would be proving nothing about itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.exceptions import GeoException
from tests.conftest import TEST_DATABASE_URL, _committed_database_context, engine
from tests.debt_setup import debt_fixture_setup
from tests.migrated_schema import (
    REPO_ROOT,
    MigratedSchemaError,
    database_exists,
    drop_database,
    ensure_tier_database,
    maintenance_connection,
)

pytestmark = pytest.mark.postgres

_CLEARING_REFUSAL = "PostgreSQL clearing requires an engine-bound AsyncSession"


def _url_with_database(name: str) -> str:
    return make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)


async def _exists_by_any_name(name: str) -> bool:
    """`database_exists` refuses a name outside `geov0_test_*`; the refusal tests need to look anyway."""

    connection = await maintenance_connection(TEST_DATABASE_URL)
    try:
        return bool(await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name))
    finally:
        await connection.close()


async def _drop(name: str) -> None:
    connection = await maintenance_connection(TEST_DATABASE_URL)
    try:
        await drop_database(connection, name)
    finally:
        await connection.close()


# =====================================================================================================
# 1. The tier creates its own database
# =====================================================================================================


async def test_ensure_tier_database_creates_a_missing_database_once():
    name = f"{make_url(TEST_DATABASE_URL).database}_t1702new"
    url = _url_with_database(name)
    await _drop(name)
    try:
        assert not await database_exists(TEST_DATABASE_URL, name)
        assert await ensure_tier_database(url) is True
        assert await database_exists(TEST_DATABASE_URL, name)
        # Idempotent: an existing database is reported, not recreated and not touched.
        assert await ensure_tier_database(url) is False
    finally:
        await _drop(name)


@pytest.mark.parametrize(
    "database, reason",
    [
        ("geov0_dev_t1702", "geov0_test_"),
        ("geov0_test_t1702__x", "doubled underscore"),
    ],
)
async def test_ensure_tier_database_refuses_what_the_guard_would_not_let_it_reset(database, reason):
    url = _url_with_database(database)
    with pytest.raises(MigratedSchemaError, match=reason):
        await ensure_tier_database(url)
    assert not await _exists_by_any_name(database)


async def test_ensure_tier_database_refuses_without_the_reset_opt_in(monkeypatch):
    name = f"{make_url(TEST_DATABASE_URL).database}_t1702optin"
    monkeypatch.setenv("GEO_TEST_ALLOW_DB_RESET", "0")
    with pytest.raises(MigratedSchemaError, match="GEO_TEST_ALLOW_DB_RESET"):
        await ensure_tier_database(_url_with_database(name))
    assert not await database_exists(TEST_DATABASE_URL, name)


def test_a_tier_pointed_at_a_missing_database_creates_it_and_runs():
    """THE REPRODUCER: a child pytest on a database that does not exist passes, and leaves it built.

    Before `init_db` created the database this child failed on its first connection with
    `InvalidCatalogNameError`, which is the 705-error run of 2026-09-23 in one test.
    """

    import asyncio

    name = f"{make_url(TEST_DATABASE_URL).database}_t1702child"
    url = _url_with_database(name)

    def _run(coro):
        return asyncio.run(coro)

    # A thread-free `asyncio.run` is safe here: this test is synchronous, so no loop is running.
    _run(_drop(name))
    try:
        assert not _run(database_exists(TEST_DATABASE_URL, name))
        environment = dict(
            os.environ,
            TEST_DATABASE_URL=url,
            GEO_TEST_ALLOW_DB_RESET="1",
            GEO_TEST_USE_MIGRATED_SCHEMA="1",
        )
        environment.pop("PYTEST_ADDOPTS", None)
        environment.pop("GEO_TEST_FIXTURE_MODE", None)
        completed = subprocess.run(  # noqa: S603 - fixed argv
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "tests/unit/test_debt_symmetry.py",
            ],
            cwd=str(REPO_ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output[-4000:]
        assert "does not exist" not in output
        assert " passed" in output, output[-2000:]
        assert _run(database_exists(TEST_DATABASE_URL, name))
    finally:
        _run(_drop(name))


# =====================================================================================================
# 2. Mode B against mode A
# =====================================================================================================


async def _insert_marker(session, marker: str) -> None:
    session.add(
        Participant(
            pid="T1702" + marker,
            display_name="t1702",
            public_key="pk-t1702-" + marker,
            type="person",
            status="active",
            profile={},
        )
    )
    await session.commit()


async def _visible_from_another_connection(url_engine, marker: str) -> bool:
    async with url_engine.connect() as connection:
        found = (
            await connection.execute(
                text("SELECT count(*) FROM participants WHERE pid = :pid"), {"pid": "T1702" + marker}
            )
        ).scalar_one()
    return found == 1


async def test_mode_b_commit_is_visible_to_another_connection(committed_session):
    marker = uuid.uuid4().hex[:12]
    await _insert_marker(committed_session, marker)
    database = committed_session.info["geo_committed_database"]
    # A NEW connection (the engine is NullPool): the session that wrote is not asked.
    assert await _visible_from_another_connection(database.engine, marker)


async def test_mode_a_commit_is_not_visible_to_another_connection(db_session):
    """COUNTER-CHECK: the same write in mode A never leaves the outer transaction."""

    marker = uuid.uuid4().hex[:12]
    await _insert_marker(db_session, marker)
    assert not await _visible_from_another_connection(engine, marker)


async def test_mode_b_clone_is_dropped_and_its_commits_do_not_reach_the_next_clone():
    marker = uuid.uuid4().hex[:12]
    async with _committed_database_context() as first:
        first_name = make_url(first.url).database
        async with first.sessionmaker() as session:
            await _insert_marker(session, marker)
        assert await _visible_from_another_connection(first.engine, marker)
    assert not await database_exists(TEST_DATABASE_URL, first_name)

    async with _committed_database_context() as second:
        assert not await _visible_from_another_connection(second.engine, marker)


async def _seed_triangle(session) -> str:
    """A->B->C->A, 10 each, consented by every creditor: the smallest cycle clearing will execute."""

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("T" + nonce[:15]).upper(),
        symbol="T",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    a, b, c = (
        Participant(
            pid=letter + nonce,
            display_name=letter,
            public_key=f"pk{letter}-{nonce}",
            type="person",
            status="active",
            profile={},
        )
        for letter in "ABC"
    )
    session.add_all([eq, a, b, c])
    await session.flush()
    async with debt_fixture_setup(session, label="setup"):
        session.add_all(
            [
                Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
                Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
                Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
            ]
        )
        session.add_all(
            [
                TrustLine(
                    from_participant_id=creditor.id,
                    to_participant_id=debtor.id,
                    equivalent_id=eq.id,
                    limit=Decimal("100"),
                    status="active",
                    policy={"auto_clearing": True},
                )
                for creditor, debtor in ((b, a), (c, b), (a, c))
            ]
        )
    await session.commit()
    return eq.code


async def test_mode_b_session_is_accepted_by_clearing(committed_session):
    code = await _seed_triangle(committed_session)
    service = ClearingService(committed_session)
    cycles = await service.find_cycles(code, max_depth=3)
    assert cycles, "the seeded triangle was not detected; the clearing half below would be vacuous"
    cleared = await service.execute_clearing_with_amount(cycles[0])
    assert cleared == Decimal("10")


async def test_mode_a_session_is_refused_by_clearing(db_session):
    """COUNTER-CHECK: the same cycle on the savepoint session is refused before any data is read."""

    code = await _seed_triangle(db_session)
    service = ClearingService(db_session)
    cycles = await service.find_cycles(code, max_depth=3)
    assert cycles
    with pytest.raises(GeoException) as raised:
        await service.execute_clearing_with_amount(cycles[0])
    assert _CLEARING_REFUSAL in str(raised.value.__cause__)
