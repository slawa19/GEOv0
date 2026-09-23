"""The payment unit-of-work retry after a REAL `40001`: a failed rollback stops it, a good one retries.

WHY THIS MODULE EXISTS (017 stage 3, slice S2a). `PaymentEngine._run_uow_with_retry` rolls the session
back before it re-runs a unit of work, and if that rollback FAILS it re-raises the original error with
the rollback failure as its cause instead of re-running (`app/core/payments/engine.py`, the
`uow_retry_rollback_failed` branch). The branch is not about any dialect: re-running `fn()` on a
session whose rollback failed builds the second attempt on whatever the first one left behind.

Until this module the branch was proved only through a SQLite busy, in
`tests/unit/test_p015_t1525_a_busy_does_not_mask_and_does_not_promise.py`
(`test_a_failed_rollback_stops_the_retry_instead_of_re_running` and its control
`test_the_same_conflict_is_retried_when_the_rollback_succeeds`). That stand goes with the SQLite
driver in stage 3, and the branch would have lost its only test without a single red run. Here the
retryable error is the one the application actually meets: a genuine PostgreSQL serialization
failure, provoked by two SERIALIZABLE transactions, never constructed.

THE ROLLBACK FAILS FOR REAL, as in the SQLite original: the unit of work closes the driver connection
out from under the session, so the wrapper's own `session.rollback()` raises by itself ("cannot call
Transaction.rollback(): the underlying connection is closed"). A stubbed `rollback` would prove the
branch is wired; a broken connection proves a real failure reaches it.

ONE DIFFERENCE FROM THE ORIGINAL, and it is load-bearing. The unit of work runs a statement through
the session BEFORE it breaks the connection. Measured 2026-09-24: without it the asyncpg adapter has
not yet sent its lazy `BEGIN`, so there is no driver transaction, the rollback is a no-op that cannot
fail, and the wrapper retried three times - the test would have measured the stand, not the branch.
(SQLite's transaction control begins eagerly, which is why the original needed no such statement.)

The stand is a mode-B clone (`committed_database`): the conflict needs a competitor's COMMIT, which
the savepoint-wrapped `db_session` cannot produce. The clone engine runs at the application's
isolation level (SERIALIZABLE), which is what makes the conflict possible at all; the first test
checks that premise on the error itself.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.core.payments.engine import PaymentEngine


@pytest_asyncio.fixture
async def probe_factory(committed_database):
    """The clone's sessionmaker, with one committed row two transactions can fight over."""
    async with committed_database.engine.begin() as conn:
        await conn.execute(text("CREATE TABLE p017_retry_probe (id INTEGER PRIMARY KEY, v TEXT)"))
        await conn.execute(text("INSERT INTO p017_retry_probe (id, v) VALUES (1, 'seed')"))
    return committed_database.sessionmaker


async def _provoke_a_serialization_failure(factory) -> DBAPIError:
    """A REAL `40001`: the reader's snapshot predates a committed update of the row it then updates."""
    async with factory() as reader, factory() as writer:
        await reader.execute(text("SELECT v FROM p017_retry_probe WHERE id = 1"))
        await writer.execute(text("UPDATE p017_retry_probe SET v = 'writer' WHERE id = 1"))
        await writer.commit()
        with pytest.raises(DBAPIError) as conflict:
            await reader.execute(text("UPDATE p017_retry_probe SET v = 'reader' WHERE id = 1"))
        await reader.rollback()
    return conflict.value


def _retrying_engine(session) -> PaymentEngine:
    engine = PaymentEngine(session)
    engine._retry_attempts = 3
    engine._retry_base_delay_s = 0.0
    engine._retry_max_delay_s = 0.0
    return engine


async def test_a_failed_rollback_after_a_real_40001_stops_the_retry(probe_factory) -> None:
    """RED if the wrapper swallows a failed rollback and re-runs the unit of work.

    MUTATION that must turn this red: in `_run_uow_with_retry`, replace `raise exc from
    rollback_error` in the rollback-failure handler with `pass` - the unit of work then runs again.
    """
    conflict = await _provoke_a_serialization_failure(probe_factory)

    session = probe_factory()
    calls: list[int] = []
    try:
        engine = _retrying_engine(session)
        # Premises, on the error itself: a genuine serialization failure, and one the wrapper
        # retries - without both this test would pass for the wrong reason.
        assert engine._get_pgcode(conflict) == "40001", conflict
        assert engine._is_retryable_db_error(conflict, op="commit") is True

        async def _uow():
            calls.append(1)
            # A statement first, so the driver really has a transaction to roll back (see above).
            await session.execute(text("SELECT 1"))
            # Break the connection for real, then fail with the conflict. The retry wrapper's own
            # rollback is what has to fail next - and it is not stubbed.
            connection = await session.connection()
            raw = await connection.get_raw_connection()
            await raw.driver_connection.close()
            raise conflict

        with pytest.raises(DBAPIError) as raised:
            await engine._run_uow_with_retry(op="commit", fn=_uow)
    finally:
        try:
            await session.close()
        except Exception:
            pass

    assert calls == [1], (
        f"the unit of work ran {len(calls)} times after a FAILED rollback: the second attempt would "
        "build on whatever the first attempt left in a transaction nobody rolled back"
    )
    assert raised.value is conflict, "the original database error must be what the caller sees"
    cause = raised.value.__cause__
    assert isinstance(cause, SQLAlchemyError), (
        f"the failed rollback should be attached as the cause, got {cause!r}"
    )
    assert "closed" in str(cause).lower() or "no active connection" in str(cause).lower(), cause


async def test_the_same_40001_is_retried_when_the_rollback_succeeds(probe_factory) -> None:
    """The control: the retry itself still works, so the test above pins the failure path only."""
    conflict = await _provoke_a_serialization_failure(probe_factory)

    async with probe_factory() as session:
        engine = _retrying_engine(session)
        calls: list[int] = []

        async def _uow():
            calls.append(1)
            if len(calls) == 1:
                raise conflict
            return "committed"

        result = await engine._run_uow_with_retry(op="commit", fn=_uow)

    assert result == "committed"
    assert calls == [1, 1], f"expected one retry after a rollback that succeeded, got {len(calls)}"
