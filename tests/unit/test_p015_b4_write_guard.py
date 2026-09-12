"""Programme 015, `B4` step 2: the write guard and the flush hook, as counterexamples.

WHAT THIS MODULE IS. The second half of the step-2 acceptance the spec bound to `B4` on 2026-09-11
(the first is `tests/unit/test_p015_b4_transaction_contract.py`). Here: design v2 §9 counterexamples
C2, C3, C20 and C21, plus binding condition 3 - "разрешение на запись - на проверенные записи, а не
на окно flush".

WHY THE GUARD EXISTS AT ALL. The journal derives every entry from the ORM's own flush plan: the
`before_flush` hook reads attribute history, `after_flush` writes the entries. Anything that changes
`debts` WITHOUT going through that plan produces no entry and is invisible to the journal - and the
ORM offers a great many such doors. Design v2 §6 closes them with a `before_execute` guard on the
engine that looks at every statement, including DML hidden inside a CTE, and lets a `debts` write
through only under a grant. Condition 3 is the round-2 reviewer's finding that the grant as designed
was a WINDOW (the whole flush) rather than a set of verified writes, so a second listener's DML slid
through it and became durable.

READ `tests/p015_b4_support.py` FIRST for how a counterexample stays red for its property rather
than for a missing import, and for the two kinds of red (DEFECT-SHAPED and API-SHAPED).

TIER. SQLite, default tier, money inside `|v| < 2^26`, every verdict read on a NEW session. The
PostgreSQL half of C2 - DML hidden inside a CTE, which SQLite cannot run at all - lives with the
rest of this slice's PostgreSQL work in
`tests/integration/test_p015_b4_transaction_contract_postgres.py`: one module, because a
PostgreSQL-marked module carries its own SERIALIZABLE engine and pool, and two of them would mean
two pools for six tests.

MARKER. This module carries `b4_counterexample` and is deselected from the canonical gate. It is
red on purpose until step 4 exists, and STEP 4 REMOVES THE MARKER, NOT THE ASSERTIONS - the full
contract is in the comment above the marker list in `pytest.ini`, and
`tests/unit/test_p015_b4_counterexample_marker_is_not_a_hiding_place.py` holds it in place.
"""

from __future__ import annotations

import textwrap
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, event, insert, select, update

from app.db.models.debt import Debt
from tests.p015_b4_support import (
    ENTRIES_TABLE,
    JOURNAL_MODULE,
    JOURNAL_TABLES,
    OPERATION_EQUIVALENTS_TABLE,
    OPERATIONS_TABLE,
    World,
    drop_world,
    exact_money,
    journal_api,
    missing_journal_tables,
    operation,
    refusal_of,
    seed_world,
    stored_debts,
    stored_rows,
)

pytestmark = pytest.mark.b4_counterexample


def _identity(name: str) -> str:
    return f"p015-b4/{name}/{uuid.uuid4().hex[:12]}"


async def _open(api, session, world: World, name: str, **kw):
    return operation(
        api,
        session,
        kind=kw.pop("kind", "TEST_FIXTURE"),
        identity=kw.pop("identity", _identity(name)),
        intent=kw.pop("intent", {"source": "p015-b4-counterexample", "name": name}),
        scope_equivalent_ids=kw.pop("scope_equivalent_ids", frozenset({world.equivalent.id})),
        **kw,
    )


async def _existing_debt(factory, world: World, amount: str) -> tuple[uuid.UUID, int]:
    """One committed debt of the world, plus its version - the subject of the U and D forms."""
    async with factory() as setup:
        debt = world.debt(amount)
        setup.add(debt)
        await setup.commit()
    async with factory() as fresh:
        row = (
            await fresh.execute(select(Debt.id, Debt.version).where(Debt.id == debt.id))
        ).one()
    return row[0], row[1]


# ==============================================================================================
# C2 - every door into `debts` that does not go through the ORM flush plan
# ==============================================================================================
#
# Each form writes ONE debt of the world without an ORM flush. The verdict is the same sentence for
# all of them, and the non-vacuity assertion is the same too: the stored debts really changed, so
# the form really reached the database. Today every one of them does.

_NEEDS_EXISTING_DEBT = {"core update", "core delete", "legacy bulk_update_mappings"}
_OFF_SESSION = {"core dml on an engine connection"}


