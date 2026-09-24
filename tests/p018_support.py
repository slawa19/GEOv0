"""Shared stand pieces for the programme 018 stage B tests. NOT a test module.

WHICH DATABASE, AND WHY (spec 018, `FORK-5`). A test that needs a real COMMIT - the deferred
completion check, a pool connection reused after a commit, a second backend reading - takes a
`committed_database` clone (mode B). A test that only needs a statement's refusal and the rows it
left behind runs inside ONE transaction that is rolled back, on a clone of the MIGRATED template
that the whole module shares (`module_clone`): the triggers under test are the migration's, and no
clone is paid per test. The `create_all` path is exercised only by the parity module, which builds
both schemas itself.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal
from typing import Any, AsyncIterator, Iterator

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

#: SQLSTATEs the stage-B schema answers with.
GE001 = "GE001"
GE002 = "GE002"
GUARD = "23000"
CHECK_VIOLATION = "23514"
SERIALIZATION_FAILURE = "40001"

ATOM = Decimal("0.00000001")


def sqlstate_of(exc: BaseException) -> str | None:
    """The SQLSTATE a DBAPI error carries (asyncpg spells it `sqlstate`), or None."""

    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


@contextmanager
def module_clone(suffix: str) -> Iterator[str]:
    """A clone of the migrated mode-B template for a whole module, dropped when the module ends.

    Built and dropped on a private event loop (`tests.conftest._run_in_fresh_thread`): a module-scoped
    fixture outlives every per-test loop, and asyncpg connections are bound to the loop that made
    them. Every test then opens its own NullPool engine on the returned URL.
    """

    from tests.conftest import TEST_DATABASE_URL, _mode_b_template, _run_in_fresh_thread
    from tests.migrated_schema import (
        create_database,
        disconnect_everyone_from,
        drop_database,
        maintenance_connection,
        scratch_database_url,
    )

    clone_url, clone_name = scratch_database_url(TEST_DATABASE_URL, suffix)

    async def _create() -> None:
        template = await _mode_b_template()
        connection = await maintenance_connection(TEST_DATABASE_URL)
        try:
            await drop_database(connection, clone_name)
            await disconnect_everyone_from(connection, template)
            await create_database(connection, clone_name, template=template)
        finally:
            await connection.close()

    async def _drop() -> None:
        connection = await maintenance_connection(TEST_DATABASE_URL)
        try:
            await drop_database(connection, clone_name)
        finally:
            await connection.close()

    _run_in_fresh_thread(_create)
    try:
        yield clone_url
    finally:
        _run_in_fresh_thread(_drop)


def serializable_engine(url: str, **pool: Any):
    """An engine over `url` at the application's isolation level; NullPool unless a pool is asked for."""

    if not pool:
        pool = {"poolclass": NullPool}
    return create_async_engine(url, isolation_level="SERIALIZABLE", **pool)


@asynccontextmanager
async def rolled_back_session(url: str) -> AsyncIterator[tuple[Any, AsyncSession]]:
    """`(connection, session)` inside ONE transaction that is rolled back whatever happens.

    The session joins the connection's transaction with `create_savepoint`, like mode A, so its own
    commit is a RELEASE and nothing outlives the test. Nothing here can observe a real COMMIT - the
    deferred completion check needs `committed_database`.
    """

    engine = serializable_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                session = AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    autoflush=False,
                    join_transaction_mode="create_savepoint",
                )
                try:
                    yield connection, session
                finally:
                    await session.close()
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


async def refused(connection: Any, statement: str, params: Any = None) -> str | None:
    """Run `statement` inside a SAVEPOINT on `connection`; the SQLSTATE that refused it, or None.

    The savepoint is rolled back either way, so the surrounding transaction stays usable and the
    caller can read what the refused statement left behind.
    """

    savepoint = await connection.begin_nested()
    try:
        if params is None:
            await connection.exec_driver_sql(statement)
        else:
            await connection.exec_driver_sql(statement, params)
    except DBAPIError as exc:
        await savepoint.rollback()
        return sqlstate_of(exc)
    await savepoint.rollback()
    return None


class World:
    """One equivalent and a few participants, as the stand's rows."""

    def __init__(self, equivalent: Equivalent, participants: list[Participant]) -> None:
        self.equivalent = equivalent
        self.participants = participants

    @property
    def eq(self) -> uuid.UUID:
        return self.equivalent.id

    def p(self, index: int) -> uuid.UUID:
        return self.participants[index].id


async def seed_world(session: AsyncSession, *, participants: int = 3, label: str = "p018") -> World:
    """Flush one equivalent and `participants` participants into the session's transaction."""

    tag = uuid.uuid4().hex[:8]
    equivalent = Equivalent(code=f"Q{tag[:7]}".upper(), precision=2, is_active=True)
    people = [
        Participant(
            pid=f"{label}_{index}_{tag}",
            display_name=f"{label} {index}",
            public_key=f"pk_{label}_{index}_{tag}",
            type="person",
            status="active",
        )
        for index in range(participants)
    ]
    session.add_all([equivalent, *people])
    await session.flush()
    return World(equivalent, people)


async def debt_amount(connection: Any, debtor: uuid.UUID, creditor: uuid.UUID, eq: uuid.UUID):
    return (
        await connection.execute(
            text(
                "SELECT amount FROM debts WHERE debtor_id = :d AND creditor_id = :c "
                "AND equivalent_id = :e"
            ),
            {"d": debtor, "c": creditor, "e": eq},
        )
    ).scalar_one_or_none()


async def entries_of(connection: Any, operation_id: uuid.UUID) -> list[Any]:
    return (
        await connection.execute(
            text(
                "SELECT ordinal, effect, amount_before, amount_after, delta, debtor_id, creditor_id "
                "FROM debt_journal_entries WHERE operation_id = :op ORDER BY ordinal"
            ),
            {"op": operation_id},
        )
    ).all()


async def envelopes_named(connection: Any, identity: str) -> list[Any]:
    return (
        await connection.execute(
            text(
                "SELECT id, state, schema_version, effect_count FROM debt_operations "
                "WHERE identity = :identity"
            ),
            {"identity": identity},
        )
    ).all()


async def context_of(connection: Any) -> str | None:
    return (
        await connection.exec_driver_sql("SELECT current_setting('geo.operation_id', true)")
    ).scalar()
