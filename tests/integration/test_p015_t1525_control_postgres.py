"""Programme 015, T1525 control: the SQLite savepoint scenarios are already correct on PostgreSQL.

`tests/unit/test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py` is red on the default SQLite
tier because a savepoint opened before the first write is a transaction of its own there. On
PostgreSQL every statement, a read included, runs inside the transaction the driver opened, so a
savepoint is always nested and a root rollback undoes what was released inside it. These are the
same scenarios and the same assertions, imported rather than copied so the two tiers cannot drift
apart; they must be GREEN here before and after the T1525 fix. A red result here would mean the
defect is not SQLite-only, and the fix would have to be designed differently.

WHY THE STAND IS BUILT THIS WAY:

* Its own engine with `isolation_level="SERIALIZABLE"`, the application's isolation
  (`app/db/session.py`); the shared test engine runs READ COMMITTED.
* A real pool, not NullPool and not the savepoint-wrapped `db_session`. Under `db_session` an outer
  transaction survives every commit and rollback of the code under test, so "nothing was stored"
  would be true by construction of the fixture rather than by the application's rollback.
* Every verdict is read through a new session on a pooled connection after the working session is
  closed, and each scenario asserts its mechanism first (the flow wrote the debt inside the
  committing transaction; the staged payments were COMMITTED; the tick really took its rollback
  branch) - an absence is only evidence when the presence was observed first.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.unit.test_p015_t1525_sqlite_savepoint_is_not_a_transaction import (
    _assert_aborted_payment_left_no_debt,
    _assert_rolled_back_tick_left_no_payment,
    _cleanup,
    _scenario_engine_commit_violates_after_flows,
    _scenario_executor_then_tick_rollback,
    _scenario_real_tick_fails_after_payments,
    _scenario_service_payment_violates_after_flows,
    _seed_world,
)

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def serializable_factory():
    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    await _ensure_schema_initialized()
    eng = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        isolation_level="SERIALIZABLE",
    )
    assert eng.dialect.name == "postgresql", eng.dialect.name
    factory = async_sessionmaker(
        bind=eng,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        yield factory
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_postgres_an_aborted_payment_commit_leaves_debts_unchanged(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_engine_commit_violates_after_flows(
            serializable_factory, world, monkeypatch
        )
        _assert_aborted_payment_left_no_debt(outcome, world)
    finally:
        await _cleanup(serializable_factory, world)


@pytest.mark.asyncio
async def test_postgres_an_aborted_service_payment_leaves_debts_unchanged(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_service_payment_violates_after_flows(
            serializable_factory, world, monkeypatch
        )
        _assert_aborted_payment_left_no_debt(outcome, world)
    finally:
        await _cleanup(serializable_factory, world)


@pytest.mark.asyncio
async def test_postgres_a_rolled_back_tick_leaves_no_payment_from_the_executor(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_executor_then_tick_rollback(
            serializable_factory, world, monkeypatch
        )
        _assert_rolled_back_tick_left_no_payment(outcome, via_tick=False)
    finally:
        await _cleanup(serializable_factory, world)


@pytest.mark.asyncio
async def test_postgres_a_real_tick_failing_after_payments_leaves_no_payment(
    serializable_factory, monkeypatch
) -> None:
    world = await _seed_world(serializable_factory)
    try:
        outcome = await _scenario_real_tick_fails_after_payments(
            serializable_factory, world, monkeypatch
        )
        _assert_rolled_back_tick_left_no_payment(outcome, via_tick=True)
    finally:
        await _cleanup(serializable_factory, world)
