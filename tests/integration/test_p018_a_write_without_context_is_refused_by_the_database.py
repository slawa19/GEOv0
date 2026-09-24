"""018 `T1801`, mechanism reproducer: a write to `debts` outside an operation is refused BY THE DATABASE.

Spec 018, Verification plan §1 ("Механизм"). On the tree before stage B1 the raw statement
`UPDATE debts SET amount = amount + 0.00000001` through `exec_driver_sql` COMMITTED - the listener
journal never saw a driver-level statement (its declared blind spot) - and only the scheduled
reconciliation found it afterwards (`tests/unit/test_p015_step5a_reconciliation.py`, the one-atom
case). After B1 the `debts` trigger refuses it with SQLSTATE `GE001` and the row is unchanged.

Counter-checks, each against the same stand (spec letters):

* (а) inside an OPEN envelope's context the same statement passes and leaves an entry of exactly one
  atom;
* (б) a pooled connection reused after a transaction that set the context refuses again - the
  setting's value there is the empty string, which `nullif` turns into "not set";
* (в) the context of a COMPLETED envelope, of an id that names no envelope, and a value that is no
  UUID at all refuse with `GE001` - never a foreign-key error;
* (г) a context set inside a savepoint that is rolled back is gone: the next write refuses;
* (д) `TRUNCATE debts` refuses, directly and through a `TRUNCATE ... CASCADE` of a referenced table;
* (е) an UPDATE that moves no money (`version` only) passes inside the context and records nothing.

Plus the key rule (`T1803`, spec "`UPDATE`, меняющий ключ"): moving a stored debt to another edge is
refused with `GE002`, inside a valid context, the row stays where it was and nothing is recorded.

THE STAND: one clone of the MIGRATED template shared by the module (`tests/p018_support.py`).
Statement-level refusals run inside one transaction that is rolled back; (б) needs a real commit and a
real pool, and commits into the same clone rows only it reads - the clone is dropped with the module.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger.book import Book, NewDebt, operation_for
from app.db.models.debt import Debt
from tests.p018_support import (
    ATOM,
    GE001,
    GE002,
    GUARD,
    context_of,
    debt_amount,
    entries_of,
    module_clone,
    refused,
    rolled_back_session,
    seed_world,
    serializable_engine,
)

_ONE_ATOM = "UPDATE debts SET amount = amount + 0.00000001 WHERE id = '{id}'"


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018a1801") as url:
        yield url


async def _one_debt(session: AsyncSession, *, amount: str = "10.00"):
    """A world with debt p0 -> p1 of `amount`, written through the book, in the open transaction."""

    world = await seed_world(session)
    posted = await Book.post(
        session,
        operation_for("TEST_FIXTURE", f"t1801-seed-{uuid.uuid4()}", {"seed": True}),
        [NewDebt(world.p(0), world.p(1), world.eq, Decimal(amount))],
    )
    assert posted.outcomes == ["APPLIED"]
    debt_id = (
        await session.execute(
            text("SELECT id FROM debts WHERE debtor_id = :d AND creditor_id = :c"),
            {"d": world.p(0), "c": world.p(1)},
        )
    ).scalar_one()
    return world, debt_id


async def _open_envelope(connection, *, kind: str = "TEST_FIXTURE") -> uuid.UUID:
    """An OPEN envelope inserted by hand, and the transaction's context set to it.

    What an application writer never does - the book does both - and exactly what a test of the
    trigger needs: the context is valid, the envelope is OPEN, and nothing else is in the way.
    """

    operation_id = uuid.uuid4()
    await connection.execute(
        text(
            "INSERT INTO debt_operations (id, kind, identity, intent, intent_digest, "
            "schema_version, money_encoding_version, intent_encoding_version, state) "
            "VALUES (:id, :kind, :identity, '{}', :digest, 2, 1, 1, 'OPEN')"
        ),
        {"id": operation_id, "kind": kind, "identity": f"t1801/{operation_id}", "digest": "0" * 64},
    )
    await connection.execute(
        text("SELECT set_config('geo.operation_id', :id, true)"), {"id": str(operation_id)}
    )
    return operation_id


@pytest.mark.asyncio
async def test_t1801_a_raw_driver_write_with_no_operation_is_refused_and_the_row_is_unchanged(
    migrated_url,
) -> None:
    """THE REPRODUCER. Before B1 this UPDATE committed and moved the debt by one atom."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _one_debt(session)
        assert await context_of(connection) in (None, ""), "the book left its context behind"

        assert await refused(connection, _ONE_ATOM.format(id=debt_id)) == GE001
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("10.00")

        # The Core and ORM doors refuse the same way: the trigger does not care who built the SQL.
        assert (
            await refused(connection, f"DELETE FROM debts WHERE id = '{debt_id}'") == GE001
        )
        assert (
            await refused(
                connection,
                "INSERT INTO debts (id, debtor_id, creditor_id, equivalent_id, amount, version) "
                f"VALUES ('{uuid.uuid4()}', '{world.p(1)}', '{world.p(2)}', '{world.eq}', 5, 0)",
            )
            == GE001
        )


