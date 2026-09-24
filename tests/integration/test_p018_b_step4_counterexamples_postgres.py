"""018 stage B1: the step-4 counterexamples of programme 015 (`C1`-`C21`), against the database journal.

WHAT THIS MODULE IS. The listener journal (`app/core/ledger/journal.py`) had a family of counterexample
modules - `test_p015_b4_write_guard.py`, `test_p015_b4_transaction_contract{,_postgres}.py`,
`test_p015_b4a_journal_mechanism.py`, `test_p015_b4a_journal_postgres.py`,
`test_p015_b4_r4_fixture_migration_is_observably_equivalent.py` - and stage B1 deleted them with the
listener. Their assertions did not go with them: the manifest `T1808` (sections 5 and 6) maps each one
to a surviving test, a stage-B test written in part i (`tests/integration/test_p018_*`), or a test here;
the rows it DROPS name the listener-internal contract they checked. What lives here is the rest, grouped
by observable behaviour rather than one function per old assertion:

* NEW-A - an operation is one envelope of its kind with its entries (`C4` shape, `schema_version = 2`).
* NEW-B - every DML door into `debts` without an operation is `GE001`, and the same door inside an
  operation lands (`C1`, `C2`, `C2-P`, `C9-P`).
* NEW-C - a writer the application did not plan, running INSIDE an open operation, is stored AND
  journalled exactly as the row it wrote (condition 3, `C3`, `T1527`). These are the INVERTED
  acceptances spec 018 `FORK-2` accepts: the listener refused them; the trigger records them from
  `OLD`/`NEW`, and a wrong writer with an honest record is criterion (b)'s to catch
  (`tests/unit/test_p015_step5b_criterion_b.py::test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind`).
* `GE002` through the ORM's relationship and expired-attribute forms (`T1527`).
* NEW-D - the book's refusals before any write that part i does not already hold: an out-of-scope
  effect takes its in-scope sibling with it (`C20`), an application operation inside a fixture one
  (`C21`), AUTOCOMMIT with its premises measured (condition 2).
* NEW-E - a failure inside one operation leaves nothing of it and its sibling commits (`C11`, the
  accepted flip), including a prevented root rollback afterwards (`T1527-P`).
* NEW-F - a rolled-back operation leaves nothing and its identity reopens on the same connection (`C7`).
* NEW-H - full-width money is stored and journalled exactly.
* NEW-L - a swallowed `GE001` leaves nothing durable, and the session recovers (`C1`).
* R4 - a refused write in a `debt_fixture_setup` block surfaces at the block's exit.

THE STAND. One clone of the MIGRATED template for the module (`tests/p018_support.module_clone`): every
trigger under test is the migration's. Tests commit for real (the deferred check, other sessions and
backends reading), and each one reads only its own world's rows; the clone is dropped when the module
ends.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, insert, literal, select, text, update
from sqlalchemy.exc import DBAPIError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.ledger.book import Book, BookError, NewDebt, Refusal, operation_for
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.debt_setup import debt_fixture_setup, writer_operation
from tests.p018_support import (
    GE001,
    GE002,
    entries_of,
    envelopes_named,
    module_clone,
    seed_world,
    serializable_engine,
    sqlstate_of,
)

OPERATIONS = "debt_operations"


@pytest.fixture(scope="module")
def migrated_url():
    with module_clone("p018bstep4") as url:
        yield url


@pytest_asyncio.fixture
async def engine(migrated_url):
    built = serializable_engine(migrated_url)
    try:
        yield built
    finally:
        await built.dispose()


def _session(engine) -> AsyncSession:
    return AsyncSession(bind=engine, expire_on_commit=False, autoflush=False)


def _fixture(identity: str, **kwargs):
    return operation_for("TEST_FIXTURE", identity, {"step4": identity}, **kwargs)


def _identity(name: str) -> str:
    return f"p018-step4/{name}/{uuid.uuid4().hex[:12]}"


async def _world(engine, participants: int = 3):
    async with _session(engine) as session:
        world = await seed_world(session, participants=participants, label="step4")
        await session.commit()
    return world


async def _debts(engine, world) -> dict[tuple[int, int], Decimal]:
    """Every debt of this world's equivalent, keyed by participant index, read on a NEW connection."""

    index = {participant.id: number for number, participant in enumerate(world.participants)}
    async with engine.connect() as observer:
        rows = (
            await observer.execute(
                text("SELECT debtor_id, creditor_id, amount FROM debts WHERE equivalent_id = :eq"),
                {"eq": world.eq},
            )
        ).all()
    return {(index[d], index[c]): Decimal(a) for d, c, a in rows}


async def _envelopes(engine, identity: str):
    async with engine.connect() as observer:
        return await envelopes_named(observer, identity)


async def _entries(engine, identity: str):
    async with engine.connect() as observer:
        [envelope] = await envelopes_named(observer, identity)
        return envelope, await entries_of(observer, envelope.id)


async def _seed_debt(engine, world, debtor: int, creditor: int, amount: str) -> uuid.UUID:
    async with _session(engine) as session:
        await Book.post(
            session,
            _fixture(_identity("seed")),
            [NewDebt(world.p(debtor), world.p(creditor), world.eq, Decimal(amount))],
        )
        debt_id = (
            await session.execute(
                select(Debt.id).where(
                    Debt.debtor_id == world.p(debtor), Debt.creditor_id == world.p(creditor)
                )
            )
        ).scalar_one()
        await session.commit()
    return debt_id


