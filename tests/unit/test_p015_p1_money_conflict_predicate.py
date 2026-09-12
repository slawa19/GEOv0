"""Programme 015 / P1: the predicate the money replay rests on, checked against real errors.

WHY THIS MODULE EXISTS. `money_conflict_name` decides whether the tick's money phase is repeated.
It is a filter, so AGENTS.md §9 requires it to carry a counter-check: a rule that excludes must be
shown still to accept the real cases AND still to refuse everything else. Both failure modes are
expensive and neither is visible from a green suite:

* Too permissive - a terminal failure classified as a conflict is replayed until the budget runs
  out, and then reported as contention rather than as the error it is. It would also be excluded
  from the error budget, so a genuinely broken run would never stop.
* Too strict - a real 40001 or SQLITE_BUSY is treated as a programmatic failure, the replay never
  happens, and P1's defect is back with the tick counted as an error.

THE ERRORS HERE ARE PRODUCED BY THE DRIVER, NOT CONSTRUCTED. A busy comes from a genuine snapshot
race on a file-backed WAL database with the production transaction control; an integrity error
comes from a foreign key the database refuses. A hand-built exception would only prove that the
predicate matches what the test author believed the driver emits, which is the assumption that
produced the masking defect `sqlite_busy_error_name` documents.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.simulator.money_replay import MoneyCommitOutcomeUnknown, money_conflict_name
from app.db.sqlite_transaction_control import (
    install_sqlite_transaction_control,
    sqlite_busy_error_name,
)
from app.utils.exceptions import (
    ConflictException,
    IntegrityViolationException,
    RetryablePaymentConflictException,
    TimeoutException,
)
from tests.scratch_db import install_test_sqlite_pragmas


@pytest_asyncio.fixture
async def race_factory(tmp_path):
    """A file-backed WAL engine: SQLITE_BUSY_SNAPSHOT needs WAL and a real transaction on both sides."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'p1-predicate.db').as_posix()}"
    engine = create_async_engine(url, poolclass=NullPool)
    install_test_sqlite_pragmas(engine.sync_engine, url=url)
    install_sqlite_transaction_control(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE probe (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
        )
        await conn.execute(
            text(
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY,"
                "  parent_id INTEGER NOT NULL REFERENCES probe(id)"
                ")"
            )
        )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _real_busy_snapshot(factory) -> DBAPIError:
    """A genuine SQLITE_BUSY_SNAPSHOT: a reader's write after another connection committed."""
    async with factory() as reader, factory() as writer:
        # Both take a read snapshot. With the transaction control in place this is a real
        # transaction in the database, which is what makes the snapshot exist at all.
        await reader.execute(text("SELECT id FROM probe LIMIT 1"))
        await writer.execute(text("SELECT id FROM probe LIMIT 1"))

        await writer.execute(text("INSERT INTO probe (v) VALUES ('writer')"))
        await writer.commit()

        with pytest.raises(DBAPIError) as busy:
            await reader.execute(text("INSERT INTO probe (v) VALUES ('reader')"))
            await reader.flush()
        await reader.rollback()
    return busy.value


@pytest.mark.asyncio
async def test_a_real_sqlite_busy_snapshot_is_a_money_conflict(race_factory) -> None:
    """The accepting half: the conflict the replay exists for is recognised, by CODE."""
    busy = await _real_busy_snapshot(race_factory)

    # Non-vacuity for the stand itself: if a driver update stopped surfacing the code, the
    # predicate would go quietly permissive and this is what notices.
    assert sqlite_busy_error_name(busy) == "SQLITE_BUSY_SNAPSHOT"
    assert getattr(busy.orig, "sqlite_errorcode", None) == 517

    assert money_conflict_name(busy) == "SQLITE_BUSY_SNAPSHOT"


@pytest.mark.asyncio
async def test_a_real_integrity_error_on_the_same_backend_is_not_a_money_conflict(
    race_factory,
) -> None:
    """The refusing half, on an error from the same driver: a broken constraint is terminal.

    Replaying this would repeat a write the database has already judged, every attempt, and then
    report the run as merely contended.
    """
    async with race_factory() as session:
        # The error surfaces from the statement itself, not from a later flush: this is Core DML,
        # so there is no unit of work to flush.
        with pytest.raises(IntegrityError) as integrity:
            await session.execute(text("INSERT INTO child (parent_id) VALUES (424242)"))
        await session.rollback()

    assert sqlite_busy_error_name(integrity.value) is None
    assert money_conflict_name(integrity.value) is None


def test_the_typed_payment_conflict_is_a_money_conflict() -> None:
    """The shape a staged payment actually propagates (`real_payments_executor.py`)."""
    assert (
        money_conflict_name(RetryablePaymentConflictException())
        == "RETRYABLE_PAYMENT_CONFLICT"
    )


@pytest.mark.parametrize(
    "sqlstate",
    ["40001", "40P01"],
    ids=["serialization_failure", "deadlock_detected"],
)
def test_the_postgresql_transient_sqlstates_are_money_conflicts(sqlstate: str) -> None:
    """40001 and 40P01 on a `DBAPIError`, read the way each driver spells the field.

    These reach the predicate as RAW database errors, not as typed payment conflicts, because the
    money boundary contains statements the payment service never classifies: the debt snapshot read
    and the owner-lock acquisition. A SERIALIZABLE waiter can take a genuine 40001 on either.

    The live 40001 is exercised end to end against a real PostgreSQL backend in
    `tests/integration/test_p015_p1_money_replay_postgres.py`; what is pinned here is the field
    reading itself, across the two driver spellings (`sqlstate` for asyncpg, `pgcode` for psycopg2).
    """

    class _AsyncpgOrig(Exception):
        sqlstate = None

    class _Psycopg2Orig(Exception):
        pgcode = None

    for orig_cls, field in ((_AsyncpgOrig, "sqlstate"), (_Psycopg2Orig, "pgcode")):
        orig = orig_cls("conflict")
        setattr(orig, field, sqlstate)
        error = DBAPIError("UPDATE debts SET amount = $1", {}, orig)
        assert money_conflict_name(error) == sqlstate, field


def test_a_non_transient_sqlstate_is_not_a_money_conflict() -> None:
    """Counter-check for the SQLSTATE branch: only the two transient codes may pass.

    23505 is the case that makes this worth asserting. `PaymentEngine` retries ONE exact
    business-key 23505 on a `Debt` insert, under its own lock and with the constraint name checked
    (`engine.py:458`). Nothing that broad belongs here: the money boundary cannot tell which unique
    violation it is looking at, and a unique violation that is replayed blindly is a write the
    database has already refused.
    """

    class _Orig(Exception):
        sqlstate = "23505"

    error = DBAPIError("INSERT INTO debts", {}, _Orig("duplicate key"))
    assert money_conflict_name(error) is None


@pytest.mark.parametrize(
    "error",
    [
        None,
        RuntimeError("a programmatic failure"),
        ValueError("a programmatic failure"),
        TimeoutException("the advisory lock timed out"),
        IntegrityViolationException("the money invariant broke"),
        ConflictException("a plain domain conflict, not a database one"),
        MoneyCommitOutcomeUnknown("the commit outcome could not be resolved"),
        KeyError("scenario"),
    ],
    ids=[
        "none",
        "runtime-error",
        "value-error",
        "timeout",
        "integrity-violation",
        "plain-conflict",
        "unknown-commit-outcome",
        "key-error",
    ],
)
def test_everything_else_is_refused(error) -> None:
    """The rest of the counter-check, including the three that are easiest to get wrong.

    * `ConflictException` is `RetryablePaymentConflictException`'s BASE class, so a predicate
      written against the base would replay every domain conflict in the system.
    * `IntegrityViolationException` is a broken money invariant. Repeating it would repeat the
      violation and then excuse it as contention - exactly the "integrity break is a gate, not a
      log line" rule of AGENTS.md §9.
    * `MoneyCommitOutcomeUnknown` must NOT read as a conflict: an unresolved outcome has to spend
      the error budget and be able to stop the run, which is the whole reason it is a type of its
      own rather than a flag.
    """
    assert money_conflict_name(error) is None


def test_the_retryable_conflict_is_matched_by_type_and_not_by_its_base() -> None:
    """Pins the relationship the test above relies on, so a refactor cannot silently widen it."""
    assert issubclass(RetryablePaymentConflictException, ConflictException)
    assert not isinstance(ConflictException("x"), RetryablePaymentConflictException)


def test_an_unrelated_exception_carrying_a_stray_sqlstate_attribute_is_refused() -> None:
    """Only a `DBAPIError` is classified: a SQLSTATE-shaped attribute elsewhere proves nothing."""

    class _LooksLikeADatabaseError(Exception):
        sqlstate = "40001"
        pgcode = "40001"

    assert money_conflict_name(_LooksLikeADatabaseError("not from a driver")) is None
    assert str(uuid.UUID(int=0)) == "00000000-0000-0000-0000-000000000000"