@pytest.mark.asyncio
async def test_t1801_a_inside_an_open_operation_the_same_write_passes_and_is_journalled(
    migrated_url,
) -> None:
    """(а) The control: with the context of an OPEN envelope the same statement lands, one atom."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _one_debt(session)
        operation_id = await _open_envelope(connection)
        await connection.exec_driver_sql(_ONE_ATOM.format(id=debt_id))

        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal(
            "10.00000001"
        )
        entries = await entries_of(connection, operation_id)
        assert [(row.effect, row.amount_before, row.amount_after, row.delta) for row in entries] == [
            ("U", Decimal("10.00000000"), Decimal("10.00000001"), ATOM)
        ]


@pytest.mark.asyncio
async def test_t1801_c_a_completed_an_unknown_or_a_malformed_context_is_refused_as_ge001(
    migrated_url,
) -> None:
    """(в) Only an OPEN envelope admits a write, and every other context is `GE001`, not a FK error."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _one_debt(session)
        completed = (
            await connection.execute(
                text("SELECT id FROM debt_operations WHERE state = 'COMPLETED' LIMIT 1")
            )
        ).scalar_one()

        for context in (str(completed), str(uuid.uuid4()), "not-a-uuid", str(completed).upper()):
            await connection.execute(
                text("SELECT set_config('geo.operation_id', :id, true)"), {"id": context}
            )
            assert await refused(connection, _ONE_ATOM.format(id=debt_id)) == GE001, context
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("10.00")


@pytest.mark.asyncio
async def test_t1801_d_a_context_set_in_a_rolled_back_savepoint_does_not_survive_it(
    migrated_url,
) -> None:
    """(г) `set_config(.., true)` inside a savepoint is undone by its rollback."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _one_debt(session)
        savepoint = await connection.begin_nested()
        await _open_envelope(connection)
        await connection.exec_driver_sql(_ONE_ATOM.format(id=debt_id))  # admitted inside
        await savepoint.rollback()

        assert await context_of(connection) in (None, "")
        assert await refused(connection, _ONE_ATOM.format(id=debt_id)) == GE001
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("10.00")


@pytest.mark.asyncio
async def test_t1801_e_truncate_of_debts_is_refused_directly_and_through_a_cascade(
    migrated_url,
) -> None:
    """(д) A row trigger never sees a TRUNCATE; the statement trigger does, with a context or not."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, _ = await _one_debt(session)
        await _open_envelope(connection)
        assert await refused(connection, "TRUNCATE debts") == GUARD
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("10.00")

    # THE CASCADE, in a transaction with no envelope written: a transaction that inserted into
    # `debt_operations` holds pending deferred-trigger events, and PostgreSQL refuses to truncate a
    # table with pending events (`55006`) before any trigger runs - which would measure that rule,
    # not the TRUNCATE refusal.
    async with rolled_back_session(migrated_url) as (connection, _session):
        for statement in ("TRUNCATE participants CASCADE", "TRUNCATE equivalents CASCADE"):
            assert await refused(connection, statement) == GUARD, statement


