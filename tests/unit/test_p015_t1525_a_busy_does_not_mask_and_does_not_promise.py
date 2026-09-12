"""T1525: a SQLITE_BUSY masks nothing, and promises nothing about a rollback.

Two defects found by the external review of T1525, both reproduced here before they were fixed.

1. A TERMINAL ERROR WAS CLASSIFIED AS TRANSIENT. `sqlite_busy_error_name` walked the whole exception
   chain and consulted `__context__`, so an exception carrying its OWN non-busy code was still
   reported busy whenever any busy sat in its context. Python sets `__context__` to whatever was
   being handled when an exception was raised, so a UNIQUE/PK violation raised inside a busy
   `except` block - an ordinary shape in retry code - was retried as if it were a lock conflict.
   Measured before the fix: current `SQLITE_CONSTRAINT_PRIMARYKEY` 1555, `__context__` a real
   `SQLITE_BUSY`, and the predicate answered `SQLITE_BUSY`.

   The rule now: if the exception under inspection carries its own `sqlite_errorcode`, THAT code
   decides and the walk stops. Only a wrapper with no code of its own is followed, and only into
   `orig` / `__cause__` - deliberate wrapping, which SQLAlchemy always uses - never `__context__`.

2. "BUSY MEANS NOTHING WAS WRITTEN" WAS FALSE. A busy raised by `commit()` while a statement is
   still in progress leaves the transaction OPEN with its own rows visible inside it. So a retry is
   safe only because the retry SITE rolls back first, never because of what the error means - and a
   rollback that fails must therefore stop the retry rather than be swallowed.

WHAT IS REAL HERE AND WHAT IS NOT. Every chained pair below is produced by the driver: a real busy
is raised by a real lock conflict, and the terminal errors are real constraint, read-only,
not-a-database and cannot-open failures. The one exception is the IOERR case, which is labelled at
its test: SQLITE_IOERR could not be provoked genuinely on this platform (an unlink mid-transaction
is refused by Windows), so that single test sets the documented code on a real `sqlite3` exception
and says so. The masking rule it checks is carried by the real errors either way.
"""

from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.payments.engine import PaymentEngine
from app.db.sqlite_transaction_control import (
    install_sqlite_transaction_control,
    sqlite_busy_error_name,
)
from tests.scratch_db import install_test_sqlite_pragmas

#: SQLITE_IOERR_WRITE. Documented at https://www.sqlite.org/rescode.html; 3338 & 0xFF == 10.
_SQLITE_IOERR_WRITE = 3338


# ---------------------------------------------------------------------------
# Real chained pairs, built with the driver rather than with attributes.
# ---------------------------------------------------------------------------


def _raised_inside_a_real_busy_handler(tmp_path, terminal) -> BaseException:
    """Run `terminal(holder)` INSIDE a genuine `except` handling a genuine SQLITE_BUSY.

    The chaining has to be built this way and not with `pytest.raises`: `__context__` is set by the
    interpreter only for an exception raised while another is BEING HANDLED, and `pytest.raises`
    has already left its handler by the time its block ends. This helper reproduces the real shape -
    retry code that catches a busy and, inside that handler, hits a terminal failure.
    """
    path = tmp_path / "busy.db"
    holder = sqlite3.connect(str(path), isolation_level=None, timeout=0.05)
    other = sqlite3.connect(str(path), isolation_level=None, timeout=0.05)
    try:
        holder.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        holder.execute("INSERT INTO t VALUES (1)")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t VALUES (2)")

        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("INSERT INTO t VALUES (3)")
        except sqlite3.OperationalError as busy:
            assert busy.sqlite_errorcode == 5, busy
            try:
                terminal(holder)
            except sqlite3.Error as caught:
                return caught
            raise AssertionError("the terminal operation did not fail")
        raise AssertionError("the second connection was not refused with a busy")
    finally:
        try:
            holder.rollback()
        except sqlite3.Error:
            pass
        holder.close()
        other.close()