def _values(world, debtor: int, creditor: int, amount: str) -> dict:
    return {
        "id": uuid.uuid4(),
        "debtor_id": world.p(debtor),
        "creditor_id": world.p(creditor),
        "equivalent_id": world.eq,
        "amount": Decimal(amount),
        "version": 0,
    }


# =================================================================================================
# NEW-A - one envelope of its kind, with its entries
# =================================================================================================


@pytest.mark.asyncio
async def test_a_fixture_operation_is_one_envelope_of_its_kind_with_its_entries(engine) -> None:
    """NEW-A (manifest `T1808` §5, `b4a_journal_mechanism` rows `:96-113`).

    The row values, `schema_version = 2`, the membership row and `effect_count` by rows are held by
    `test_p018_b_book_transaction_contract_postgres.py::test_a_fresh_session_opens_completes_and_records_one_entry_per_row`;
    this adds what that test does not read: the KIND, `tx_id` NULL for a kind without a transaction,
    the digest, and the chain `I 10` then `U 10 -> 12` with its `ordinal` increasing.

    MUTATION: write `effect_count` from `ordinal` arithmetic (last minus first plus one) in
    `app/core/ledger/book.py::_complete` - the write rolled back in a savepoint below consumes an
    ordinal and leaves no entry, so the surviving ordinals have a gap and the formula gives 3, not 2
    (measured 2026-09-24); or record `TG_OP` as the effect of the wrong branch in
    `app/db/journal_triggers.py` - the effects assertion goes red.
    """

    world = await _world(engine)
    identity = _identity("new-a")
    async with _session(engine) as session:
        async with Book.operation(session, _fixture(identity)):
            debt = Debt(**_values(world, 0, 1, "10"))
            session.add(debt)
            await session.flush()
            # A REAL GAP between the two surviving entries (Codex review of B1, 2026-09-24, P3): a
            # write inside a savepoint that is rolled back consumes a sequence value and leaves no
            # entry. Without it the two kept entries have consecutive ordinals, and the wrong formula
            # `last - first + 1` would also give 2.
            connection = await session.connection()
            savepoint = await connection.begin_nested()
            await connection.execute(
                text("UPDATE debts SET amount = 11 WHERE id = :id"), {"id": debt.id}
            )
            await savepoint.rollback()
            debt.amount = Decimal("12")
            await session.flush()
        await session.commit()

    async with engine.connect() as observer:
        envelope = (
            await observer.execute(
                text(
                    "SELECT kind, tx_id, state, effect_count, length(effect_digest), schema_version "
                    "FROM debt_operations WHERE identity = :i"
                ),
                {"i": identity},
            )
        ).one()
    assert tuple(envelope) == ("TEST_FIXTURE", None, "COMPLETED", 2, 64, 2), envelope
    _, entries = await _entries(engine, identity)
    assert [(row.effect, row.amount_before, row.amount_after, row.delta) for row in entries] == [
        ("I", None, Decimal("10.00000000"), Decimal("10.00000000")),
        ("U", Decimal("10.00000000"), Decimal("12.00000000"), Decimal("2.00000000")),
    ]
    ordinals = [row.ordinal for row in entries]
    assert ordinals == sorted(set(ordinals)), ordinals
    # The premise of the effect_count assertion above: the surviving ordinals are NOT contiguous, so
    # `effect_count == 2` is a count of rows and could not come from ordinal arithmetic (which gives 3).
    assert ordinals[-1] - ordinals[0] + 1 > len(entries), ordinals


# =================================================================================================
# NEW-B - every DML door: refused without an operation, landing inside one
# =================================================================================================

#: `(form, needs an existing debt)`. Each builds its statement(s) on the session it is given.
_DOORS = [
    ("orm insert", False),
    ("orm update", True),
    ("orm delete", True),
    ("core insert", False),
    ("core update", True),
    ("core delete", True),
    ("orm batched insert", False),
    ("legacy bulk_save_objects", False),
    ("legacy bulk_insert_mappings", False),
    ("legacy bulk_update_mappings", True),
    ("core dml on the session connection", False),
    ("text() expression update", True),
    ("select with a writing cte", False),
    ("select from an updating cte", True),
    ("insert from select with a dml cte", False),
]