@pytest.mark.asyncio
async def test_t1801_f_an_update_that_moves_no_money_passes_and_records_nothing(
    migrated_url,
) -> None:
    """(е) `version` only: admitted inside the context, and no entry - an entry needs `before <> after`."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _one_debt(session)
        operation_id = await _open_envelope(connection)
        await connection.exec_driver_sql(
            f"UPDATE debts SET version = version + 1, amount = amount WHERE id = '{debt_id}'"
        )
        version = (
            await connection.execute(text("SELECT version FROM debts WHERE id = :id"), {"id": debt_id})
        ).scalar_one()
        assert version >= 1
        assert await entries_of(connection, operation_id) == []


@pytest.mark.asyncio
async def test_t1803_moving_a_stored_debt_to_another_edge_is_refused_as_ge002(migrated_url) -> None:
    """The key is immutable: debtor, creditor or equivalent changed by UPDATE is `GE002`.

    Inside a valid context, so the refusal cannot be `GE001`; the ORM form (swap through attribute
    assignment) and the raw form are both refused, the row stays on its edge and nothing is recorded.
    """

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _one_debt(session)
        # A REAL second equivalent: a random id would meet the foreign key (whose RI trigger fires
        # before this one) and measure that instead.
        other = await seed_world(session, participants=0, label="t1803eq")
        operation_id = await _open_envelope(connection)

        for column, value in (
            ("creditor_id", world.p(2)),
            ("debtor_id", world.p(2)),
            ("equivalent_id", other.eq),
        ):
            assert (
                await refused(
                    connection, f"UPDATE debts SET {column} = '{value}' WHERE id = '{debt_id}'"
                )
                == GE002
            ), column

        # The ORM form, on a FRESH session sharing the connection: its own savepoint nests inside
        # ours, so the rollback a failed flush performs stays inside it.
        savepoint = await connection.begin_nested()
        orm = AsyncSession(
            bind=connection, autoflush=False, join_transaction_mode="create_savepoint"
        )
        try:
            debt = await orm.get(Debt, debt_id)
            debt.debtor_id, debt.creditor_id = debt.creditor_id, debt.debtor_id
            with pytest.raises(Exception) as caught:
                await orm.flush()
            assert getattr(getattr(caught.value, "orig", None), "sqlstate", None) == GE002
        finally:
            await orm.close()
        await savepoint.rollback()

        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("10.00")
        assert await entries_of(connection, operation_id) == []


@pytest.mark.asyncio
async def test_t1801_b_a_pooled_connection_after_a_committed_operation_refuses_again(
    migrated_url,
) -> None:
    """(б) The same physical connection, next transaction: the context was transaction-local.

    A real pool (one connection, reused) and a real commit - neither exists in a rolled-back stand.
    The backend pid is compared to prove it IS the same connection; without that the test could pass
    on a fresh connection that never saw a `SET LOCAL`.
    """

    engine = serializable_engine(migrated_url, pool_size=1, max_overflow=0)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session)
            await Book.post(
                session,
                operation_for("TEST_FIXTURE", f"t1801b-{uuid.uuid4()}", {"seed": True}),
                [NewDebt(world.p(0), world.p(1), world.eq, Decimal("10.00"))],
            )
            first_pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await session.commit()

        async with engine.connect() as connection:
            second_pid = (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            assert second_pid == first_pid, "the pool handed out another connection"
            assert await context_of(connection) == ""  # the placeholder survives, empty
            assert (
                await refused(
                    connection,
                    "UPDATE debts SET amount = amount + 0.00000001 "
                    f"WHERE debtor_id = '{world.p(0)}' AND creditor_id = '{world.p(1)}'",
                )
                == GE001
            )
            await connection.rollback()
    finally:
        await engine.dispose()