def _assert_terminal_and_masked_before(error: BaseException, expected_name: str) -> None:
    assert getattr(error, "sqlite_errorname", None) == expected_name, error
    assert (error.sqlite_errorcode & 0xFF) != 5, error  # type: ignore[attr-defined]
    # Non-vacuity: the busy really is in the context, so the OLD walk really would have found it
    # and answered "busy". Without this the test could pass on an unchained error.
    assert getattr(error.__context__, "sqlite_errorcode", None) == 5, (
        "the real busy is not in __context__, so this proves nothing about masking"
    )
    assert sqlite_busy_error_name(error) is None, (
        f"{expected_name} was classified as a transient busy because a busy sat in its "
        "__context__; it would be retried, and retrying it cannot succeed"
    )


def test_a_constraint_violation_raised_inside_a_busy_handler_is_not_busy(tmp_path) -> None:
    """The reproduction: REAL busy in `__context__`, REAL PK violation in hand."""
    error = _raised_inside_a_real_busy_handler(
        tmp_path, lambda holder: holder.execute("INSERT INTO t VALUES (1)")
    )
    assert isinstance(error, sqlite3.IntegrityError)
    assert error.sqlite_errorcode == 1555
    _assert_terminal_and_masked_before(error, "SQLITE_CONSTRAINT_PRIMARYKEY")


def _readonly(tmp_path):
    def _terminal(_holder):
        readonly_path = tmp_path / "ro.db"
        setup = sqlite3.connect(str(readonly_path))
        setup.execute("CREATE TABLE t (x)")
        setup.commit()
        setup.close()
        sqlite3.connect(f"file:{readonly_path.as_posix()}?mode=ro", uri=True).execute(
            "INSERT INTO t VALUES (1)"
        )

    return _terminal


def _not_a_database(tmp_path):
    def _terminal(_holder):
        garbage = tmp_path / "garbage.db"
        garbage.write_bytes(b"not a sqlite database header at all" * 8)
        sqlite3.connect(str(garbage)).execute("SELECT count(*) FROM sqlite_master")

    return _terminal


def _cannot_open(tmp_path):
    def _terminal(_holder):
        directory = tmp_path / "a_directory"
        directory.mkdir()
        sqlite3.connect(str(directory)).execute("SELECT 1")

    return _terminal


@pytest.mark.parametrize(
    ("build_terminal", "expected_name"),
    (
        (_readonly, "SQLITE_READONLY"),
        (_not_a_database, "SQLITE_NOTADB"),
        (_cannot_open, "SQLITE_CANTOPEN_ISDIR"),
    ),
    ids=("readonly", "not-a-database", "cannot-open"),
)
def test_other_real_terminal_errors_with_a_busy_in_context_stay_terminal(
    tmp_path, build_terminal, expected_name
) -> None:
    """Not only constraint errors: any error carrying its own non-busy code decides for itself."""
    error = _raised_inside_a_real_busy_handler(tmp_path, build_terminal(tmp_path))
    _assert_terminal_and_masked_before(error, expected_name)


def test_an_ioerr_with_a_busy_in_context_is_not_busy(tmp_path) -> None:
    """The IOERR case. THE CODE HERE IS SET, NOT PROVOKED - and that is stated, not hidden.

    SQLITE_IOERR needs a failing filesystem; the usual trick of unlinking the database inside a
    write transaction is refused by Windows, so no genuine IOERR was available on this platform.
    The exception object, the handler and the busy in its context are all real; only
    `sqlite_errorcode` is assigned, to the documented SQLITE_IOERR_WRITE. The masking RULE is
    carried by the tests above, which use codes the driver really raised; this one exists so the
    IOERR family is named explicitly rather than assumed.
    """

    def _terminal(_holder):
        error = sqlite3.OperationalError("disk I/O error")
        error.sqlite_errorcode = _SQLITE_IOERR_WRITE
        error.sqlite_errorname = "SQLITE_IOERR_WRITE"
        raise error

    error = _raised_inside_a_real_busy_handler(tmp_path, _terminal)
    assert _SQLITE_IOERR_WRITE & 0xFF == 10, "SQLITE_IOERR is primary code 10"
    _assert_terminal_and_masked_before(error, "SQLITE_IOERR_WRITE")