async def _through(form: str, session: AsyncSession, world, existing) -> None:
    debt_id = existing
    if form == "orm insert":
        session.add(Debt(**_values(world, 1, 2, "13")))
        await session.flush()
    elif form == "orm update":
        debt = await session.get(Debt, debt_id)
        debt.amount = Decimal("17")
        await session.flush()
    elif form == "orm delete":
        await session.delete(await session.get(Debt, debt_id))
        await session.flush()
    elif form == "core insert":
        await session.execute(insert(Debt).values(**_values(world, 1, 2, "13")))
    elif form == "core update":
        await session.execute(update(Debt).where(Debt.id == debt_id).values(amount=Decimal("17")))
    elif form == "core delete":
        await session.execute(delete(Debt).where(Debt.id == debt_id))
    elif form == "orm batched insert":
        await session.execute(insert(Debt), [_values(world, 1, 2, "14"), _values(world, 2, 3, "16")])
    elif form == "legacy bulk_save_objects":
        row = Debt(**_values(world, 1, 2, "18"))
        await session.run_sync(lambda s: s.bulk_save_objects([row]))
    elif form == "legacy bulk_insert_mappings":
        values = _values(world, 1, 2, "19")
        await session.run_sync(lambda s: s.bulk_insert_mappings(Debt, [values]))
    elif form == "legacy bulk_update_mappings":
        version = (
            await session.execute(select(Debt.version).where(Debt.id == debt_id))
        ).scalar_one()
        await session.run_sync(
            lambda s: s.bulk_update_mappings(
                Debt, [{"id": debt_id, "amount": Decimal("22"), "version": version}]
            )
        )
    elif form == "core dml on the session connection":
        connection = await session.connection()
        await connection.execute(Debt.__table__.insert().values(**_values(world, 1, 2, "23")))
    elif form == "text() expression update":
        await session.execute(
            text("UPDATE debts SET amount = amount + 1 WHERE id = :id"), {"id": debt_id}
        )
    elif form == "select with a writing cte":
        cte = insert(Debt).values(**_values(world, 1, 2, "41")).returning(Debt.id).cte("writer")
        await session.execute(select(cte.c.id).add_cte(cte))
    elif form == "select from an updating cte":
        cte = (
            update(Debt)
            .where(Debt.id == debt_id)
            .values(amount=Decimal("66"))
            .returning(Debt.id)
            .cte("updater")
        )
        await session.execute(select(cte.c.id))
    elif form == "insert from select with a dml cte":
        cte = insert(Debt).values(**_values(world, 1, 2, "43")).returning(Debt.amount).cte("hidden")
        columns = Debt.__table__.c
        await session.execute(
            insert(Debt).from_select(
                ["id", "debtor_id", "creditor_id", "equivalent_id", "amount", "version"],
                select(
                    literal(uuid.uuid4(), columns.id.type),
                    literal(world.p(2), columns.debtor_id.type),
                    literal(world.p(3), columns.creditor_id.type),
                    literal(world.eq, columns.equivalent_id.type),
                    cte.c.amount,
                    literal(0, columns.version.type),
                ).add_cte(cte),
            )
        )
    else:  # pragma: no cover - a typo in the parametrisation must not pass silently
        raise AssertionError(f"unknown door {form!r}")


@pytest.mark.parametrize("form,needs_existing", _DOORS, ids=[row[0] for row in _DOORS])
@pytest.mark.asyncio
async def test_every_dml_door_without_an_operation_is_refused_by_the_database(
    engine, form, needs_existing
) -> None:
    """NEW-B, the refusal half (`C1`, `C2`, `C2-P`; `b4a` `:579-582`, `:601`, `:756-759`, `:919-920`,
    `:263-268`; `write_guard` `:203-214`, `:291`; `transaction_contract` `:279`, PG `:201-211`).

    The listener refused these doors one by one - the flush plan, the statement classifier, the CTE
    walk - and named `exec_driver_sql` as its blind spot. The `debts` trigger does not care who built
    the SQL: every one of them is `GE001` from the database, and `debts` is unchanged. Raw
    `exec_driver_sql` itself is `test_p018_a_write_without_context_is_refused_by_the_database.py`.

    MUTATION: in `app/db/journal_triggers.py::_DEBTS_JOURNAL` let a NULL context through (return NEW
    instead of RAISE) - every form here goes red on the SQLSTATE.
    """

    world = await _world(engine, participants=4)
    existing = await _seed_debt(engine, world, 0, 1, "5") if needs_existing else None
    before = await _debts(engine, world)

    async with _session(engine) as session:
        with pytest.raises(DBAPIError) as caught:
            await _through(form, session, world, existing)
            await session.commit()
        await session.rollback()

    assert sqlstate_of(caught.value) == GE001, (form, caught.value)
    assert await _debts(engine, world) == before, form


@pytest.mark.parametrize("form,needs_existing", _DOORS, ids=[row[0] for row in _DOORS])
@pytest.mark.asyncio
async def test_every_dml_door_inside_an_operation_lands_and_is_journalled(
    engine, form, needs_existing
) -> None:
    """NEW-B, the control half (`C1` `:267-272`, `C2` anti-vacuum): the refusal above is about the
    missing operation and nothing else - the same statement inside one lands, and the operation's
    entries account for exactly the change (criterion (a) over this operation).
    """

    world = await _world(engine, participants=4)
    existing = await _seed_debt(engine, world, 0, 1, "5") if needs_existing else None
    before = await _debts(engine, world)
    identity = _identity(f"door-{form}")

    async with _session(engine) as session:
        async with Book.operation(session, _fixture(identity)):
            await _through(form, session, world, existing)
        await session.commit()

    after = await _debts(engine, world)
    assert after != before, f"stand: `{form}` inside an operation changed nothing"
    _, entries = await _entries(engine, identity)
    index = {participant.id: number for number, participant in enumerate(world.participants)}
    journalled: dict[tuple[int, int], Decimal] = {}
    for row in entries:
        edge = (index[row.debtor_id], index[row.creditor_id])
        journalled[edge] = journalled.get(edge, Decimal(0)) + row.delta
    changed = {
        edge: after.get(edge, Decimal(0)) - before.get(edge, Decimal(0))
        for edge in set(before) | set(after)
        if after.get(edge, Decimal(0)) != before.get(edge, Decimal(0))
    }
    assert journalled == changed, (form, journalled, changed)


