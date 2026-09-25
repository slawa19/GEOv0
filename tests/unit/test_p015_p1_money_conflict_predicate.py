"""Programme 015 / P1: the predicate the money replay rests on, checked against real errors.

WHY THIS MODULE EXISTS. `money_conflict_name` decides whether the tick's money phase is repeated.
It is a filter, so AGENTS.md §9 requires it to carry a counter-check: a rule that excludes must be
shown still to accept the real cases AND still to refuse everything else. Both failure modes are
expensive and neither is visible from a green suite:

* Too permissive - a terminal failure classified as a conflict is replayed until the budget runs
  out, and then reported as contention rather than as the error it is. It would also be excluded
  from the error budget, so a genuinely broken run would never stop.
* Too strict - a real 40001 is treated as a programmatic failure, the replay never happens, and
  P1's defect is back with the tick counted as an error.

THE TWO REAL ERRORS HERE ARE PRODUCED BY THE DRIVER, NOT CONSTRUCTED. A 40001 comes from a genuine
concurrent update of one row at SERIALIZABLE, on a disposable PostgreSQL clone (mode B); an
integrity error comes from a foreign key the same database refuses. A hand-built exception would only
prove that the predicate matches what the test author believed the driver emits. Until 017 stage 3
(slice S3) both were produced on a SQLite file as a SQLITE_BUSY_SNAPSHOT and a SQLite foreign-key
error; the SQLite half left with SQLite, and these are its PostgreSQL form.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.simulator.money_replay import MoneyCommitOutcomeUnknown, money_conflict_name
from app.utils.exceptions import (
    ConflictException,
    IntegrityViolationException,
    RetryablePaymentConflictException,
    TimeoutException,
)


@pytest_asyncio.fixture
async def race_factory(committed_database):
    """A disposable PostgreSQL clone (mode B) at the application's isolation level, with two tables.

    Mode B because a serialization failure needs two transactions that both COMMIT or try to: the
    savepoint-wrapped `db_session` has one connection and no real commit. The clone is dropped when
    the test ends, so the probe tables never reach the tier's database.
    """
    async with committed_database.engine.begin() as conn:
        await conn.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY, v TEXT NOT NULL)"))
        await conn.execute(
            text(
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY,"
                "  parent_id INTEGER NOT NULL REFERENCES probe(id)"
                ")"
            )
        )
        await conn.execute(text("INSERT INTO probe (id, v) VALUES (1, 'seed')"))
    yield committed_database.sessionmaker


def _sqlstate(error: BaseException) -> str | None:
    orig = getattr(error, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


async def _real_serialization_failure(factory) -> DBAPIError:
    """A genuine 40001: a SERIALIZABLE reader updates a row another transaction changed and committed."""
    async with factory() as reader, factory() as writer:
        # Both take their snapshot by reading the row.
        await reader.execute(text("SELECT v FROM probe WHERE id = 1"))
        await writer.execute(text("SELECT v FROM probe WHERE id = 1"))

        await writer.execute(text("UPDATE probe SET v = 'writer' WHERE id = 1"))
        await writer.commit()

        with pytest.raises(DBAPIError) as conflict:
            await reader.execute(text("UPDATE probe SET v = 'reader' WHERE id = 1"))
            await reader.commit()
        await reader.rollback()
    return conflict.value


@pytest.mark.asyncio
async def test_a_real_serialization_failure_is_a_money_conflict(race_factory) -> None:
    """The accepting half: the conflict the replay exists for is recognised, by SQLSTATE."""
    conflict = await _real_serialization_failure(race_factory)

    # Non-vacuity for the stand itself: the driver really surfaced a serialization failure. If it
    # stopped surfacing the code, the predicate would go quietly strict and this is what notices.
    assert _sqlstate(conflict) == "40001", conflict

    assert money_conflict_name(conflict) == "40001"


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
            await session.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 424242)"))
        await session.rollback()

    # Non-vacuity: a real foreign-key refusal, not some other error.
    assert _sqlstate(integrity.value) == "23503", integrity.value
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

    019 stage 5 (`T1909`, precondition 1): the predicate now passes a 23505 whose STRUCTURED constraint
    name is `uq_debts_debtor_creditor_equivalent` (`is_debt_pair_collision`; the concurrent-insert
    reproducers are `tests/integration/test_p019_debt_pair_insert_race_is_retried_postgres.py`). This
    error names no constraint, so it is still refused - which is the counter-check that the new branch
    reads the name and not the code.
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