def test_a_busy_at_commit_does_not_mean_the_transaction_rolled_back(tmp_path) -> None:
    """The contract correction: this busy leaves the transaction OPEN, with its own rows visible.

    The comments across the money paths used to say a SQLite busy meant "nothing was written, run it
    again". With a statement still in progress, `commit()` fails with SQLITE_BUSY and the
    transaction survives - so a retry that did not roll back first would run on top of its own
    uncommitted rows.

    AND THE SECOND HALF, added 2026-09-12 after an external review: the rollback is checked for its
    PHYSICAL RESULT. Until then this test proved only the first half - that the busy left the
    transaction open with its own row visible - and then rolled back in `finally` without asserting
    anything, while the entire retry-safety argument rests on the other half ("retrying is safe
    because the retry site rolls back first"). An unverified rollback is exactly the shape the
    argument cannot afford, so the rows are now counted again through a FRESH connection, which
    sees only what is durable.

    THE MUTATION that must turn this red again: replace the `connection.rollback()` below with
    `connection.commit()` (the cursor is closed by then, so it succeeds) - the transaction's own
    rows become durable and the two counts below stop being zero.
    """
    path = tmp_path / "commit.db"
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO t VALUES (1, 'seed'), (2, 'seed'), (3, 'seed')")
        connection.execute("BEGIN")
        connection.execute("INSERT INTO t VALUES (10, 'own-row')")
        # An UNEXHAUSTED cursor keeps a statement in progress across the commit.
        cursor = connection.execute("INSERT INTO t VALUES (11, 'r'), (12, 'r') RETURNING id")
        cursor.fetchone()

        with pytest.raises(sqlite3.OperationalError) as refusal:
            connection.commit()

        assert refusal.value.sqlite_errorcode == 5, refusal.value
        assert sqlite_busy_error_name(refusal.value) == "SQLITE_BUSY"
        assert connection.in_transaction is True, (
            "this stand no longer reproduces a busy that leaves the transaction open; the retry "
            "sites' rollback-before-rerun is justified by exactly this case"
        )
        cursor.close()
        still_there = connection.execute(
            "SELECT count(*) FROM t WHERE v = 'own-row'"
        ).fetchone()[0]
        assert still_there == 1, "the transaction's own row should still be visible inside it"
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()

    # A FRESH connection sees only what is DURABLE, so this is the physical result of the
    # rollback rather than a second look through the transaction that wrote the rows.
    verifier = sqlite3.connect(str(path))
    try:
        own_rows = verifier.execute(
            "SELECT count(*) FROM t WHERE v = 'own-row'"
        ).fetchone()[0]
        returning_rows = verifier.execute(
            "SELECT count(*) FROM t WHERE id IN (11, 12)"
        ).fetchone()[0]
        seed_rows = verifier.execute("SELECT count(*) FROM t WHERE v = 'seed'").fetchone()[0]
    finally:
        verifier.close()

    assert own_rows == 0, (
        "the rolled-back transaction's own row is STILL STORED. The retry sites justify re-running "
        "a busy unit of work by rolling back first; if that rollback does not remove what the busy "
        "left open, the next attempt builds on its own uncommitted rows"
    )
    assert returning_rows == 0, (
        "the rows of the INSERT ... RETURNING that held a statement open across the commit are "
        f"still stored ({returning_rows} of them)"
    )
    # Non-vacuity: the rollback undid this transaction, it did not empty the table.
    assert seed_rows == 3, seed_rows