async def _c2_write(form: str, world: World, session, engine, existing) -> None:
    debt_id, version = existing if existing else (None, None)
    if form == "core insert":
        await session.execute(insert(Debt).values(**world.debt_values("13.00")))
    elif form == "core update":
        await session.execute(
            update(Debt).where(Debt.id == debt_id).values(amount=exact_money("17.00"))
        )
    elif form == "core delete":
        await session.execute(delete(Debt).where(Debt.id == debt_id))
    elif form == "orm batched insert":
        await session.execute(
            insert(Debt),
            [
                world.debt_values("14.00"),
                world.debt_values("16.00", creditor_id=world.extra_participants[0].id),
            ],
        )
    elif form == "legacy bulk_save_objects":
        await session.run_sync(lambda s: s.bulk_save_objects([world.debt("18.00")]))
    elif form == "legacy bulk_insert_mappings":
        await session.run_sync(
            lambda s: s.bulk_insert_mappings(Debt, [world.debt_values("19.00")])
        )
    elif form == "legacy bulk_update_mappings":
        await session.run_sync(
            lambda s: s.bulk_update_mappings(
                Debt, [{"id": debt_id, "amount": exact_money("22.00"), "version": version}]
            )
        )
    elif form == "core dml on the session connection":
        connection = await session.connection()
        await connection.execute(Debt.__table__.insert().values(**world.debt_values("23.00")))
    elif form == "core dml on an engine connection":
        async with engine.begin() as connection:
            await connection.execute(
                Debt.__table__.insert().values(**world.debt_values("24.00"))
            )
    else:  # pragma: no cover - a typo in the parametrisation must not pass silently
        raise AssertionError(f"unknown C2 form {form!r}")


