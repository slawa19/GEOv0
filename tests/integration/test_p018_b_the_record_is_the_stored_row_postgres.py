"""018 stage B1: the journal entry IS the row the statement stored - whatever rewrote the statement.

WHAT THIS REPLACES (manifest `T1808` section 5, the `NEW-C`/`NEW-A`/`NEW-G`/`NEW-H` rows of the
`T1528`, `T1530`, `T1531` and `T1532` files). Programme 015 closed a family of holes in the LISTENER
journal where a neighbour rewrote a debt statement after the journal had verified it - a late
`before_flush` listener putting a SQL expression into the UPDATE (`T1528`), a `before_execute` listener
rewriting the parameters on the connection or on the engine instance (`T1528`, `T1531`), a listener
rewriting the journal's own entry INSERT (`T1530`). Each was answered with a REFUSAL, because the
listener built the entry from what it had verified, and the database then held something else.

THE OUTCOME FLIPS, AND ON PURPOSE (spec 018 `FORK-2`: "`T1528`: выражения суммы и подстановки
параметров; `T1531`: переписанные чтения проверки — принимается изменённый исход там, где `OLD`/`NEW`
снимает расхождение записи и строки"). Since stage B1 the `debts` trigger writes the entry from `OLD`
and `NEW` inside the same statement, so there is no verified-then-executed gap left for a rewrite to
fall into: the stored row and its entry cannot disagree. These tests assert THAT, form by form - the
rewrite really happened (premise), the row holds the rewritten value, and the operation's entry
records exactly that value - plus that criterion (a)'s own identity (`sum(delta) == amount` on an
edge with no other history) holds. Catching a writer that moved the WRONG amount or edge faithfully is
criterion (b)'s job (`tests/unit/test_p015_step5b_criterion_b.py`, the C6 tests), not this one's.

The one form that stays a refusal: a rewrite that MOVES THE KEY of a stored debt (`T1528`'s literal
edge) is `GE002`, row and journal unchanged.

Also here, because they are the same claim on the ordinary path: one flush with an insert, an update
and a delete, and one multi-row statement, each give one entry per row (`NEW-A`); a savepoint rolled
back INSIDE an operation - the `StaleDataError` retry shape - leaves the operation `COMPLETED` with only
the surviving rows' entries, and nothing of the rolled-back attempt is durable (`NEW-G`); the full
width of `NUMERIC(20, 8)` is stored and journalled exactly (`NEW-H`).

WHAT IT DOES NOT CLAIM (spec, "узко"): a rewrite is not refused and not detected here - it is recorded
faithfully. The trigger is not a trust boundary.

THE STAND: one clone of the MIGRATED template for the module (`tests/p018_support.py`). The statement
forms run inside one transaction that is rolled back; the savepoint test commits, into rows only it
reads.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, insert, literal, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger.book import Book, NewDebt, operation_for
from app.db.models.debt import Debt
from tests.p018_support import (
    GE002,
    debt_amount,
    entries_of,
    envelopes_named,
    module_clone,
    rolled_back_session,
    seed_world,
    serializable_engine,
    sqlstate_of,
)

FULL_WIDTH = Decimal("999999999999.99999999")


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018brow") as url:
        yield url


def _fixture(label: str):
    identity = f"p018-row/{label}/{uuid.uuid4()}"
    return identity, operation_for("TEST_FIXTURE", identity, {"label": label})


async def _seeded(session: AsyncSession, amount: str = "10.00", *, participants: int = 3):
    """A world and debt p0 -> p1 of `amount`, written through the book in the open transaction."""

    world = await seed_world(session, participants=participants)
    _identity, op = _fixture("seed")
    await Book.post(session, op, [NewDebt(world.p(0), world.p(1), world.eq, Decimal(amount))])
    debt_id = (
        await session.execute(
            select(Debt.id).where(Debt.debtor_id == world.p(0), Debt.creditor_id == world.p(1))
        )
    ).scalar_one()
    return world, debt_id


async def _the_only_entry_of(connection, identity: str) -> Any:
    (envelope,) = await envelopes_named(connection, identity)
    assert envelope.state == "COMPLETED", envelope
    entries = await entries_of(connection, envelope.id)
    assert envelope.effect_count == len(entries) == 1, (envelope, entries)
    return entries[0]


async def _journal_explains_the_edge(connection, world, debtor, creditor) -> None:
    """Criterion (a)'s identity on an edge whose whole history is journalled: sum(delta) == amount."""

    total = (
        await connection.execute(
            text(
                "SELECT coalesce(sum(delta), 0) FROM debt_journal_entries "
                "WHERE equivalent_id = :e AND debtor_id = :d AND creditor_id = :c"
            ),
            {"e": world.eq, "d": debtor, "c": creditor},
        )
    ).scalar_one()
    stored = await debt_amount(connection, debtor, creditor, world.eq) or Decimal(0)
    assert total == stored, f"the journal explains {total} of a debt that holds {stored}"


