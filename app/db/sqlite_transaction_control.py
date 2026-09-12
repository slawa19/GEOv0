"""SQLite transaction control: the database transaction starts where SQLAlchemy says it starts.

T1525 of programme 015. WHY THIS MODULE EXISTS.

SQLAlchemy 2.0 leaves transaction control on SQLite to the Python driver, and the driver (pysqlite,
and aiosqlite on top of it) runs in its legacy mode: `Connection.begin()` sends nothing, and the
driver emits `BEGIN` on its own only in front of INSERT/UPDATE/DELETE. A unit of work that has only
read therefore has NO transaction in the database. If it then opens a savepoint, SQLite starts a
transaction with that `SAVEPOINT` statement, the matching `RELEASE` commits it, and the root
`rollback()` that follows finds nothing to undo.

The application reaches that shape on its money paths. Measured 2026-09-11 on SQLAlchemy 2.0.25,
aiosqlite 0.20.0 and SQLite 3.45.1 by `tests/unit/test_p015_t1525_sqlite_savepoint_is_not_a_transaction.py`:

* `PaymentEngine.commit` only reads before its first flow on SQLite (every owner/tx/segment lock
  helper returns early there) and applies each flow inside `_apply_flow`'s savepoint. With an
  invariant violation after the flows, the payment ended ABORTED while its debt of 7.00000000
  stayed stored - through the engine directly and through `PaymentService`.
* The simulator tick executes each staged payment inside `RealPaymentsExecutor`'s per-action
  savepoint after only reading. A tick that failed after its payments phase rolled back, resolved
  its observations as rolled back and published no `tx.updated` - and kept two COMMITTED payments
  and 925.31 of debt in the database.
* Over the whole default test tier, 113 savepoints in 63 tests ran with no database transaction
  open, and 103 of them were committed by their own RELEASE.

WHAT THIS DOES - SQLAlchemy's documented recipe for pysqlite ("Serializable isolation / Savepoints /
Transactional DDL"): on connect, `isolation_level = None` switches the driver's own transaction
handling off; on SQLAlchemy's `begin`, a plain deferred `BEGIN` is emitted. From then on the database
transaction covers the reads too, a savepoint is always nested inside it, and a root rollback undoes
what was released inside it.

The setter used on connect is the ADAPTED connection's: for aiosqlite, SQLAlchemy's
`AsyncAdapt_aiosqlite_connection.isolation_level` forwards the assignment to the underlying
`sqlite3.Connection` through aiosqlite's own worker queue, so it runs on the thread that owns the
connection. The underlying `_conn` is deliberately not touched here.

THE COST, and it is real. With a real `BEGIN` a reading transaction holds a read snapshot:

* Under WAL, a transaction that has read and then tries to write fails with SQLITE_BUSY_SNAPSHOT
  (reported as "database is locked") if another connection committed in between. `busy_timeout`
  does not cure it - waiting cannot make the old snapshot current; only a restart of the whole
  transaction can.
* Under the rollback journal, a reading transaction holds a SHARED lock until it ends, so a writer
  cannot commit meanwhile, and two transactions that both read and then write can deadlock, which
  SQLite reports as busy immediately.
* A long idle read transaction keeps WAL checkpoints from completing past its snapshot, so the WAL
  file grows while it stays open.

Before this module those costs were invisible because the atomicity they pay for did not exist.

`BEGIN IMMEDIATE` would remove the snapshot-upgrade failure by taking the write lock up front, at the
price of serialising every transaction, reads included. It is a different trade and is not made here.

Do not combine this with `execution_options(isolation_level=...)` on a SQLite connection: SQLAlchemy's
pysqlite dialect implements that by assigning `isolation_level` on the driver connection, which would
put the connection back into legacy mode (SQLAlchemy documents the same warning for this recipe).
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine

#: SQLite's primary result code for "busy" (SQLITE_BUSY = 5). The extended codes are
#: `primary | (N << 8)`: SQLITE_BUSY_RECOVERY 261, SQLITE_BUSY_SNAPSHOT 517, SQLITE_BUSY_TIMEOUT 773.
_SQLITE_BUSY_PRIMARY_ERRORCODE = 5


def _driver_transaction_control_off(dbapi_connection, _connection_record) -> None:
    # The adapted connection's setter; see the module docstring for why not `_conn`.
    dbapi_connection.isolation_level = None


def _emit_begin(conn) -> None:
    conn.exec_driver_sql("BEGIN")


def install_sqlite_transaction_control(sync_engine: Engine) -> None:
    """Make the SQLite database transaction start at SQLAlchemy's `begin`, reads included.

    Takes the SYNC engine (`AsyncEngine.sync_engine` for async code). Idempotent. Refuses a
    non-SQLite engine: on any other backend these listeners would break transaction handling.
    """
    if sync_engine.dialect.name != "sqlite":
        raise ValueError(
            f"install_sqlite_transaction_control is for SQLite engines, got {sync_engine.dialect.name!r}"
        )
    if not event.contains(sync_engine, "connect", _driver_transaction_control_off):
        event.listen(sync_engine, "connect", _driver_transaction_control_off)
    if not event.contains(sync_engine, "begin", _emit_begin):
        event.listen(sync_engine, "begin", _emit_begin)


def has_sqlite_transaction_control(sync_engine: Engine) -> bool:
    """True when both halves of the control are installed on this engine."""
    return event.contains(sync_engine, "connect", _driver_transaction_control_off) and event.contains(
        sync_engine, "begin", _emit_begin
    )


def sqlite_busy_error_name(exc: BaseException) -> str | None:
    """The SQLITE_BUSY-family error name behind `exc`, or None if it is another failure.

    This is the retryable half of the cost documented above, and it exists because the control
    created it: a transaction that has read holds a snapshot, and a write on a stale snapshot fails
    at once with SQLITE_BUSY_SNAPSHOT ("database is locked"). The cure is to end the transaction and
    run the unit of work again from a fresh snapshot - exactly what a retry does - not to wait, which
    is why `busy_timeout` does not help here.

    Matched on the error CODE, not on the message: CPython 3.11's `sqlite3` carries
    `sqlite_errorcode` / `sqlite_errorname` on every error it raises, aiosqlite re-raises that
    object unchanged and SQLAlchemy keeps it as `DBAPIError.orig`. The message is useless for this
    decision - SQLITE_BUSY (5), SQLITE_BUSY_SNAPSHOT (517) and a rollback-journal deadlock all read
    "database is locked". There is deliberately no message fallback: if a future driver stops
    surfacing the code, this must go silent-red in
    `tests/unit/test_p015_t1525_sqlite_stale_snapshot_is_retried.py`, which asserts the attribute is
    present on a real error, rather than quietly match text.

    The whole SQLITE_BUSY family is included, not only _SNAPSHOT: a plain busy (a writer that held
    the lock past `busy_timeout`) and a rollback-journal deadlock are equally "nothing was written,
    run it again".
    """
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        code = getattr(current, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) == _SQLITE_BUSY_PRIMARY_ERRORCODE:
            name = getattr(current, "sqlite_errorname", None)
            return str(name) if name else f"sqlite_errorcode {code}"
        for related in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(related, BaseException):
                pending.append(related)
    return None