@pytest.mark.asyncio
async def test_a_read_only_cte_is_no_write(engine) -> None:
    """NEW-B control (`b4a_journal_postgres` `:267`): a SELECT with a READ-ONLY cte is no write."""

    world = await _world(engine)
    await _seed_debt(engine, world, 0, 1, "5")
    async with _session(engine) as session:
        reader = select(Debt.id).where(Debt.equivalent_id == world.eq).cte("reader")
        rows = (await session.execute(select(reader.c.id))).all()
        await session.rollback()
    assert len(rows) == 1, rows


@pytest.mark.asyncio
async def test_c9_an_open_operation_does_not_cover_an_independent_transactions_write(
    engine,
) -> None:
    """NEW-B, independent transaction (`C9-P` `:815-825`; `write_guard` "core dml on an engine
    connection"): while A's operation is OPEN, B on another backend writes `debts` - `GE001`, and only
    A's covered write is durable. The context is transaction-local, so it cannot reach B.
    """

    world = await _world(engine, participants=4)
    identity = _identity("c9-a")
    async with _session(engine) as a:
        async with Book.operation(a, _fixture(identity)):
            a.add(Debt(**_values(world, 0, 1, "8")))
            await a.flush()
            with pytest.raises(DBAPIError) as caught:
                async with engine.begin() as b:
                    await b.execute(Debt.__table__.insert().values(**_values(world, 2, 3, "24")))
        await a.commit()

    assert sqlstate_of(caught.value) == GE001, caught.value
    assert await _debts(engine, world) == {(0, 1): Decimal("8")}
    assert [row.state for row in await _envelopes(engine, identity)] == ["COMPLETED"]


# =================================================================================================
# NEW-C - a writer the application did not plan is recorded as the row it wrote
# =================================================================================================

_UNPLANNED = [
    "a late before_flush listener changes the amount",
    "an after_flush listener issues core dml",
    "a pending debt keyed only through relationships",
    "a late before_flush listener moves the insert to another edge",
    "a relationship that contradicts the key column",
]


@pytest.mark.parametrize("form", _UNPLANNED)
@pytest.mark.asyncio
async def test_an_unplanned_writer_inside_an_operation_is_recorded_as_the_row_it_wrote(
    engine, form
) -> None:
    """NEW-C (condition 3 `b4a` `:391-457`, `write_guard` `:559-631`; `C3` `:481`; `T1527` `:850-862`,
    PG `:297-309`). THE INVERTED ACCEPTANCES, spec 018 `FORK-2` / manifest §3 item 2.

    Under the listener each of these was REFUSED: the journal recorded one thing from the ORM's flush
    plan and the statement wrote another, so the write could not be verified. The trigger writes the
    entry FROM THE ROW - `OLD`/`NEW` of the very statement - so the record and the row cannot differ:
    what the neighbour wrote is durable AND journalled, exactly. That the neighbour wrote the WRONG
    thing is no longer the journal's to say; criterion (b) replays the operation's intent and refuses
    it (`test_p015_step5b_criterion_b.py::test_step5b_the_c6_wrong_route_is_failed_by_b_while_a_stays_blind`).

    Asserted for every form: the neighbour really acted (premise), and for every edge the entries'
    deltas equal the stored change (criterion (a) over this operation) - including the edge the
    caller never named.

    MUTATION: in `_DEBTS_JOURNAL` record `NEW.creditor_id` as `OLD.creditor_id`'s place on INSERT
    (any edge not from the row), or record the amount from a constant - the edge-move and
    amount forms go red.
    """

    world = await _world(engine, participants=4)
    identity = _identity(f"unplanned-{form}")
    acted: list[str] = []
    async with _session(engine) as session:
        if form == "a late before_flush listener changes the amount":

            @event.listens_for(session.sync_session, "before_flush")
            def _late(sync_session, _context, _instances) -> None:
                for obj in list(sync_session.new):
                    if isinstance(obj, Debt) and obj.amount != Decimal("31"):
                        obj.amount = Decimal("31")
                        acted.append("amount")

            async with Book.operation(session, _fixture(identity)):
                session.add(Debt(**_values(world, 0, 1, "30")))
                await session.flush()
            expected = {(0, 1): Decimal("31")}
        elif form == "an after_flush listener issues core dml":

            @event.listens_for(session.sync_session, "after_flush")
            def _neighbour(sync_session, _context) -> None:
                if acted:
                    return
                acted.append("after_flush")
                sync_session.execute(insert(Debt).values(**_values(world, 0, 2, "77")))
                sync_session.connection().execute(
                    Debt.__table__.update()
                    .where(Debt.creditor_id == world.p(1), Debt.equivalent_id == world.eq)
                    .values(amount=Decimal("99"))
                )

            async with Book.operation(session, _fixture(identity)):
                session.add(Debt(**_values(world, 0, 1, "20")))
                await session.flush()
            expected = {(0, 1): Decimal("99"), (0, 2): Decimal("77")}
        elif form == "a pending debt keyed only through relationships":
            debtor = await session.get(Participant, world.p(0))
            creditor = await session.get(Participant, world.p(1))
            equivalent = await session.get(Equivalent, world.eq)
            debt = Debt(
                id=uuid.uuid4(),
                debtor=debtor,
                creditor=creditor,
                equivalent=equivalent,
                amount=Decimal("27"),
                version=0,
            )
            acted.append(repr((debt.debtor_id, debt.creditor_id, debt.equivalent_id)))
            async with Book.operation(session, _fixture(identity)):
                session.add(debt)
                await session.flush()
            expected = {(0, 1): Decimal("27")}
        elif form == "a relationship that contradicts the key column":
            elsewhere = await session.get(Participant, world.p(3))
            debt = Debt(**_values(world, 0, 1, "44"), creditor=elsewhere)
            acted.append("relationship")
            async with Book.operation(session, _fixture(identity)):
                session.add(debt)
                await session.flush()
            expected = {(0, 3): Decimal("44")}
        else:

            @event.listens_for(session.sync_session, "before_flush")
            def _mover(sync_session, _context, _instances) -> None:
                for obj in list(sync_session.new):
                    if isinstance(obj, Debt) and obj.creditor_id != world.p(3):
                        obj.creditor_id = world.p(3)
                        acted.append("edge")

            async with Book.operation(session, _fixture(identity)):
                session.add(Debt(**_values(world, 0, 1, "43")))
                await session.flush()
            expected = {(0, 3): Decimal("43")}
        await session.commit()

    assert acted, f"stand: `{form}` never acted"
    if form == "a pending debt keyed only through relationships":
        # The listener's premise: at construction the key columns are all None.
        assert acted == [repr((None, None, None))], acted
    stored = await _debts(engine, world)
    assert stored == expected, (form, stored)
    envelope, entries = await _entries(engine, identity)
    assert envelope.state == "COMPLETED"
    index = {participant.id: number for number, participant in enumerate(world.participants)}
    journalled: dict[tuple[int, int], Decimal] = {}
    for row in entries:
        edge = (index[row.debtor_id], index[row.creditor_id])
        journalled[edge] = journalled.get(edge, Decimal(0)) + row.delta
    assert journalled == stored, (form, journalled, stored)