# ---------------------------------------------------------------------------
# The positive control and the retry site, on a real engine.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def busy_factory(tmp_path):
    """A file-backed WAL engine: BUSY_SNAPSHOT needs WAL and a real transaction on both sides."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'race.db').as_posix()}"
    engine = create_async_engine(url, poolclass=NullPool)
    install_test_sqlite_pragmas(engine.sync_engine, url=url)
    install_sqlite_transaction_control(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY, v TEXT)"))
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _provoke_busy_snapshot(factory) -> DBAPIError:
    """A REAL SQLITE_BUSY_SNAPSHOT: two readers, one commits, the other then writes."""
    async with factory() as reader, factory() as writer:
        await reader.execute(text("SELECT count(*) FROM probe"))
        await writer.execute(text("SELECT count(*) FROM probe"))
        await writer.execute(text("INSERT INTO probe (v) VALUES ('writer')"))
        await writer.commit()
        with pytest.raises(DBAPIError) as busy:
            await reader.execute(text("INSERT INTO probe (v) VALUES ('reader')"))
        await reader.rollback()
    return busy.value


async def test_a_real_busy_wrapped_in_dbapierror_orig_is_still_classified(busy_factory) -> None:
    """The positive control for the narrowed walk: the wrapper has no code, so `orig` is followed."""
    busy = await _provoke_busy_snapshot(busy_factory)

    # Non-vacuity: the wrapper really carries no code of its own, so the walk really is doing work.
    assert getattr(busy, "sqlite_errorcode", None) is None
    assert getattr(busy.orig, "sqlite_errorcode", None) == 517
    assert sqlite_busy_error_name(busy) == "SQLITE_BUSY_SNAPSHOT", (
        "narrowing the chain walk must not lose a genuine busy that arrives wrapped by SQLAlchemy"
    )


async def test_a_failed_rollback_stops_the_retry_instead_of_re_running(busy_factory) -> None:
    """The rollback is the retry's precondition, so its failure must end the attempt.

    THE ROLLBACK FAILS FOR REAL HERE - nothing is monkeypatched. The unit of work closes the
    driver connection out from under the session, so the `session.rollback()` that
    `_run_uow_with_retry` performs next raises on its own ("no active connection"), exactly as it
    would if the connection had dropped in production. That matters because the defect being held
    is precisely "the rollback did not happen": a stubbed `rollback` proves the branch is wired,
    while a genuinely broken connection proves the branch is reached by a real failure.
    """
    busy = await _provoke_busy_snapshot(busy_factory)

    session = busy_factory()
    calls: list[int] = []
    try:
        engine = PaymentEngine(session)
        engine._retry_attempts = 3
        engine._retry_base_delay_s = 0.0
        engine._retry_max_delay_s = 0.0
        assert engine._is_retryable_db_error(busy, op="commit") is True, (
            "non-vacuity: without a retryable error this test would pass for the wrong reason"
        )

        async def _uow():
            calls.append(1)
            # Break the connection for real, then fail with the busy. The retry wrapper's own
            # rollback is what has to fail next - and it is not stubbed.
            connection = await session.connection()
            raw = await connection.get_raw_connection()
            await raw.driver_connection.close()
            raise busy

        with pytest.raises(DBAPIError) as raised:
            await engine._run_uow_with_retry(op="commit", fn=_uow)
    finally:
        try:
            await session.close()
        except Exception:
            pass

    assert calls == [1], (
        f"the unit of work ran {len(calls)} times after a FAILED rollback: the second attempt would "
        "build on the first attempt's uncommitted rows, because a SQLite busy does not imply the "
        "transaction rolled back"
    )
    assert raised.value is busy, "the original database error must be what the caller sees"
    cause = raised.value.__cause__
    assert isinstance(cause, SQLAlchemyError), (
        f"the failed rollback should be attached as the cause, got {cause!r}"
    )
    assert "no active connection" in str(cause).lower() or "closed" in str(cause).lower(), cause


async def _a_real_terminal_raised_inside_a_real_busy_handler(
    factory,
) -> tuple[DBAPIError, DBAPIError]:
    """(a genuine BUSY_SNAPSHOT, a genuine PK violation raised INSIDE its handler).

    Both arrive as SQLAlchemy `DBAPIError`s through aiosqlite, which is what the payment service's
    classifier actually receives. The raw-`sqlite3` pairs at the top of this module prove the
    PREDICATE; this one proves the CALLER, which had a chain walk of its own.
    """
    async with factory() as reader, factory() as writer, factory() as third:
        await reader.execute(text("SELECT count(*) FROM probe"))
        await writer.execute(text("SELECT count(*) FROM probe"))
        await writer.execute(text("INSERT INTO probe (id, v) VALUES (1, 'writer')"))
        await writer.commit()

        try:
            await reader.execute(text("INSERT INTO probe (id, v) VALUES (2, 'reader')"))
        except DBAPIError as busy:
            assert getattr(busy.orig, "sqlite_errorcode", None) == 517, busy
            # INSIDE the handler, so the interpreter sets `__context__` on what is raised next.
            try:
                await third.execute(text("INSERT INTO probe (id, v) VALUES (1, 'duplicate')"))
            except DBAPIError as terminal:
                for session in (reader, third):
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                return busy, terminal
            raise AssertionError("the duplicate primary key did not fail")
        raise AssertionError("the reader's write was not refused with a busy")


async def test_the_service_classifier_does_not_read_a_busy_out_of___context__(
    busy_factory,
) -> None:
    """The masking defect one layer up: the SERVICE walked `__context__` and re-masked the error.

    `sqlite_busy_error_name` was narrowed first, but `_classify_payment_db_error` had its OWN
    traversal that included `__context__` and applied the fixed predicate to nodes that unfixed
    traversal handed it. So a terminal SQLITE_CONSTRAINT_PRIMARYKEY raised inside a busy handler
    still became a `RetryablePaymentConflictException` - and retrying it cannot succeed.

    THE MUTATION that must turn this red again: add `current.__context__` back to the `following`
    step of `_iter_exception_chain` in `app/core/payments/service.py`.
    """
    from app.core.payments.service import _classify_payment_db_error
    from app.utils.exceptions import RetryablePaymentConflictException

    busy, terminal = await _a_real_terminal_raised_inside_a_real_busy_handler(busy_factory)

    # Non-vacuity: the real busy IS in `__context__`, so the old walk really would have found it.
    assert terminal.__context__ is busy, terminal.__context__
    assert getattr(terminal.orig, "sqlite_errorcode", None) == 1555, terminal
    assert sqlite_busy_error_name(terminal) is None

    assert not isinstance(
        _classify_payment_db_error(terminal), RetryablePaymentConflictException
    ), (
        "a terminal primary-key violation was classified as a retryable conflict because a busy "
        "sat in its __context__; the payment would be retried and the retry cannot succeed"
    )
    # The positive control: narrowing the walk must not lose the genuine busy.
    assert isinstance(
        _classify_payment_db_error(busy), RetryablePaymentConflictException
    ), "a real SQLITE_BUSY_SNAPSHOT must still classify as a retryable conflict"


async def test_the_same_conflict_is_retried_when_the_rollback_succeeds(busy_factory) -> None:
    """The control: the retry itself still works, so the test above pins the failure path only."""
    busy = await _provoke_busy_snapshot(busy_factory)

    async with busy_factory() as session:
        engine = PaymentEngine(session)
        engine._retry_attempts = 3
        engine._retry_base_delay_s = 0.0
        engine._retry_max_delay_s = 0.0

        calls: list[int] = []

        async def _uow():
            calls.append(1)
            if len(calls) == 1:
                raise busy
            return "committed"

        result = await engine._run_uow_with_retry(op="commit", fn=_uow)

    assert result == "committed"
    assert calls == [1, 1], f"expected one retry after a rollback that succeeded, got {len(calls)}"
