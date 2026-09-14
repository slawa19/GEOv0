"""Programme 015 step 5b: the backend refuses to START on a SQLite database created before migration 027.

WHY. A PAYMENT envelope carries intent encoding version 2. PostgreSQL gets the widened CHECK from migration
027; a local SQLite database gets its schema from `create_all`, which never alters an existing table, so one
created before step 5b keeps `CHECK (intent_encoding_version IN (1))` and refuses every payment at commit
(measured). The decision (review round D2): refuse at startup with the cause and the fix, never rebuild.

THE STAND. Throwaway SQLite files in `tmp_path`: one built by the current `create_all`, one whose
`debt_operations` is the same DDL with the old predicate, one whose CHECK the probe cannot recognise. The
module-level `engine` and `settings.DATABASE_URL` of `app.main` are pointed at them.

MUTATION: make `_sqlite_refuse_pre_027_debt_operations` return immediately - the old-schema and unrecognised
cases go red.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.db.models  # noqa: F401 - registers every table on Base.metadata
import app.main as main_module
from app.db.base import Base
from app.db.sqlite_transaction_control import install_sqlite_transaction_control

_NEW_PREDICATE = "intent_encoding_version IN (1, 2)"


def _fresh_ddl(tmp_path) -> str:
    path = tmp_path / "reference.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}", poolclass=NullPool)
    install_sqlite_transaction_control(engine)
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            ddl = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='debt_operations'"
            ).scalar_one()
    finally:
        engine.dispose()
    assert _NEW_PREDICATE in ddl, f"stand: the current model no longer renders the widened CHECK: {ddl}"
    return ddl


def _database_with(tmp_path, name: str, ddl: str | None, *, with_debts: bool = False) -> str:
    """A SQLite file holding `debt_operations` with the given DDL (or none), optionally a `debts` table, and
    its async URL."""

    path = tmp_path / f"{name}.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}", poolclass=NullPool)
    install_sqlite_transaction_control(engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE placeholder (id INTEGER)")
            if ddl is not None:
                connection.exec_driver_sql(ddl)
            if with_debts:
                connection.exec_driver_sql("CREATE TABLE debts (id INTEGER PRIMARY KEY, version INTEGER)")
    finally:
        engine.dispose()
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest.fixture
def point_main_at(monkeypatch):
    engines = []

    def _point(url: str) -> None:
        engine = create_async_engine(url, poolclass=NullPool)
        install_sqlite_transaction_control(engine.sync_engine)
        engines.append(engine)
        monkeypatch.setattr(main_module.settings, "DATABASE_URL", url)
        monkeypatch.setattr(main_module, "engine", engine)

    yield _point
    for engine in engines:
        engine.sync_engine.dispose()


@pytest.mark.asyncio
async def test_step5b_startup_refuses_a_sqlite_database_created_before_migration_027(
    tmp_path, point_main_at, monkeypatch
) -> None:
    """Through the real `lifespan`: the old `IN (1)` CHECK stops startup with the cause and the fix, and
    nothing after the probe runs (the simulator recovery that follows it is never reached)."""

    old_ddl = _fresh_ddl(tmp_path).replace(_NEW_PREDICATE, "intent_encoding_version IN (1)")
    point_main_at(_database_with(tmp_path, "pre_027", old_ddl))
    reached_after_probe = AsyncMock(return_value=0)
    import app.core.simulator.storage as simulator_storage

    monkeypatch.setattr(simulator_storage, "reconcile_stale_runs", reached_after_probe)

    with pytest.raises(RuntimeError) as refused:
        async with main_module.lifespan(FastAPI()):
            pytest.fail("the backend started on a database that refuses every version-2 payment")

    message = str(refused.value)
    assert "migration 027" in message and "IN (1)" in message, message
    assert ".\\scripts\\run_local.ps1 reset-db" in message, message
    reached_after_probe.assert_not_called()


@pytest.mark.asyncio
async def test_step5b_startup_refuses_an_intent_version_check_it_does_not_recognise(tmp_path, point_main_at) -> None:
    """Never assume: a `debt_operations` whose intent-version CHECK cannot be read refuses too."""

    unreadable = _fresh_ddl(tmp_path).replace(_NEW_PREDICATE, "intent_encoding_version BETWEEN 1 AND 2")
    point_main_at(_database_with(tmp_path, "unrecognised", unreadable))

    with pytest.raises(RuntimeError) as refused:
        await main_module._sqlite_refuse_pre_027_debt_operations()
    assert "does not recognise" in str(refused.value) and "reset-db" in str(refused.value), refused.value


@pytest.mark.asyncio
async def test_step5b_startup_refuses_an_application_database_from_before_the_debt_journal(
    tmp_path, point_main_at
) -> None:
    """`debts` present, `debt_operations` absent: a database older than migration 022 refuses too.

    MUTATION: let an absent `debt_operations` pass again whatever else exists - red.
    """

    point_main_at(_database_with(tmp_path, "pre_022", None, with_debts=True))
    with pytest.raises(RuntimeError) as refused:
        await main_module._sqlite_refuse_pre_027_debt_operations()
    message = str(refused.value)
    assert "before the debt journal" in message and ".\\scripts\\run_local.ps1 reset-db" in message, message


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["current_create_all", "empty_database"])
async def test_step5b_startup_passes_a_current_or_empty_sqlite_database(tmp_path, point_main_at, case) -> None:
    """A database built by the current `create_all` starts; so does a genuinely empty one - neither
    `debts` nor `debt_operations`."""

    ddl = _fresh_ddl(tmp_path) if case == "current_create_all" else None
    point_main_at(_database_with(tmp_path, case, ddl, with_debts=case == "current_create_all"))
    assert await main_module._sqlite_refuse_pre_027_debt_operations() is None


@pytest.mark.asyncio
async def test_step5b_the_startup_probe_does_not_run_on_postgresql(monkeypatch) -> None:
    """PostgreSQL has migration 027; the probe must not touch its engine at all."""

    class _Untouchable:
        def begin(self):  # pragma: no cover - reaching it is the failure
            raise AssertionError("the SQLite startup probe touched a PostgreSQL engine")

    monkeypatch.setattr(main_module.settings, "DATABASE_URL", "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0")
    monkeypatch.setattr(main_module, "engine", _Untouchable())
    assert await main_module._sqlite_refuse_pre_027_debt_operations() is None
