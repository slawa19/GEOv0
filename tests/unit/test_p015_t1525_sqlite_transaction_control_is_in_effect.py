"""T1525: the SQLite transaction control is in effect on the default test engine, at runtime.

The source guard (`test_p015_t1525_every_sqlite_engine_has_transaction_control.py`) reads code; this
module asks the live connection. Both halves of `app/db/sqlite_transaction_control.py` are observable
on the driver: with the control, a read after `begin` leaves `sqlite3.Connection.in_transaction`
True, because a real `BEGIN` was sent; in the driver's legacy mode the same read leaves it False,
which is the state in which a savepoint becomes its own transaction.

The journal mode belongs here too. The WAL pragma used to run inside `engine.begin()` in the per-test
reset, under a swallow. With a real `BEGIN` SQLite refuses a journal-mode change inside a transaction,
so that placement would leave a fresh database in the rollback journal without a sound. Journal mode
is persistent in the database file, so reading it on the shared test database proves nothing about a
fresh one; the test therefore also opens a FRESH file with the conftest's own connect listener.

THE LIVE-CONNECTION CHECKS RUN ON AN ENGINE OF THIS MODULE'S OWN (programme 017, `T1702`, 2026-09-23).
They used to ask `tests.conftest.engine`, which was right only while the default tier ran on
SQLite: on a PostgreSQL tier the same tests asked asyncpg for `in_transaction` and `PRAGMA`. The
mechanism they hold in place is still in the code until stage 3 removes it, so the coverage has to
live with it rather than with the tier. `tier_shaped_sqlite` below builds a disposable SQLite file
with EXACTLY the two calls the conftest makes for its SQLite engine - `install_test_sqlite_pragmas`
and `install_sqlite_transaction_control` - and the conftest's sessionmaker options, and every
assertion is unchanged and asked of that engine. What the conftest ITSELF wires is asserted in
addition wherever the tier is SQLite (`_the_tier_engine_if_sqlite`), and by the source guard
`test_p015_t1525_every_sqlite_engine_has_transaction_control.py` whatever the tier is. On a
PostgreSQL tier there is no SQLite tier engine to wire, so that one half has no subject there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models.equivalent import Equivalent
from app.db.sqlite_transaction_control import (
    install_sqlite_transaction_control,
    sqlite_transaction_control_is_installed,
)
from tests.scratch_db import install_test_sqlite_pragmas


@pytest_asyncio.fixture
async def tier_shaped_sqlite(tmp_path: Path):
    """`(engine, sessionmaker)`: a fresh SQLite file wired exactly as the conftest wires its SQLite engine.

    The same two connect-time calls, in the same order, as `tests/conftest.py` makes for a SQLite
    `TEST_DATABASE_URL`; the same `NullPool` and driver timeout; the conftest's sessionmaker options;
    and the schema from `create_all`, as the SQLite tier has it.
    """
    url = f"sqlite+aiosqlite:///{(tmp_path / 'tier-shaped.db').as_posix()}"
    shaped = create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 30})
    install_test_sqlite_pragmas(shaped.sync_engine, url=url)
    install_sqlite_transaction_control(shaped.sync_engine)
    async with shaped.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=shaped,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield shaped, factory
    finally:
        await shaped.dispose()


def _the_tier_engine_if_sqlite():
    """`tests.conftest.engine` when the tier runs on SQLite, else None: there is nothing to wire."""
    from tests.conftest import engine

    return engine if engine.dialect.name == "sqlite" else None


async def _driver_in_transaction(session) -> bool:
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    # `driver_connection` is the aiosqlite connection; its `in_transaction` reads the sqlite3 one.
    return bool(raw.driver_connection.in_transaction)


@pytest.mark.asyncio
async def test_a_read_after_begin_is_inside_a_database_transaction(tier_shaped_sqlite) -> None:
    engine, TestingSessionLocal = tier_shaped_sqlite

    assert engine.dialect.name == "sqlite", (
        f"this module checks a SQLite engine; the stand engine is {engine.dialect.name}"
    )
    # Registration only - see the function's docstring and the limit test at the bottom of this
    # module. What proves the control is in EFFECT is the `in_transaction` assertion below.
    assert sqlite_transaction_control_is_installed(engine.sync_engine)
    tier_engine = _the_tier_engine_if_sqlite()
    if tier_engine is not None:
        assert sqlite_transaction_control_is_installed(tier_engine.sync_engine), (
            "the SQLite tier engine in tests/conftest.py no longer registers the transaction control"
        )

    async with TestingSessionLocal() as session:
        await session.begin()
        await session.execute(select(func.count()).select_from(Equivalent))
        inside = await _driver_in_transaction(session)
        await session.commit()
    assert inside is True, (
        "a read after `begin` left the SQLite driver outside any transaction: the transaction "
        "control is not in effect on the test engine, and a savepoint opened now would commit on "
        "its own RELEASE (T1525)"
    )


@pytest.mark.asyncio
async def test_one_connection_is_outside_then_inside_then_outside_a_transaction(
    tier_shaped_sqlite,
) -> None:
    """The same probe on one live connection must report False, True, False.

    Anti-vacuum for the check above: a probe that always says True would pass it on a connection
    in legacy mode. Here the False before the read and after the commit shows the probe can see the
    difference, and the True in between is the control at work.
    """
    engine, _factory = tier_shaped_sqlite

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver_isolation_level = raw.driver_connection.isolation_level
        before = bool(raw.driver_connection.in_transaction)
        await conn.execute(select(func.count()).select_from(Equivalent))
        during = bool(raw.driver_connection.in_transaction)
        await conn.commit()
        after = bool(raw.driver_connection.in_transaction)
    assert (before, during, after) == (False, True, False), (before, during, after)
    # SHAPE, not effect, and named as such. The connect half (`isolation_level = None`) could not be
    # shown to change any observable outcome on CPython 3.11: the legacy driver only begins on its
    # own in front of INSERT/UPDATE/DELETE/REPLACE and never when a transaction is open, and the
    # explicit BEGIN always comes first. Mutation M2 of T1525 removed it and every effect test stayed
    # green. It stays because it is the documented recipe's guarantee that the driver takes no
    # transaction decisions of its own (older drivers committed implicitly before DDL, `executescript`
    # still does); this line is what notices if it is dropped.
    assert driver_isolation_level is None, (
        f"the driver connection is back in legacy transaction mode (isolation_level="
        f"{driver_isolation_level!r}); `install_sqlite_transaction_control`'s connect half is gone"
    )


@pytest.mark.asyncio
async def test_the_default_test_database_is_in_wal(tier_shaped_sqlite) -> None:
    """Read through a session, after the schema was built, as the tier's `db_session` read it."""
    _engine, factory = tier_shaped_sqlite
    async with factory() as session:
        mode = (await session.execute(text("PRAGMA journal_mode"))).scalar_one()
    assert str(mode).lower() == "wal", mode
    tier_engine = _the_tier_engine_if_sqlite()
    if tier_engine is not None:
        # A read only: the tier's shared file, which is the one a journal-mode regression in the
        # per-test reset would leave behind.
        async with tier_engine.connect() as connection:
            tier_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
        assert str(tier_mode).lower() == "wal", tier_mode