@pytest.mark.parametrize("form", ["a relationship assignment", "an expired key attribute"])
@pytest.mark.asyncio
async def test_t1527_a_key_change_through_the_orm_is_refused_as_ge002(engine, form) -> None:
    """`T1527` edge moves through the ORM (`write_guard` `:932-946`, `:1032-1040`), inside a valid
    operation so the answer cannot be `GE001`. The trigger compares `OLD` with `NEW`, not the ORM's
    attribute history, so neither a relationship nor an expired attribute hides the move. The raw and
    attribute-swap forms are `test_p018_a_write_without_context_*::test_t1803_moving_a_stored_debt_*`.
    """

    world = await _world(engine)
    debt_id = await _seed_debt(engine, world, 0, 1, "50")
    identity = _identity(f"ge002-{form}")
    async with _session(engine) as session:
        with pytest.raises(DBAPIError) as caught:
            async with Book.operation(session, _fixture(identity)):
                debt = await session.get(Debt, debt_id)
                if form == "a relationship assignment":
                    debt.creditor = await session.get(Participant, world.p(2))
                else:
                    session.expire(debt, ["creditor_id"])
                    debt.creditor_id = world.p(2)
                await session.flush()
        await session.rollback()

    assert sqlstate_of(caught.value) == GE002, caught.value
    assert await _debts(engine, world) == {(0, 1): Decimal("50")}
    assert await _envelopes(engine, identity) == []


# =================================================================================================
# NEW-D - refusals before any write
# =================================================================================================


@pytest.mark.asyncio
async def test_c20_an_out_of_scope_effect_takes_its_in_scope_sibling_with_it(engine) -> None:
    """NEW-D, `C20` (`write_guard` `:1247-1259`): an operation that applied an in-scope effect and
    is then handed one outside its declared scope is refused (`OUT_OF_SCOPE`) - and BOTH effects are
    gone, because the book rolls its own savepoint back (the listener poisoned the root instead).
    """

    world = await _world(engine)
    other = await _world(engine, participants=0)
    async with _session(engine) as session:
        with pytest.raises(BookError) as caught:
            async with Book.operation(
                session, _fixture(_identity("c20"), scope_equivalent_ids={world.eq})
            ) as posting:
                await posting.apply(NewDebt(world.p(0), world.p(1), world.eq, Decimal("5")))
                await posting.apply(NewDebt(world.p(0), world.p(1), other.eq, Decimal("6")))
        await session.commit()

    assert caught.value.reason == Refusal.OUT_OF_SCOPE
    assert await _debts(engine, world) == {}
    assert await _debts(engine, other) == {}


@pytest.mark.asyncio
async def test_c21_an_application_operation_inside_a_fixture_operation_is_refused(engine) -> None:
    """NEW-D, `C21` runtime half (`write_guard` `:1457`): a PAYMENT operation opened inside a
    `debt_fixture_setup` block is refused as nesting, and nothing of either is durable once the block
    ends with that refusal.
    """

    world = await _world(engine)
    async with _session(engine) as session:
        with pytest.raises(BookError) as caught:
            # The outer block is `Book.operation` with the fixture kind rather than
            # `debt_fixture_setup` itself: the static half of `C21` would (rightly) flag an
            # `async with` inside a fixture block, and it is the RUNTIME half that is measured here.
            async with Book.operation(session, _fixture(_identity("c21-outer"))):
                session.add(Debt(**_values(world, 0, 1, "7")))
                async with writer_operation(
                    session, kind="PAYMENT", equivalent_ids={world.eq}, initiator_id=world.p(0)
                ):
                    pass
        await session.rollback()

    assert caught.value.reason == Refusal.NESTED_OPERATION
    assert await _debts(engine, world) == {}