@pytest.mark.parametrize(
    "form",
    [
        "core insert",
        "core update",
        "core delete",
        "orm batched insert",
        "legacy bulk_save_objects",
        "legacy bulk_insert_mappings",
        "legacy bulk_update_mappings",
        "core dml on the session connection",
        "core dml on an engine connection",
    ],
)
@pytest.mark.asyncio
async def test_c2_a_debt_write_that_skips_the_orm_flush_plan_is_refused(db_session, form) -> None:
    """C2, DEFECT-SHAPED. Nine doors into `debts` that produce no ORM flush and no journal entry.

    RED TODAY BECAUSE: every form reaches the database and commits. The failure prints the debts
    before and after, so the bypass is visible in the message.
    WHY EACH FORM IS HERE, and none is redundant: `session.execute(insert/update/delete(Debt))` is
    Core DML through the ORM session; the batched `insert(Debt), [rows]` form is what SQLAlchemy 2.0
    turns into `insertmanyvalues`; the three `bulk_*` entry points set `Session._flushing` while
    emitting no flush plan at all (`sqlalchemy/orm/session.py:4691-4719`), which is why the design's
    grant cannot be `_flushing` alone; and the last two show the guard must live on the ENGINE, not
    on the session - a `Connection` obtained from `session.connection()` or straight from the engine
    reaches the same table.
    MUTATION once step 4 exists: put the guard on `do_orm_execute` or on the session instead of on
    `before_execute`; the last two forms must go through again.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    try:
        existing = (
            await _existing_debt(factory, world, "5.00")
            if form in _NEEDS_EXISTING_DEBT
            else None
        )
        before = await stored_debts(factory, world)

        if form in _OFF_SESSION:
            refusal = await refusal_of(api, _c2_write(form, world, None, engine, existing))
        else:
            async with factory() as session:
                refusal = await refusal_of(api, _c2_write(form, world, session, engine, existing))
                if refusal is None:
                    await session.commit()

        after = await stored_debts(factory, world)

        # NON-VACUITY: the form really changed the table. A form that silently wrote nothing would
        # otherwise read as "the guard refused it".
        assert after != before or refusal is not None, (
            f"stand: `{form}` neither changed `debts` nor was refused, so nothing was tested "
            f"(before={before})"
        )

        # VERDICT.
        assert refusal is not None, (
            f"`{form}` changed `debts` with no ORM flush and nothing refused it: "
            f"{before} -> {after}. A write the journal cannot derive an entry from must not reach "
            f"the table at all ({JOURNAL_MODULE}, design v2 §6)."
        )
        assert after == before, f"`{form}` was refused but its change is durable: {after}"
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c2_control_reads_and_writes_to_other_tables_are_not_touched(db_session) -> None:
    """C2 anti-vacuum control, GREEN today and after step 4. The guard must not be a blanket.

    A guard that refused everything would make every counterexample above green and the application
    unusable. These three must keep working: a `select(Debt)`, a write to a table that is not
    guarded, and a granted ORM flush of a Debt inside an operation.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    try:
        async with factory() as session:
            async with await _open(api, session, world, "control"):
                session.add(world.debt("25.00"))
                await session.flush()
            rows = (
                await session.execute(
                    select(Debt.amount).where(Debt.equivalent_id == world.equivalent.id)
                )
            ).scalars().all()
            # A write to a table the guard does not own.
            world.equivalent.metadata_ = {"p015_b4": "control"}
            merged = await session.merge(world.equivalent)
            await session.flush()
            await session.commit()

        after = await stored_debts(factory, world)
        assert [Decimal(str(a)) for a in rows] == [Decimal("25.00000000")], rows
        assert after == {("debtor", "creditor", "eq"): Decimal("25.00000000")}, after
        assert merged.metadata_ == {"p015_b4": "control"}
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c2_limit_exec_driver_sql_is_not_intercepted_and_is_declared_so(db_session) -> None:
    """C2's declared limit, and the contrast that keeps it from being an excuse.

    `Connection.exec_driver_sql` fires no `before_execute` (`sqlalchemy/engine/base.py:1712-1778`),
    so the guard cannot see it and design v2 §1.6 documents that rather than claiming otherwise.
    This test PINS the limit: the first half is expected to keep passing, and the second half - the
    same change expressed through the ORM - is the one that must be refused. Without the second
    half this would be a test that certifies a hole.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory)
    debt_id, _version = await _existing_debt(factory, world, "5.00")
    try:
        # HALF 1, the declared limit: raw driver SQL is not intercepted, by construction.
        async with engine.begin() as connection:
            await connection.exec_driver_sql("UPDATE debts SET amount = 44 WHERE amount = 5")
        after_raw = await stored_debts(factory, world)
        assert after_raw == {("debtor", "creditor", "eq"): Decimal("44.00000000")}, (
            f"stand: the raw driver UPDATE did not reach the table, so the limit this test "
            f"declares is not the one being measured: {after_raw}"
        )

        # HALF 2, the contrast: the same change through the ORM must be refused.
        async with factory() as session:
            refusal = await refusal_of(
                api,
                session.execute(
                    update(Debt).where(Debt.id == debt_id).values(amount=exact_money("45.00"))
                ),
            )
            if refusal is None:
                await session.commit()
        after_orm = await stored_debts(factory, world)
        assert refusal is not None, (
            f"the same UPDATE issued through the ORM session was not refused either: {after_orm}. "
            f"`exec_driver_sql` is a documented limit; `session.execute(update(Debt))` is not."
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c2_the_journal_tables_refuse_the_same_writes(db_session) -> None:
    """C2, API-SHAPED. The journal's own tables are guarded exactly as `debts` is.

    They are unmapped Core tables (design v2 §5), so no ORM path reaches them; the guard is what
    stops a Core `insert()` from forging an envelope or an entry. Without it the journal's integrity
    would rest on nobody happening to write to it.

    RED TODAY BECAUSE: the tables do not exist, so a forged INSERT fails with "no such table",
    which is not a refusal. The three existence checks below say which table is missing.
    MUTATION once step 4 exists: list only `debts` in the guard's table set.
    """
    from tests.conftest import TestingSessionLocal as factory

    for table in JOURNAL_TABLES:
        rows = await stored_rows(factory, f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608
        assert rows is not None, missing_journal_tables(rows, table)

    api = journal_api()
    assert api.available, (
        f"{JOURNAL_MODULE} does not exist, so the guard that must own "
        f"{OPERATIONS_TABLE}, {ENTRIES_TABLE} and {OPERATION_EQUIVALENTS_TABLE} does not either"
    )
    from app.db import journal_tables  # type: ignore[attr-defined]

    world = await seed_world(factory)
    try:
        async with factory() as session:
            forged = journal_tables.debt_operations.insert().values(
                id=uuid.uuid4(),
                kind="TEST_FIXTURE",
                identity=_identity("forged"),
                state="OPEN",
            )
            refusal = await refusal_of(api, session.execute(forged))
            if refusal is None:
                await session.commit()
        rows = await stored_rows(factory, f"SELECT identity FROM {OPERATIONS_TABLE}")  # noqa: S608
        assert refusal is not None, (
            f"an envelope was forged straight into `{OPERATIONS_TABLE}` with no operation and "
            f"nothing refused it: {rows}"
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C3 - the key of a debt, and a debt whose key is not in its columns
# ==============================================================================================


@pytest.mark.asyncio
async def test_c3_moving_a_stored_debt_to_another_edge_is_refused(db_session) -> None:
    """C3, DEFECT-SHAPED. `debtor_id`, `creditor_id` and `equivalent_id` are immutable.

    WHY IT MATTERS. The journal records per-edge effects: an entry says what an edge's amount was
    before and after. A row that changes edge is not an update of that edge, it is a delete on one
    edge and an insert on another, and the ORM reports it as a single UPDATE with no amount change
    at all. The journal would record nothing while the money moved between two participants.

    RED TODAY BECAUSE: swapping debtor and creditor on a stored Debt commits, and a fresh read shows
    the obligation now points the other way - with the amount untouched, so no amount-based check
    anywhere could notice.
    MUTATION once step 4 exists: check only `amount` history in `before_flush` and let the key
    columns through.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    debt_id, _version = await _existing_debt(factory, world, "5.00")
    try:
        async with factory() as session:
            debt = await session.get(Debt, debt_id)
            debt.debtor_id = world.creditor.id
            debt.creditor_id = world.debtor.id
            refusal = await refusal_of(api, session.flush())
            if refusal is None:
                await session.commit()

        after = await stored_debts(factory, world)

        # NON-VACUITY: the debt existed on its original edge before the change.
        assert after, "stand: the debt disappeared entirely, so no key change was measured"

        # VERDICT.
        assert refusal is not None, (
            f"a stored Debt was moved from debtor->creditor to creditor->debtor and nothing "
            f"refused it: {after}. The amount did not change, so nothing downstream can see that "
            f"the obligation now belongs to different participants."
        )
        assert after == {("debtor", "creditor", "eq"): Decimal("5.00000000")}, after
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c3_a_pending_debt_keyed_only_through_relationships_is_refused(db_session) -> None:
    """C3, DEFECT-SHAPED. A Debt whose foreign keys are still None when `before_flush` runs.

    MEASURED ON THIS TREE. `Debt(debtor=p, creditor=q, equivalent=e, amount=...)` is a perfectly
    ordinary construction, and at `before_flush` its `debtor_id`, `creditor_id` and
    `equivalent_id` are all `None` - SQLAlchemy resolves relationships to columns during the flush,
    after the hook has run. A hook that reads the columns would therefore see an effect on the edge
    `(None, None, None)`, or skip the row, and the debt would commit unjournalled.

    RED TODAY BECAUSE: the row commits, and the listener below records that the hook's own view of
    it was `(None, None, None)`.
    MUTATION once step 4 exists: read the key columns in `before_flush` without falling back to the
    relationship, and drop the refusal; this row then journals onto a null edge.
    """
    from tests.conftest import TestingSessionLocal as factory
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant

    api = journal_api()
    world = await seed_world(factory)
    seen_by_a_before_flush_hook: list[tuple] = []
    try:
        async with factory() as session:

            @event.listens_for(session.sync_session, "before_flush")
            def _what_a_hook_would_see(sync_session, _flush_context, _instances) -> None:
                for obj in sync_session.new:
                    if isinstance(obj, Debt):
                        seen_by_a_before_flush_hook.append(
                            (obj.debtor_id, obj.creditor_id, obj.equivalent_id)
                        )

            debtor = await session.get(Participant, world.debtor.id)
            creditor = await session.get(Participant, world.creditor.id)
            equivalent = await session.get(Equivalent, world.equivalent.id)
            session.add(
                Debt(
                    id=uuid.uuid4(),
                    debtor=debtor,
                    creditor=creditor,
                    equivalent=equivalent,
                    amount=exact_money("27.00"),
                    version=0,
                )
            )
            refusal = await refusal_of(api, session.flush())
            if refusal is None:
                await session.commit()

        after = await stored_debts(factory, world)

        # NON-VACUITY: the hook's view really was keyless. Without this the verdict could be about
        # an ordinary, fully-keyed Debt.
        assert seen_by_a_before_flush_hook == [(None, None, None)], (
            f"stand: the pending Debt already carried its foreign keys at `before_flush` "
            f"({seen_by_a_before_flush_hook}), so this is not the keyless case C3 describes"
        )

        # VERDICT.
        assert refusal is not None, (
            f"a Debt whose key columns were all None at `before_flush` was flushed and committed, "
            f"and nothing refused it: {after}. The hook cannot name the edge such a row affects, "
            f"so it must refuse rather than journal an effect on no edge."
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# Condition 3 - the grant covers verified writes, not the flush window
# ==============================================================================================


@pytest.mark.asyncio
async def test_condition3_dml_from_another_listeners_after_flush_is_refused(db_session) -> None:
    """Binding condition 3, DEFECT-SHAPED. The reviewer's probe, rebuilt on real debts.

    WHAT THE REVIEWER SHOWED (`review-round2-codex.md` §3). Inside another listener's `after_flush`,
    both of these pass the design's grant condition and become durable:

        session.execute(insert(Debt).values(...))
        session.connection().execute(Debt.__table__.update().values(...))

    `Session._flushing` is still True and `session.info["geo.flush_ctx"]` is still the same flush
    context - they are cleared only at `after_flush_postexec` (`sqlalchemy/orm/session.py:4412`,
    `:4442`). Neither write is in the effects the journal collected at `before_flush`, so the
    journal records money that did not move and misses money that did.

    REQUIREMENT: the grant must name the writes that were verified, not the interval in which the
    verification happened.
    RED TODAY BECAUSE: both writes commit. DEGENERATE FORM: with no journal there is no grant, so
    today's red says the weaker "an unverified write inside a flush was not refused".
    MUTATION once step 4 exists: grant on `(session, flush_context)` for the duration of the flush -
    the design's own wording - instead of per verified statement.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory, extra_participants=2)
    fired: list[str] = []
    try:
        covered = exact_money("20.00")
        async with factory() as session:

            @event.listens_for(session.sync_session, "after_flush")
            def _a_neighbouring_listener(sync_session, _flush_context) -> None:
                if fired:
                    return
                fired.append("after_flush")
                sync_session.execute(
                    insert(Debt).values(
                        **world.debt_values(
                            "77.00", creditor_id=world.extra_participants[0].id
                        )
                    )
                )
                sync_session.connection().execute(
                    Debt.__table__.update()
                    .where(Debt.creditor_id == world.creditor.id)
                    .values(amount=exact_money("99.00"))
                )

            async with await _open(api, session, world, "granted"):
                session.add(world.debt(str(covered)))
                refusal = await refusal_of(api, session.flush())
            if refusal is None:
                await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY: the neighbouring listener really ran inside the flush.
        assert fired == ["after_flush"], (
            f"stand: the neighbouring `after_flush` listener never ran, so no unverified DML was "
            f"attempted: {fired}"
        )

        # VERDICT.
        assert refusal is not None, (
            f"a neighbouring `after_flush` listener inserted a Debt and updated another one, "
            f"neither of them in the effects the journal collected, and both committed: {after}. "
            f"The covered write was {covered}; the table now says otherwise."
        )
        assert after == {}, f"the unverified writes are durable: {after}"
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_condition3_a_late_before_flush_listener_mutating_a_debt_is_refused(
    db_session,
) -> None:
    """Binding condition 3, DEFECT-SHAPED. The mutation that happens after the effects are read.

    THE NEIGHBOURING SHAPE the reviewer named next to the `after_flush` one: a `before_flush`
    listener registered LATER than the journal's runs after it, changes a Debt, and SQLAlchemy then
    re-collects `new`/`dirty` before writing (`sqlalchemy/orm/session.py:4339-4346`). The amount the
    journal recorded and the amount the database stores are then different numbers, and every
    downstream check that reads `debts` agrees with the wrong one.

    RED TODAY BECAUSE: the tampered amount is what commits, and the caller's amount is nowhere.
    MUTATION once step 4 exists: collect effects in `before_flush` and never compare them with what
    was actually written; this test then stores the tampered value silently.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    tampered_rows: list[str] = []
    try:
        written_by_the_caller = exact_money("30.00")
        tampered = exact_money("31.00")
        async with factory() as session:

            @event.listens_for(session.sync_session, "before_flush")
            def _a_late_listener(sync_session, _flush_context, _instances) -> None:
                for obj in list(sync_session.new) + list(sync_session.dirty):
                    if isinstance(obj, Debt) and obj.amount != tampered:
                        obj.amount = tampered
                        tampered_rows.append(str(obj.id))

            async with await _open(api, session, world, "late-mutation"):
                session.add(world.debt(str(written_by_the_caller)))
                refusal = await refusal_of(api, session.flush())
            if refusal is None:
                await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY: the late listener really changed the value.
        assert tampered_rows, "stand: the late `before_flush` listener never changed anything"

        # VERDICT.
        assert refusal is not None, (
            f"a `before_flush` listener registered after the journal's changed the debt's amount "
            f"from {written_by_the_caller} to {tampered} and the change committed: {after}. The "
            f"journal must confirm the write it recorded, not the window it recorded it in."
        )
        assert after.get(("debtor", "creditor", "eq")) != tampered, (
            f"the tampered amount is durable: {after}"
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_condition3_control_a_multi_row_granted_flush_still_passes(db_session) -> None:
    """Binding condition 3, anti-vacuum control. GREEN today and after step 4.

    The reviewer's own requirement alongside the narrowing: "Положительные контроли batch/RETURNING
    сохраняются". A grant per verified write must not degrade into one statement per row. Two debts
    added to one flush must still reach the database as SQLAlchemy chooses to send them - on this
    tree, batched - and both must be stored exactly.

    The statement count is recorded rather than asserted to a fixed number: the point is that
    batching is not forbidden, and pinning SQLAlchemy's batching threshold here would make this a
    test of SQLAlchemy's version instead of of the guard. The count IS asserted to be smaller than
    the number of rows, which is the property that would break under a per-row grant.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    debt_statements: list[str] = []

    def _count(_conn, clauseelement, _multiparams, _params, _options) -> None:
        if getattr(getattr(clauseelement, "table", None), "name", None) == "debts":
            debt_statements.append(type(clauseelement).__name__)

    event.listen(engine.sync_engine, "before_execute", _count)
    try:
        async with factory() as session:
            async with await _open(api, session, world, "batch-control"):
                session.add_all(
                    [
                        world.debt("32.00"),
                        world.debt("33.00", creditor_id=world.extra_participants[0].id),
                    ]
                )
                refusal = await refusal_of(api, session.flush())
            if refusal is None:
                await session.commit()

        after = await stored_debts(factory, world)
        assert refusal is None, f"a legitimate two-row flush inside an operation was refused: {refusal!r}"
        assert after == {
            ("debtor", "creditor", "eq"): Decimal("32.00000000"),
            ("debtor", "extra0", "eq"): Decimal("33.00000000"),
        }, after
        assert 0 < len(debt_statements) < 2, (
            f"two debts in one flush reached the connection as {len(debt_statements)} statements "
            f"({debt_statements}): the grant has been narrowed into one statement per row"
        )
    finally:
        event.remove(engine.sync_engine, "before_execute", _count)
        await drop_world(factory, world)


# ==============================================================================================
# C20 - scope
# ==============================================================================================


@pytest.mark.asyncio
async def test_c20_an_effect_outside_the_declared_scope_is_refused_and_poisons_the_root(
    db_session,
) -> None:
    """C20, DEFECT-SHAPED. An operation may only touch the equivalents it locked.

    WHY SCOPE IS THE POINT OF THE JOURNAL. The per-equivalent chain of step 5 is ordered under the
    equivalent's owner lock. A writer that touches an equivalent it did not lock produces entries
    whose order is a matter of luck, and the chain that is supposed to prove continuity proves
    nothing. So an effect outside the declared scope is not a warning, it is a root poison.

    RED TODAY BECAUSE: the out-of-scope debt commits alongside the in-scope one.
    DEGENERATE FORM: with no journal there is no declared scope, so today's red says the weaker
    "an uncovered write was not refused".
    MUTATION once step 4 exists: check scope but refuse only the offending flush, without poisoning
    the root - the in-scope debt then commits while the operation's equivalents row claims a scope
    the operation did not respect.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    try:
        async with factory() as session:
            async with await _open(
                api, session, world, "scope", scope_equivalent_ids=frozenset({world.equivalent.id})
            ):
                session.add(world.debt("35.00"))
                await session.flush()
                # The other equivalent was never declared and never locked.
                session.add(world.debt("36.00", equivalent=world.other_equivalent))
                refusal = await refusal_of(api, session.flush())
            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY: the out-of-scope write really targeted a different equivalent.
        assert world.other_equivalent.id != world.equivalent.id, "stand: one equivalent, no scope"

        # VERDICT.
        assert refusal is not None, (
            f"a debt was written in an equivalent the operation never declared and nothing refused "
            f"it: {after}. The declared scope is what the operation locked; an effect outside it "
            f"has no ordering guarantee at all."
        )
        assert commit_refusal is not None, (
            f"the root committed after an out-of-scope effect: {after}. A scope violation poisons "
            f"the root until a real database rollback."
        )
        assert after == {}, f"the poisoned root still stored debts: {after}"
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c20_an_intent_equivalent_that_was_never_touched_is_recorded_with_zero_effects(
    db_session,
) -> None:
    """C20, API-SHAPED. "Intended and did nothing" must be recorded, not inferred from silence.

    An operation names the equivalents its intent involves. If one of them ends up with no effect,
    the completion must say so explicitly - `in_intent` true, `effect_count` zero - because
    "no row" would be indistinguishable from "this operation was never asked about that
    equivalent", and step 6's verifier decides continuity from exactly this difference.

    RED TODAY BECAUSE: `debt_operation_equivalents` does not exist.
    MUTATION once step 4 exists: write the equivalents rows only for equivalents that had effects
    (design v2 §5's CHECK `effect_count > 0 OR in_intent` would then never exercise its second arm).
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("untouched-intent")
    try:
        async with factory() as session:
            async with await _open(
                api,
                session,
                world,
                "untouched-intent",
                identity=identity,
                scope_equivalent_ids=frozenset(world.equivalent_ids),
                intent_equivalent_ids=frozenset(world.equivalent_ids),
            ):
                session.add(world.debt("38.00"))
                await session.flush()
            await session.commit()

        rows = await stored_rows(
            factory,
            f"SELECT oe.equivalent_id, oe.in_intent, oe.in_scope, oe.effect_count "  # noqa: S608
            f"FROM {OPERATION_EQUIVALENTS_TABLE} oe JOIN {OPERATIONS_TABLE} o "
            f"ON o.id = oe.operation_id WHERE o.identity = :identity",
            {"identity": identity},
        )

        after = await stored_debts(factory, world)

        # NON-VACUITY: exactly one of the two intended equivalents really received an effect.
        assert after == {("debtor", "creditor", "eq"): Decimal("38.00000000")}, (
            f"stand: the operation did not touch exactly one of its two intended equivalents: "
            f"{after}"
        )

        # VERDICT.
        assert rows is not None, missing_journal_tables(rows, OPERATION_EQUIVALENTS_TABLE)
        by_equivalent = {str(row["equivalent_id"]): row for row in rows}
        untouched = by_equivalent.get(str(world.other_equivalent.id))
        assert untouched is not None, (
            f"the equivalent named in the intent and never touched has no completion row: {rows}. "
            f"Silence must not stand for 'intended, no effect'."
        )
        assert (bool(untouched["in_intent"]), int(untouched["effect_count"])) == (True, 0), untouched
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C21 - the test-local fixture context
# ==============================================================================================


_FIXTURE_BLOCK_CALLING_APPLICATION_CODE = textwrap.dedent(
    """
    async def test_something(db_session):
        async with debt_fixture_setup(session, label="seed") as op:
            session.add(Debt(debtor_id=a, creditor_id=b, equivalent_id=e, amount=amount))
            await PaymentEngine(session).commit(tx_id)
            await session.flush()
    """
)

_FIXTURE_BLOCK_HIDING_THE_CALL_IN_AN_EXPRESSION = textwrap.dedent(
    """
    async def test_something(db_session):
        async with debt_fixture_setup(session, label="seed") as op:
            debt.amount = (await PaymentEngine(session).quote(tx_id)).amount
    """
)

_FIXTURE_BLOCK_THAT_IS_ALLOWED = textwrap.dedent(
    """
    async def test_something(db_session):
        async with debt_fixture_setup(session, label="seed") as op:
            debt = Debt(debtor_id=a, creditor_id=b, equivalent_id=e, amount=amount)
            session.add(debt)
            debt.amount = amount
            await session.flush()
    """
)


@pytest.mark.parametrize(
    "source,expected_rejected",
    [
        (_FIXTURE_BLOCK_CALLING_APPLICATION_CODE, True),
        (_FIXTURE_BLOCK_HIDING_THE_CALL_IN_AN_EXPRESSION, True),
        (_FIXTURE_BLOCK_THAT_IS_ALLOWED, False),
    ],
    ids=["statement call", "call hidden in an expression", "allowed block"],
)
def test_c21_the_fixture_block_guard_rejects_application_calls(
    source, expected_rejected
) -> None:
    """C21, API-SHAPED. The AST guard over `async with debt_fixture_setup` blocks.

    WHY A STATIC GUARD AND NOT ONLY A RUNTIME ONE. The test-local operation kind exists so tests can
    put debts in place without pretending to be a payment. Its contract is that the block contains
    only model construction, `session.add/add_all/delete`, attribute assignment and `flush` - if
    application code runs inside it, the fixture's envelope claims authorship of effects the
    application produced. The runtime nesting refusal catches only application code that opens an
    operation OF ITS OWN; `_apply_flow` under a fixture context is caught by nothing else
    (design v2 §7).

    THE SECOND CASE is binding condition 7: the whitelist must be recursive over EXPRESSIONS. A
    statement-level whitelist that allows "attribute assignment on a local" does not by itself
    forbid an application call on the right-hand side, and the reviewer asked for exactly this
    negative test.

    RED TODAY BECAUSE: `tests/debt_setup.py` and its guard do not exist.
    MUTATION once step 4 exists: make the whitelist statement-level only; the second case must go
    green when it should be red.
    """
    try:
        from tests.debt_setup import fixture_block_violations  # type: ignore[attr-defined]
    except ImportError:
        fixture_block_violations = None

    assert fixture_block_violations is not None, (
        "`tests/debt_setup.py` does not provide `fixture_block_violations(source) -> list[str]`, "
        "so the bodies of `async with debt_fixture_setup` blocks are checked by nothing. Design v2 "
        "§7 makes this guard the only thing that stops application code from running inside a "
        "TEST_FIXTURE operation."
    )
    violations = fixture_block_violations(source)
    if expected_rejected:
        assert violations, (
            f"the guard accepted a fixture block that calls application code:\n{source}"
        )
    else:
        assert not violations, (
            f"the guard rejected a fixture block that only builds and flushes models: {violations}"
            f"\n{source}"
        )


@pytest.mark.asyncio
async def test_c21_an_application_operation_inside_a_fixture_operation_is_refused(
    db_session,
) -> None:
    """C21, API-SHAPED. The runtime half: operations do not nest.

    Design v2 §1.4 refuses opening an operation when the root already holds a non-COMPLETED one.
    A test that opens a fixture context and then calls an application entry point that opens its
    own would otherwise produce two envelopes for one set of effects, with no way to say which one
    owns them.

    RED TODAY BECAUSE: no operation can be opened at all, so nothing can nest. Checked first.
    MUTATION once step 4 exists: allow a nested open when the outer operation's kind is
    TEST_FIXTURE.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    try:
        assert api.available, (
            f"{JOURNAL_MODULE} does not exist, so no operation can be opened and nesting cannot be "
            "observed. This is the step-2 red state, not a passing test."
        )
        refusal: BaseException | None = None
        async with factory() as session:
            async with await _open(api, session, world, "fixture", kind="TEST_FIXTURE"):
                session.add(world.debt("39.00"))
                await session.flush()
                try:
                    async with await _open(api, session, world, "inner", kind="PAYMENT"):
                        pass
                except api.refusals as exc:  # noqa: B902
                    refusal = exc
            await refusal_of(api, session.commit())

        assert refusal is not None, (
            "an application operation opened inside a TEST_FIXTURE operation and nothing refused "
            "it: two envelopes now claim the same effects"
        )
    finally:
        await drop_world(factory, world)