def _rewrite_debt_params(target: Any, rewrite) -> Any:
    """A `before_execute` listener on `target` that rewrites a `debts` INSERT/UPDATE parameter dict."""

    @event.listens_for(target, "before_execute", retval=True)
    def _rewrite(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        name = type(clause).__name__
        table = getattr(getattr(clause, "table", None), "name", None)
        if table == "debts" and name.endswith(("Insert", "Update")) and isinstance(params, dict):
            changed = rewrite(dict(params)) if params else None
            if changed is not None:
                return clause, multiparams, changed
        return clause, multiparams, params

    return _rewrite


# =================================================================================================
# T1528 / T1531 forms: the statement was rewritten after the application built it
# =================================================================================================


@pytest.mark.asyncio
async def test_a_sql_expression_amount_from_a_late_listener_is_stored_and_journalled_as_stored(
    migrated_url,
) -> None:
    """`T1528` expression form: a late `before_flush` listener sets `amount = Debt.amount + 1` on a
    debt the operation only touched for its version. Before B1: refused (`UNVERIFIED_DEBT_WRITE`),
    10 kept, no entry. Now: 11 stored and the entry says `U 10 -> 11, delta 1`."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _seeded(session)
        fired: list[str] = []

        @event.listens_for(session.sync_session, "before_flush")
        def _a_late_listener(sync_session, _flush_context, _instances) -> None:
            if fired:
                return
            for obj in list(sync_session.dirty):
                if isinstance(obj, Debt) and obj.id == debt_id:
                    obj.amount = Debt.amount + 1
                    fired.append("before_flush")

        identity, op = _fixture("expression-amount")
        async with Book.operation(session, op):
            subject = await session.get(Debt, debt_id)
            subject.version = subject.version + 1
        event.remove(session.sync_session, "before_flush", _a_late_listener)

        assert fired == ["before_flush"], "premise: the late listener never rewrote the amount"
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("11")
        entry = await _the_only_entry_of(connection, identity)
        assert (entry.effect, entry.amount_before, entry.amount_after, entry.delta) == (
            "U",
            Decimal("10"),
            Decimal("11"),
            Decimal("1"),
        )
        await _journal_explains_the_edge(connection, world, world.p(0), world.p(1))


@pytest.mark.asyncio
async def test_a_literal_edge_from_a_late_listener_is_refused_as_a_key_change(migrated_url) -> None:
    """`T1528` key form - the one rewrite that stays a refusal: `creditor_id = literal(other)` moves
    the stored debt to another edge. `GE002`; the row stays on its edge with its amount; the refused
    operation leaves no envelope and no entry."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _seeded(session)
        fired: list[str] = []

        @event.listens_for(session.sync_session, "before_flush")
        def _a_late_listener(sync_session, _flush_context, _instances) -> None:
            if fired:
                return
            for obj in list(sync_session.dirty):
                if isinstance(obj, Debt) and obj.id == debt_id:
                    obj.creditor_id = literal(world.p(2))
                    fired.append("before_flush")

        identity, op = _fixture("literal-edge")
        with pytest.raises(DBAPIError) as refusal:
            async with Book.operation(session, op):
                subject = await session.get(Debt, debt_id)
                subject.amount = Decimal("11.00")
        event.remove(session.sync_session, "before_flush", _a_late_listener)
        session.expunge_all()

        assert fired == ["before_flush"], "premise: the late listener never moved the edge"
        assert sqlstate_of(refusal.value) == GE002, refusal.value
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("10")
        assert await debt_amount(connection, world.p(0), world.p(2), world.eq) is None
        assert await envelopes_named(connection, identity) == []


@pytest.mark.asyncio
async def test_parameters_rewritten_on_the_connection_are_what_is_stored_and_journalled(
    migrated_url,
) -> None:
    """`T1528` decimal-scan / `T1531` neighbour form: a `Connection`-level `before_execute` listener
    rewrites the UPDATE's `amount` from 11 to 12 (and adds an unused 11). Before B1: refused, 10 kept.
    Now: 12 stored and the entry says `10 -> 12` - never the 11 the application asked for."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _seeded(session)
        rewritten: list[dict] = []

        def _rewrite(params: dict) -> dict | None:
            if params.get("amount") != Decimal("11.00"):
                return None
            params["amount"] = Decimal("12.00")
            params["audit_amount"] = Decimal("11.00")
            rewritten.append(dict(params))
            return params

        sync_connection = connection.sync_connection
        listener = _rewrite_debt_params(sync_connection, _rewrite)
        try:
            identity, op = _fixture("param-rewrite")
            async with Book.operation(session, op):
                subject = await session.get(Debt, debt_id)
                subject.amount = Decimal("11.00")
        finally:
            event.remove(sync_connection, "before_execute", listener)

        assert rewritten, "premise: the connection-level listener never rewrote the UPDATE"
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("12")
        entry = await _the_only_entry_of(connection, identity)
        assert (entry.effect, entry.amount_before, entry.amount_after, entry.delta) == (
            "U",
            Decimal("10"),
            Decimal("12"),
            Decimal("2"),
        )
        await _journal_explains_the_edge(connection, world, world.p(0), world.p(1))


@pytest.mark.asyncio
async def test_an_insert_rewritten_on_the_engine_instance_is_stored_and_journalled_as_stored(
    migrated_url,
) -> None:
    """`T1528` readback form: an ENGINE-INSTANCE `before_execute` listener - which ran after every
    class-level one, so after the listener journal's guard - rewrites an INSERT from 11 to 12. Before
    B1: refused by the readback (`UNRECONCILED_DEBT_ROW`), nothing stored. Now: `I 12`, row 12."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world = await seed_world(session)
        rewritten: list[dict] = []

        def _rewrite(params: dict) -> dict | None:
            if params.get("amount") != Decimal("11.00"):
                return None
            params["amount"] = Decimal("12.00")
            rewritten.append(dict(params))
            return params

        engine = connection.sync_connection.engine
        listener = _rewrite_debt_params(engine, _rewrite)
        try:
            identity, op = _fixture("engine-rewrite")
            async with Book.operation(session, op):
                session.add(
                    Debt(
                        debtor_id=world.p(0),
                        creditor_id=world.p(1),
                        equivalent_id=world.eq,
                        amount=Decimal("11.00"),
                    )
                )
        finally:
            event.remove(engine, "before_execute", listener)

        assert rewritten, "premise: the engine-instance listener never rewrote the INSERT"
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == Decimal("12")
        entry = await _the_only_entry_of(connection, identity)
        assert (entry.effect, entry.amount_before, entry.amount_after, entry.delta) == (
            "I",
            None,
            Decimal("12"),
            Decimal("12"),
        )
        await _journal_explains_the_edge(connection, world, world.p(0), world.p(1))


@pytest.mark.asyncio
async def test_the_entry_insert_is_never_a_client_statement_so_no_listener_can_rewrite_it(
    migrated_url,
) -> None:
    """`T1530` form: the listener journal INSERTed its entries from the client, and an engine-instance
    listener could rewrite that INSERT (amount, delta, or a dropped row). Now the entries are written
    inside the `debts` trigger: across a whole operation that changes two rows, NO client statement
    inserts into `debt_journal_entries` - there is nothing to rewrite - and each row has its entry."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, debt_id = await _seeded(session)
        seen: list[str] = []

        @event.listens_for(connection.sync_connection, "before_cursor_execute")
        def _every_statement(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            seen.append(" ".join(str(statement).split()).upper())

        try:
            identity, op = _fixture("no-client-entry-insert")
            async with Book.operation(session, op):
                subject = await session.get(Debt, debt_id)
                subject.amount = Decimal("10.99999991")
                session.add(
                    Debt(
                        debtor_id=world.p(1),
                        creditor_id=world.p(2),
                        equivalent_id=world.eq,
                        amount=Decimal("5"),
                    )
                )
        finally:
            event.remove(connection.sync_connection, "before_cursor_execute", _every_statement)

        assert any(s.startswith(("UPDATE DEBTS", "INSERT INTO DEBTS")) for s in seen), (
            f"premise: the listener saw no debt statement at all: {seen}"
        )
        assert not [s for s in seen if s.startswith("INSERT INTO DEBT_JOURNAL_ENTRIES")], seen
        (envelope,) = await envelopes_named(connection, identity)
        entries = await entries_of(connection, envelope.id)
        assert sorted((e.effect, e.amount_after) for e in entries) == [
            ("I", Decimal("5")),
            ("U", Decimal("10.99999991")),
        ], entries
        assert envelope.effect_count == 2
        for debtor, creditor in ((world.p(0), world.p(1)), (world.p(1), world.p(2))):
            await _journal_explains_the_edge(connection, world, debtor, creditor)


# =================================================================================================
# NEW-A: one entry per row, whatever the statement shape
# =================================================================================================


@pytest.mark.asyncio
async def test_one_flush_with_an_insert_an_update_and_a_delete_and_one_multi_row_statement(
    migrated_url,
) -> None:
    """`T1528` control / `B4` cond. 3 multi-row control: one flush carrying `I`, `U` and `D`, then one
    Core INSERT of two rows - one entry per row, ordered by increasing `ordinal`, `effect_count` = rows."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world, changed_id = await _seeded(session, participants=6)
        _gone_identity, gone_op = _fixture("to-delete")
        await Book.post(session, gone_op, [NewDebt(world.p(0), world.p(2), world.eq, Decimal("7"))])
        deleted_id = (
            await session.execute(
                select(Debt.id).where(Debt.debtor_id == world.p(0), Debt.creditor_id == world.p(2))
            )
        ).scalar_one()

        identity, op = _fixture("three-effects")
        async with Book.operation(session, op):
            (await session.get(Debt, changed_id)).amount = Decimal("13")
            await session.delete(await session.get(Debt, deleted_id))
            session.add(
                Debt(
                    debtor_id=world.p(0),
                    creditor_id=world.p(3),
                    equivalent_id=world.eq,
                    amount=Decimal("5"),
                )
            )
            await session.flush()
            await session.execute(
                insert(Debt),
                [
                    {
                        "id": uuid.uuid4(),
                        "debtor_id": world.p(4),
                        "creditor_id": world.p(5),
                        "equivalent_id": world.eq,
                        "amount": Decimal("32"),
                        "version": 1,
                    },
                    {
                        "id": uuid.uuid4(),
                        "debtor_id": world.p(5),
                        "creditor_id": world.p(3),
                        "equivalent_id": world.eq,
                        "amount": Decimal("33"),
                        "version": 1,
                    },
                ],
            )

        (envelope,) = await envelopes_named(connection, identity)
        entries = await entries_of(connection, envelope.id)
        assert envelope.state == "COMPLETED" and envelope.schema_version == 2, envelope
        assert envelope.effect_count == len(entries) == 5, entries
        ordinals = [e.ordinal for e in entries]
        assert ordinals == sorted(ordinals) and len(set(ordinals)) == 5, ordinals
        assert sorted((e.effect, e.amount_before, e.amount_after) for e in entries[:3]) == [
            ("D", Decimal("7"), None),
            ("I", None, Decimal("5")),
            ("U", Decimal("10"), Decimal("13")),
        ], entries
        assert [(e.effect, e.delta) for e in entries[3:]] == [
            ("I", Decimal("32")),
            ("I", Decimal("33")),
        ], entries
        assert await debt_amount(connection, world.p(0), world.p(2), world.eq) is None


# =================================================================================================
# NEW-H: the full width of the column, on the ordinary path and through a rewrite
# =================================================================================================


@pytest.mark.asyncio
async def test_full_width_money_is_stored_and_journalled_exactly_even_when_rewritten(
    migrated_url,
) -> None:
    """`NEW-H`, and the `T1528`/`T1530` asyncpg full-width forms: `999999999999.99999999` inserted,
    then moved by exactly one unit, is stored and journalled to the last digit; a parameter rewrite
    to the full width on another edge is stored and journalled as stored."""

    async with rolled_back_session(migrated_url) as (connection, session):
        world = await seed_world(session)
        identity, op = _fixture("full-width-insert")
        await Book.post(session, op, [NewDebt(world.p(0), world.p(1), world.eq, FULL_WIDTH)])
        entry = await _the_only_entry_of(connection, identity)
        assert (entry.effect, entry.amount_after, entry.delta) == ("I", FULL_WIDTH, FULL_WIDTH)

        debt_id = (
            await session.execute(
                select(Debt.id).where(Debt.debtor_id == world.p(0), Debt.creditor_id == world.p(1))
            )
        ).scalar_one()
        identity, op = _fixture("full-width-update")
        async with Book.operation(session, op):
            (await session.get(Debt, debt_id)).amount = FULL_WIDTH - 1
        entry = await _the_only_entry_of(connection, identity)
        assert (entry.amount_before, entry.amount_after, entry.delta) == (
            FULL_WIDTH,
            FULL_WIDTH - 1,
            Decimal("-1"),
        )
        assert await debt_amount(connection, world.p(0), world.p(1), world.eq) == FULL_WIDTH - 1

        rewritten: list[dict] = []

        def _rewrite(params: dict) -> dict | None:
            if params.get("amount") != Decimal("11.00"):
                return None
            params["amount"] = Decimal("100000000000.00000001")
            rewritten.append(dict(params))
            return params

        sync_connection = connection.sync_connection
        listener = _rewrite_debt_params(sync_connection, _rewrite)
        try:
            identity, op = _fixture("full-width-rewrite")
            async with Book.operation(session, op):
                session.add(
                    Debt(
                        debtor_id=world.p(1),
                        creditor_id=world.p(2),
                        equivalent_id=world.eq,
                        amount=Decimal("11.00"),
                    )
                )
        finally:
            event.remove(sync_connection, "before_execute", listener)
        assert rewritten, "premise: the full-width rewrite never happened"
        entry = await _the_only_entry_of(connection, identity)
        stored = await debt_amount(connection, world.p(1), world.p(2), world.eq)
        assert stored == entry.amount_after == Decimal("100000000000.00000001"), (stored, entry)


# =================================================================================================
# NEW-G: a savepoint rolled back INSIDE an operation (the StaleDataError retry shape)
# =================================================================================================


@pytest.mark.asyncio
async def test_an_inner_savepoint_rolled_back_inside_an_operation_leaves_only_what_survived(
    migrated_url,
) -> None:
    """`T1530` / `T1532` control: two writes in one operation, the second inside a savepoint that is
    rolled back. The operation COMPLETES; its `effect_count` and entries are the surviving write only;
    after the COMMIT, read on another connection, the debts are 11 and 20 - nothing of the
    rolled-back attempt is durable."""

    engine = serializable_engine(migrated_url)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False, autoflush=False) as session:
            world = await seed_world(session)
            _seed_identity, seed_op = _fixture("savepoint-seed")
            await Book.post(
                session,
                seed_op,
                [
                    NewDebt(world.p(0), world.p(1), world.eq, Decimal("10")),
                    NewDebt(world.p(0), world.p(2), world.eq, Decimal("20")),
                ],
            )
            await session.commit()
            first = (
                await session.execute(
                    select(Debt.id).where(Debt.debtor_id == world.p(0), Debt.creditor_id == world.p(1))
                )
            ).scalar_one()
            second = (
                await session.execute(
                    select(Debt.id).where(Debt.debtor_id == world.p(0), Debt.creditor_id == world.p(2))
                )
            ).scalar_one()
            await session.commit()

            identity, op = _fixture("inner-savepoint")
            async with Book.operation(session, op):
                (await session.get(Debt, first)).amount = Decimal("11")
                await session.flush()
                nested = await session.begin_nested()
                (await session.get(Debt, second)).amount = Decimal("21")
                await session.flush()
                await nested.rollback()
            await session.commit()

        async with engine.connect() as observer:
            (envelope,) = await envelopes_named(observer, identity)
            entries = await entries_of(observer, envelope.id)
            assert envelope.state == "COMPLETED", envelope
            assert envelope.effect_count == 1, envelope
            assert [(e.effect, e.delta) for e in entries] == [("U", Decimal("1"))], entries
            assert await debt_amount(observer, world.p(0), world.p(1), world.eq) == Decimal("11")
            assert await debt_amount(observer, world.p(0), world.p(2), world.eq) == Decimal("20")
            await _journal_explains_the_edge(observer, world, world.p(0), world.p(2))
    finally:
        await engine.dispose()