@pytest.mark.asyncio
async def test_condition2_an_autocommit_root_is_refused_before_the_operation_opens(
    migrated_url, engine
) -> None:
    """NEW-D, condition 2 (`transaction_contract_postgres` `:1228-1275`; `b4a_journal_postgres`
    `:157-177`), with the PREMISES measured: SQLAlchemy's connection options are blind to an
    engine-level AUTOCOMMIT, the dialect knows it, and a row really does survive a "rollback" on such
    a connection. The refusal itself (and the driver-only form) is also held by
    `test_p018_b_book_transaction_contract_postgres.py::test_autocommit_is_refused_before_any_write_and_the_driver_check_stands_alone`.
    """

    world = await _world(engine)
    autocommit = create_async_engine(migrated_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    probe_code = f"AC{uuid.uuid4().hex[:6]}".upper()
    try:
        async with autocommit.connect() as connection:
            sync = connection.sync_connection
            assert sync._execution_options.get("isolation_level") is None
            assert getattr(sync.engine.dialect, "_on_connect_isolation_level", None) == "AUTOCOMMIT"
            probe = AsyncSession(bind=connection, autoflush=False)
            probe.add(Equivalent(code=probe_code, precision=2, is_active=True))
            await probe.flush()
            await probe.rollback()
            await probe.close()
        async with engine.connect() as observer:
            survived = (
                await observer.execute(
                    select(Equivalent.id).where(Equivalent.code == probe_code)
                )
            ).scalar_one_or_none()
        assert survived is not None, "stand: the connection is not AUTOCOMMIT after all"

        identity = _identity("autocommit")
        async with AsyncSession(bind=autocommit, expire_on_commit=False, autoflush=False) as session:
            with pytest.raises(BookError) as caught:
                await Book.post(
                    session,
                    _fixture(identity),
                    [NewDebt(world.p(0), world.p(1), world.eq, Decimal("1"))],
                )
        assert caught.value.reason == Refusal.AUTOCOMMIT_ROOT
        assert await _envelopes(engine, identity) == []
        assert await _debts(engine, world) == {}
    finally:
        await autocommit.dispose()


# =================================================================================================
# NEW-E - a failure inside one operation leaves nothing of it; the sibling commits
# =================================================================================================


class _BusinessFailure(Exception):
    pass


#: `(label, where it fails, what it raises)`. `insert`/`update` inject the failure into the book's own
#: statement against `debt_operations` (the envelope INSERT at open, the COMPLETED UPDATE at the end);
#: `body` raises it from the caller's block.
_FAILURES = [
    ("the envelope INSERT at open", "insert", RuntimeError),
    ("the completion UPDATE", "update", RuntimeError),
    ("a cancellation during the completion UPDATE", "update", asyncio.CancelledError),
    ("a business rejection in the body", "body", _BusinessFailure),
    ("a cancellation in the body", "body", asyncio.CancelledError),
]


@pytest.mark.parametrize("label,where,raises", _FAILURES, ids=[row[0] for row in _FAILURES])
@pytest.mark.asyncio
async def test_c11_a_failure_inside_one_operation_leaves_nothing_and_its_sibling_commits(
    engine, label, where, raises
) -> None:
    """NEW-E, `C11` (`transaction_contract` `:1209-1223`, `:1327-1363`, `:1456-1473`; PG `:929-944`,
    `:1035-1061`, `:1140-1163`). THE ACCEPTED FLIP (spec 018 `FORK-2`, row `C11`).

    The listener POISONED THE ROOT on a failure in its own I/O: nothing of the transaction could
    commit, the sibling included. The book rolls ITS savepoint back and re-raises the ORIGINAL
    exception; the savepoint rollback is CONFIRMED (it did not fail), so the sibling's commit is
    accepted - with its envelope and its entries - and nothing of the failed operation is durable:
    no envelope, no entry, no debt. A failed or undetermined cleanup is the opposite case and is
    `test_p018_b_book_transaction_contract_postgres.py::test_fork2_a_failed_rollback_after_completed_makes_the_transaction_unusable`.

    MUTATION: in `app/core/ledger/book.py::Book.operation` swallow the exception after `_abandon`
    (drop the `raise`) - the "reached the caller" assertion goes red; or skip `_abandon` for
    `BaseException` that is not an `Exception` - the cancellation cases leave the failed
    operation's rows behind (the injection cases then fail on the deferred check at commit).
    """

    world = await _world(engine, participants=4)
    sibling, failed = _identity("c11-sibling"), _identity("c11-failed")
    fired: list[str] = []
    message = f"p018 step4: {label}"

    def _fail_the_envelope_statement(_conn, clauseelement, _multi, _params, _options) -> None:
        table = getattr(getattr(clauseelement, "table", None), "name", None)
        if table != OPERATIONS or not getattr(clauseelement, f"is_{where}", False):
            return
        # Registered only after the sibling has completed, so the first matching statement is the
        # failed operation's own.
        fired.append(where)
        raise raises(message)

    reached: BaseException | None = None
    async with _session(engine) as session:
        await Book.post(
            session, _fixture(sibling), [NewDebt(world.p(0), world.p(1), world.eq, Decimal("12"))]
        )
        if where != "body":
            event.listen(engine.sync_engine, "before_execute", _fail_the_envelope_statement)
        try:
            async with Book.operation(session, _fixture(failed)) as posting:
                await posting.apply(NewDebt(world.p(2), world.p(3), world.eq, Decimal("28")))
                await session.flush()
                if where == "body":
                    fired.append(where)
                    raise raises(message)
        except BaseException as exc:  # noqa: BLE001 - CancelledError is one of the cases
            reached = exc
        finally:
            if where != "body":
                event.remove(engine.sync_engine, "before_execute", _fail_the_envelope_statement)
        await session.commit()

    assert fired == [where], f"stand: the failure was never injected ({fired})"
    assert isinstance(reached, raises) and message in str(reached), reached
    assert await _debts(engine, world) == {(0, 1): Decimal("12")}
    assert [row.state for row in await _envelopes(engine, sibling)] == ["COMPLETED"]
    _, sibling_entries = await _entries(engine, sibling)
    assert [(row.effect, row.delta) for row in sibling_entries] == [("I", Decimal("12.00000000"))]
    assert await _envelopes(engine, failed) == []


class _PreventedTheRollback(BaseException):
    """What a neighbouring `rollback` listener raises. Stands for any listener that fails there."""


@pytest.mark.asyncio
async def test_t1527_a_prevented_root_rollback_after_a_failed_operation_commits_nothing_of_it(
    engine,
) -> None:
    """NEW-E, `T1527-P` (`transaction_contract_postgres` `:477-499`). The listener's finding 23: a
    neighbour that raises in the root's `rollback` leaves SQLAlchemy's root detached while the DRIVER
    still holds the transaction, and a second root born over it commits whatever is inside.

    The premises are measured exactly as before (the rollback was prevented, the root detached, the
    driver still in a transaction). What changed is WHY nothing of the abandoned operation survives:
    the book rolled its own savepoint back when the block failed, before the caller's rollback was
    ever attempted - so the second root commits, and there is nothing of that operation in it. The
    listener's `begin` refusal (the driver probe) is DROPPED with it (manifest rows `:492`).
    """

    world = await _world(engine)
    identity = _identity("t1527-prevented")
    prevented = None
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False, autoflush=False)
        with pytest.raises(_BusinessFailure):
            async with Book.operation(session, _fixture(identity)):
                session.add(Debt(**_values(world, 0, 1, "71")))
                await session.flush()
                raise _BusinessFailure()

        sync_connection = connection.sync_connection

        def _neighbour(_conn) -> None:
            raise _PreventedTheRollback("a neighbour prevented the root rollback")

        event.listen(sync_connection, "rollback", _neighbour)
        try:
            await transaction.rollback()
        except BaseException as exc:  # noqa: BLE001 - the prevented rollback is the premise
            prevented = exc
        finally:
            event.remove(sync_connection, "rollback", _neighbour)
        root_after = sync_connection.get_transaction()
        driver_in_transaction = sync_connection.connection.driver_connection.is_in_transaction()

        second = await connection.begin()
        await second.commit()

    assert isinstance(prevented, _PreventedTheRollback), prevented
    assert root_after is None
    assert driver_in_transaction is True
    assert await _debts(engine, world) == {}
    assert await _envelopes(engine, identity) == []