@pytest.mark.asyncio
async def test_a_fresh_database_gets_wal_from_the_conftest_connect_listener(tmp_path: Path) -> None:
    """The placement check: journal mode is file-persistent, so only a fresh file can show it.

    The engine below gets exactly what the test engine gets on connect - the conftest's pragma
    listener and the transaction control - and nothing else. If WAL is not set on connect (for
    example because the pragma went back inside a transaction), this file stays in `delete`.
    """
    tier_engine = _the_tier_engine_if_sqlite()
    if tier_engine is not None:
        assert getattr(tier_engine.sync_engine, "_geo_test_sqlite_pragmas_installed", False), (
            "the test engine no longer sets its connection pragmas on connect"
        )

    fresh_url = f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    fresh = create_async_engine(fresh_url, poolclass=NullPool)
    install_test_sqlite_pragmas(fresh.sync_engine, url=fresh_url)
    install_sqlite_transaction_control(fresh.sync_engine)
    try:
        async with fresh.begin() as conn:
            await conn.execute(text("CREATE TABLE probe (x INTEGER)"))
        async with fresh.connect() as conn:
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
    finally:
        await fresh.dispose()
    assert str(mode).lower() == "wal", (
        f"a fresh database opened by the test engine's connect listener is in {mode!r}, not WAL"
    )


@pytest.mark.asyncio
async def test_installing_the_control_late_does_not_repair_a_connection_that_already_read(
    tmp_path: Path,
) -> None:
    """`sqlite_transaction_control_is_installed` reports REGISTRATION, never live state.

    The function was called `has_sqlite_transaction_control`, which read as a promise about the
    engine's behaviour. It is not one, and this is the counter-proof: install the control AFTER a
    connection has already read - so the driver is in legacy mode and no `BEGIN` was ever sent -
    and the savepoint opened next is still its own transaction. The root rollback leaves the row
    behind, exactly the T1525 defect, while the function returns True throughout.

    The listeners are not retroactive: `connect` fires only on the NEXT connection and `begin` only
    on the next SQLAlchemy `begin`. This is why the module's other tests read
    `sqlite3.Connection.in_transaction` on the live connection instead of trusting this function.
    """
    url = f"sqlite+aiosqlite:///{(tmp_path / 'late.db').as_posix()}"
    late = create_async_engine(url, poolclass=NullPool)
    install_test_sqlite_pragmas(late.sync_engine, url=url)
    try:
        async with late.begin() as conn:
            await conn.execute(text("CREATE TABLE probe (x INTEGER)"))

        connection = await late.connect()
        try:
            # This read opens SQLAlchemy's transaction while the control is NOT yet installed, so
            # the driver stays in legacy mode and sends no BEGIN.
            await connection.execute(text("SELECT count(*) FROM probe"))

            install_sqlite_transaction_control(late.sync_engine)
            installed_now = sqlite_transaction_control_is_installed(late.sync_engine)

            await connection.exec_driver_sql("SAVEPOINT late_sp")
            await connection.exec_driver_sql("INSERT INTO probe VALUES (1)")
            await connection.exec_driver_sql("RELEASE late_sp")
            await connection.rollback()
        finally:
            await connection.close()

        async with late.connect() as check:
            survived = (await check.execute(text("SELECT count(*) FROM probe"))).scalar_one()
    finally:
        await late.dispose()

    assert installed_now is True, "the control was installed; the point of the test is that it is"
    assert survived == 1, (
        "the row did NOT survive the root rollback, so this stand no longer reproduces the "
        "retroactivity limit; if the control became retroactive, say so at the function instead"
    )
