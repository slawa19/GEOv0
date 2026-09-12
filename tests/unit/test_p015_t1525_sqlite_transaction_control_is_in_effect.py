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
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models.equivalent import Equivalent
from app.db.sqlite_transaction_control import (
    has_sqlite_transaction_control,
    install_sqlite_transaction_control,
)


async def _driver_in_transaction(session) -> bool:
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    # `driver_connection` is the aiosqlite connection; its `in_transaction` reads the sqlite3 one.
    return bool(raw.driver_connection.in_transaction)


@pytest.mark.asyncio
async def test_a_read_after_begin_is_inside_a_database_transaction(db_session) -> None:
    from tests.conftest import TestingSessionLocal, engine

    assert engine.dialect.name == "sqlite", (
        f"this module checks the default SQLite tier; the test engine is {engine.dialect.name}"
    )
    assert has_sqlite_transaction_control(engine.sync_engine)

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
async def test_one_connection_is_outside_then_inside_then_outside_a_transaction(db_session) -> None:
    """The same probe on one live connection must report False, True, False.

    Anti-vacuum for the check above: a probe that always says True would pass it on a connection
    in legacy mode. Here the False before the read and after the commit shows the probe can see the
    difference, and the True in between is the control at work.
    """
    from tests.conftest import engine

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
async def test_the_default_test_database_is_in_wal(db_session) -> None:
    mode = (await db_session.execute(text("PRAGMA journal_mode"))).scalar_one()
    assert str(mode).lower() == "wal", mode


@pytest.mark.asyncio
async def test_a_fresh_database_gets_wal_from_the_conftest_connect_listener(tmp_path: Path) -> None:
    """The placement check: journal mode is file-persistent, so only a fresh file can show it.

    The engine below gets exactly what the test engine gets on connect - the conftest's pragma
    listener and the transaction control - and nothing else. If WAL is not set on connect (for
    example because the pragma went back inside a transaction), this file stays in `delete`.
    """
    import tests.conftest as conftest

    assert event.contains(conftest.engine.sync_engine, "connect", conftest._sqlite_connection_pragmas), (
        "the test engine no longer sets its connection pragmas on connect"
    )

    fresh = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}", poolclass=NullPool
    )
    event.listen(fresh.sync_engine, "connect", conftest._sqlite_connection_pragmas)
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