# =================================================================================================
# NEW-F - a rolled-back operation leaves nothing, and its identity reopens
# =================================================================================================


@pytest.mark.asyncio
async def test_c7_a_rolled_back_operation_leaves_nothing_and_the_identity_reopens_on_the_same_connection(
    engine,
) -> None:
    """NEW-F (`b4a` `:143-155`; `transaction_contract` `:572-583`, PG `:758-766`; `T1527-P` control
    `:560-566`): an operation whose transaction is rolled back leaves no envelope, entry or debt;
    the SAME identity then opens again on the SAME connection, completes and commits - one COMPLETED
    envelope, whose entries are the second attempt's only.

    MUTATION: in `_MUST_COMPLETE` refuse any envelope at commit - the second commit goes red; or keep
    the rolled-back attempt's envelope (e.g. write it outside the book's savepoint on its own
    connection) - the reopen hits `uq_debt_operations_kind_identity`.
    """

    world = await _world(engine)
    identity = _identity("c7")
    async with engine.connect() as connection:
        first = await connection.begin()
        pid = (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        session = AsyncSession(bind=connection, expire_on_commit=False, autoflush=False)
        async with Book.operation(session, _fixture(identity)):
            session.add(Debt(**_values(world, 0, 1, "73")))
            await session.flush()
        await session.close()
        await first.rollback()

        second = await connection.begin()
        again = AsyncSession(bind=connection, expire_on_commit=False, autoflush=False)
        async with Book.operation(again, _fixture(identity)):
            again.add(Debt(**_values(world, 0, 2, "74")))
            await again.flush()
        await again.close()
        await second.commit()
        assert (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one() == pid

    assert await _debts(engine, world) == {(0, 2): Decimal("74")}
    envelope, entries = await _entries(engine, identity)
    assert envelope.state == "COMPLETED"
    assert [(row.effect, row.delta) for row in entries] == [("I", Decimal("74.00000000"))]


# =================================================================================================
# NEW-H - full-width money
# =================================================================================================


@pytest.mark.asyncio
async def test_full_width_money_is_stored_and_journalled_exactly(engine) -> None:
    """NEW-H (`b4a` `:989-991`; `b4a_journal_postgres` `:113-115`): the largest amount `NUMERIC(20,8)`
    holds, a value SQLite once rounded, and a one-unit decrease of the full width - stored and
    journalled to the last atom. The trigger computes `delta` in `NUMERIC`, never through a float.
    The whole-life chain at full size is `test_p015_b4_entries_and_money_postgres.py::test_c4_p_*`.
    """

    world = await _world(engine)
    identity = _identity("full-width")
    full = Decimal("999999999999.99999999")
    odd = Decimal("100000000000.00000001")
    async with _session(engine) as session:
        async with Book.operation(session, _fixture(identity)) as posting:
            await posting.apply(NewDebt(world.p(0), world.p(1), world.eq, full))
            await posting.apply(NewDebt(world.p(1), world.p(2), world.eq, odd))
            await session.flush()
            debt = (
                await session.execute(
                    select(Debt).where(Debt.debtor_id == world.p(0), Debt.equivalent_id == world.eq)
                )
            ).scalar_one()
            debt.amount = Decimal("999999999998.99999999")
            await session.flush()
        await session.commit()

    assert await _debts(engine, world) == {
        (0, 1): Decimal("999999999998.99999999"),
        (1, 2): odd,
    }
    _, entries = await _entries(engine, identity)
    assert [(row.effect, row.amount_before, row.amount_after, row.delta) for row in entries] == [
        ("I", None, full, full),
        ("I", None, odd, odd),
        ("U", full, Decimal("999999999998.99999999"), Decimal("-1.00000000")),
    ]


# =================================================================================================
# NEW-L - a swallowed refusal leaves nothing durable
# =================================================================================================


@pytest.mark.asyncio
async def test_c1_a_swallowed_refusal_leaves_nothing_durable_and_the_session_recovers(
    engine,
) -> None:
    """NEW-L (`transaction_contract` `:337-348`; `b4a` `:617-618`). An earlier write of the same
    transaction completes inside an operation; then an undeclared ORM write is refused (`GE001`) and
    the caller SWALLOWS it and commits. Nothing of the transaction is durable - the earlier completed
    operation included - because PostgreSQL aborted the transaction at the refused statement.

    WHAT IS NOT ASSERTED, on purpose (manifest §3 item 12, C8): whether that COMMIT raises. What the
    driver returns for a COMMIT of an aborted transaction is not the property; durability is. After a
    rollback the same session runs an operation that commits.
    """

    world = await _world(engine, participants=4)
    earlier = _identity("c1-earlier")
    later = _identity("c1-after-rollback")
    async with _session(engine) as session:
        await Book.post(
            session, _fixture(earlier), [NewDebt(world.p(0), world.p(1), world.eq, Decimal("21"))]
        )
        written_inside = (
            await session.execute(select(Debt.amount).where(Debt.equivalent_id == world.eq))
        ).scalars().all()

        session.add(Debt(**_values(world, 0, 2, "34")))
        with pytest.raises(DBAPIError) as refused:
            await session.flush()
        try:
            await session.commit()
        except (DBAPIError, InvalidRequestError):
            pass
        await session.rollback()

        assert await _debts(engine, world) == {}
        assert await _envelopes(engine, earlier) == []

        await Book.post(
            session, _fixture(later), [NewDebt(world.p(2), world.p(3), world.eq, Decimal("41"))]
        )
        await session.commit()

    assert written_inside == [Decimal("21.00000000")], written_inside
    assert sqlstate_of(refused.value) == GE001, refused.value
    assert await _debts(engine, world) == {(2, 3): Decimal("41")}
    assert [row.state for row in await _envelopes(engine, later)] == ["COMPLETED"]


# =================================================================================================
# R4 - a refused write surfaces at the fixture block's exit
# =================================================================================================


@pytest.mark.asyncio
async def test_debt_fixture_setup_surfaces_a_refused_write_at_block_exit(engine) -> None:
    """R4's one live fact (`test_p015_b4_r4_*` `:880-886`, deleted by 018 stage B1).

    `debt_fixture_setup` never flushes (design v2 §8 R2); the book's completion does, inside its
    savepoint. So a duplicate-edge `Debt` added in the block is refused - `UNIQUE(debtor, creditor,
    equivalent)` - at the block's EXIT, not at the `add`, and no OPEN envelope and no entry of the
    block is left behind: the book rolled its savepoint back, and the transaction is usable.
    """

    world = await _world(engine)
    await _seed_debt(engine, world, 0, 1, "5")
    label = f"r4-duplicate-{uuid.uuid4().hex[:8]}"
    added: list[bool] = []
    # Built before the block: the static half of `C21` allows only construction and session calls
    # inside one.
    duplicate = Debt(**_values(world, 0, 1, "6"))
    async with _session(engine) as session:
        with pytest.raises(DBAPIError) as caught:
            async with debt_fixture_setup(session, label=label):
                session.add(duplicate)
                added = [True]
        await session.rollback()

    assert added == [True], "stand: the add itself raised; the refusal did not wait for the exit"
    assert sqlstate_of(caught.value) == "23505", caught.value
    assert await _debts(engine, world) == {(0, 1): Decimal("5")}
    async with engine.connect() as observer:
        left = (
            await observer.execute(
                text("SELECT count(*) FROM debt_operations WHERE identity LIKE :pattern"),
                {"pattern": f"%:{label}:%"},
            )
        ).scalar_one()
    assert left == 0
