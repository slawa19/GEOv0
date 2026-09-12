"""Programme 015, `B4` step 2: what the journal must RECORD, and what it must refuse to record.

WHAT THIS MODULE IS. The second slice of the step-2 acceptance bound to `B4` on 2026-09-11. The
first slice (commit `a4533c6`) covered the transaction contract and the write guard - when a write
is allowed to happen at all. This one covers the other half: given that a write is allowed, what the
journal must write down about it, and which values it must refuse before the write happens.

Design v2 §9 counterexamples carried here: `C4` (the entry sequence of one edge's life), `C12`
(money the dialect would silently change), `C13` (an identity is spent once), `C15` (a process that
imports only the models), `C17` (deleting the rows the history names), `C18` (a concurrent version
bump), `C19` (forged journal rows), plus **binding condition 4** - exact storability of the computed
DELTA, not only of the endpoints.

`C5` and `C6` live in `tests/unit/test_p015_b4_wrong_writer_is_recorded_faithfully.py`, because they
need a real payment and a real clearing cycle rather than a bare session. The PostgreSQL tier is
`tests/integration/test_p015_b4_entries_and_money_postgres.py`.

TWO KINDS OF RED, the same two the first slice established and they read differently:

* DEFECT-SHAPED - the scenario runs today to its end and the database keeps what the journal would
  have refused. The failure quotes real stored money. `C12` and condition 4 are the sharp ones here:
  this tree stores `0.123456789` as `0.12345679` and `Infinity` as `Infinity`, and nothing objects.
* API-SHAPED - the property has no carrier: there is no entries table to look in, no envelope to
  conflict with. These assert non-vacuity FIRST, so they cannot pass having measured nothing.

TIER. SQLite, the default tier. Money stays inside `|v| < 2^26` - the domain where scale-8 values
round-trip exactly through the driver's float binding (design v2 §4) - EXCEPT where the subject of
the counterexample is precisely a value outside it. Those tests do not go through
`tests.p015_b4_support.exact_money`; they measure the real round-trip against the real database
first, and say so in the failure text, so that "the dialect changed this number" can never be
confused with "this test used a number the tier cannot hold".

MARKER. This module carries `b4_counterexample` and is deselected from the canonical gate. It is
red on purpose until step 4 exists, and STEP 4 REMOVES THE MARKER, NOT THE ASSERTIONS - the full
contract is in the comment above the marker list in `pytest.ini`, and
`tests/unit/test_p015_b4_counterexample_marker_is_not_a_hiding_place.py` holds it in place.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DatabaseError, StatementError
from sqlalchemy.orm.exc import StaleDataError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.p015_b4_support import (
    ENTRIES_TABLE,
    JOURNAL_MODULE,
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
    stored_entries,
    stored_operations,
    stored_rows,
)

pytestmark = pytest.mark.b4_counterexample


def _identity(name: str) -> str:
    return f"p015-b4/{name}/{uuid.uuid4().hex[:12]}"


def _intent(**fields) -> dict:
    return {"source": "p015-b4-counterexample", **fields}


async def _open(api, session, world: World, name: str, **kw):
    """`operation()` with this module's standard arguments, so each test shows only what differs."""
    return operation(
        api,
        session,
        kind=kw.pop("kind", "TEST_FIXTURE"),
        identity=kw.pop("identity", _identity(name)),
        intent=kw.pop("intent", _intent(name=name)),
        scope_equivalent_ids=kw.pop("scope_equivalent_ids", frozenset({world.equivalent.id})),
        **kw,
    )


def _money(rows: list[dict] | None, key: str) -> list[Decimal | None]:
    """A column of `stored_entries` as Decimals, so a float from SQLite cannot compare unequal."""
    return [None if row[key] is None else Decimal(str(row[key])) for row in rows or []]


class _DebtStatements:
    """Records every statement that reaches the connection naming the `debts` table.

    "Refused BEFORE any SQL" is a requirement about the ORDER of two things, and the only way to see
    it is to watch the connection. Reading the table afterwards cannot: a statement that ran and was
    rolled back leaves the same empty table as a statement that never ran, and design v2 §4's rule
    is explicitly "refusal before SQL" - a value the dialect would change must never be sent, not
    sent and then undone.
    """

    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, _conn, clauseelement, _multiparams, _params, _options) -> None:
        table = getattr(getattr(clauseelement, "table", None), "name", None)
        if table == "debts":
            self.seen.append(type(clauseelement).__name__)


def _watch_debt_statements(engine) -> _DebtStatements:
    recorder = _DebtStatements()
    event.listen(engine.sync_engine, "before_execute", recorder)
    return recorder


def _obeys_the_money_rules(value: Decimal) -> bool:
    """Design v2 §4 rule 3 minus its dialect clause, restated here for the ANTI-VACUUM only.

    It is deliberately a restatement and not an import: the rule it describes does not exist yet,
    and the module that will own it is what these counterexamples are written against. It is used
    in exactly one place - to say "this value is forbidden for a reason OTHER than the round-trip" -
    so a future step 4 that legalises one of these values makes the non-vacuity assertion speak up
    instead of letting the test pass having measured nothing.
    """
    if not value.is_finite():
        return False
    if value != value.quantize(Decimal("1E-8")):
        return False
    return abs(value) < Decimal(10) ** 12


def _debt_row(world: World, amount: Decimal) -> Debt:
    """A `Debt` on this world's edge with an amount `exact_money()` would refuse.

    `World.debt` guards the proven-exact SQLite domain, which is right for every test whose subject
    is something other than money precision. These tests' subject IS money precision, so they build
    the row directly and measure what the database does with it.
    """
    return Debt(
        id=uuid.uuid4(),
        debtor_id=world.debtor.id,
        creditor_id=world.creditor.id,
        equivalent_id=world.equivalent.id,
        amount=amount,
        version=0,
    )


async def _refusal_or_database_error(api, awaitable):
    """`refusal_of`, widened to the database's own complaint.

    Some counterexamples here are about a refusal that design v2 §5 places in the SCHEMA -
    `UNIQUE(kind, identity)`, a CHECK - rather than in the hook. Catching only the journal's
    exception types would let the test die on an `IntegrityError` instead of reading it as the
    refusal it is.
    """
    try:
        await awaitable
    except api.refusals as exc:  # noqa: B902 - the refusal contract is the subject under test
        return exc
    except DatabaseError as exc:
        return exc
    return None


async def _round_trip_through_the_real_database(factory, world: World, value: Decimal):
    """Store `value` on this world's edge, read it back on a NEW session, remove it again.

    Returns `(stored, error)`: exactly one is not None. This is the MEASUREMENT the money
    counterexamples stand on - never an analytical claim about floats, always what this database on
    this dialect actually kept (`AGENTS.md` §1, "никаких гипотез из памяти сессии").
    """
    row_id = uuid.uuid4()
    try:
        async with factory() as session:
            session.add(
                Debt(
                    id=row_id,
                    debtor_id=world.debtor.id,
                    creditor_id=world.creditor.id,
                    equivalent_id=world.equivalent.id,
                    amount=value,
                    version=0,
                )
            )
            await session.commit()
    except (DatabaseError, StatementError) as exc:
        return None, exc
    async with factory() as fresh:
        stored = (
            await fresh.execute(select(Debt.amount).where(Debt.id == row_id))
        ).scalar_one_or_none()
    async with factory() as cleanup:
        await cleanup.execute(Debt.__table__.delete().where(Debt.id == row_id))
        await cleanup.commit()
    return (None if stored is None else Decimal(str(stored))), None


# ==============================================================================================
# C4 - the entry sequence of one edge's life
# ==============================================================================================


@pytest.mark.asyncio
async def test_c4_one_edge_through_insert_update_update_delete_is_four_linked_entries(
    db_session,
) -> None:
    """C4, API-SHAPED. The journal is a chain per edge, not a diff of the two endpoints.

    An edge that goes 10 -> 7 -> 12 -> gone inside one operation must leave four entries whose
    `amount_before` is the previous entry's `amount_after`, whose `delta` is the difference, and
    whose last entry records the removal as a negative delta of the whole remaining amount. The
    property that makes this worth a counterexample is CONTINUITY: a journal that recorded only
    (first before, last after) would be indistinguishable from one that recorded the truth here, and
    step 6's verifier would have nothing to check the middle against.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so the non-vacuity assertion below -
    placed FIRST - fails naming the missing table.
    MUTATION once step 4 exists: aggregate the four flushes into one entry per operation per edge
    (net -0 here, so the operation would report "nothing happened"), or write `amount_before` from
    the in-memory attribute's ORIGINAL loaded value instead of the previous entry's `amount_after`.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("edge-life")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "edge-life", identity=identity):
                debt = world.debt("10.00")
                session.add(debt)
                await session.flush()
                debt.amount = exact_money("7.00")
                await session.flush()
                debt.amount = exact_money("12.00")
                await session.flush()
                await session.delete(debt)
                await session.flush()
            await session.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST. Without the table there is nothing to be right or wrong about, and
        # every assertion below would be about an empty list.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: the four flushes really happened and really cancelled out.
        assert after == {}, f"stand: the edge was not removed by the end of the operation: {after}"

        assert [row["flush_ordinal"] for row in entries] == [1, 2, 3, 4], entries
        assert [row["effect"] for row in entries] == ["I", "U", "U", "D"], (
            f"the four flushes were not recorded as INSERT, UPDATE, UPDATE, DELETE: {entries}"
        )
        assert _money(entries, "amount_before") == [
            None,
            Decimal("10.00000000"),
            Decimal("7.00000000"),
            Decimal("12.00000000"),
        ], f"an entry's `amount_before` is not the previous entry's `amount_after`: {entries}"
        assert _money(entries, "amount_after") == [
            Decimal("10.00000000"),
            Decimal("7.00000000"),
            Decimal("12.00000000"),
            None,
        ], entries
        assert _money(entries, "delta") == [
            Decimal("10.00000000"),
            Decimal("-3.00000000"),
            Decimal("5.00000000"),
            Decimal("-12.00000000"),
        ], (
            f"the deltas do not reconstruct the edge's life; a DELETE in particular must record "
            f"the whole remaining amount as a negative delta: {entries}"
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c4_an_amount_change_and_a_delete_in_one_flush_are_one_effect(db_session) -> None:
    """C4, API-SHAPED. Per-flush aggregation per key: one flush, one entry for one edge.

    Design v2 §6 aggregates effects per key per flush. The case that decides the rule is "set it to
    zero, then delete it" in a single flush: the ORM emits only the DELETE, so a journal that
    listened to attribute events instead of to the flush plan would invent a `U` to `0.00000000`
    that never reached the database - and `0` is not even a value `debts` can hold
    (`chk_debt_amount_positive`). One entry, effect `D`, `amount_before` the value that was really
    stored.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist.
    MUTATION once step 4 exists: record effects from `attributes.get_history` per attribute change
    instead of from the flush plan - two entries appear, one of them describing a state the database
    never held.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("zero-then-delete")
    try:
        async with factory() as setup:
            setup.add(world.debt("10.00"))
            await setup.commit()

        async with factory() as session:
            async with await _open(api, session, world, "zero-then-delete", identity=identity):
                debt = (
                    await session.execute(
                        select(Debt).where(Debt.equivalent_id == world.equivalent.id)
                    )
                ).scalar_one()
                debt.amount = Decimal("0")
                await session.delete(debt)
                await session.flush()
            await session.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: the row really went away, in one flush.
        assert after == {}, f"stand: the debt survived the delete: {after}"

        assert len(entries) == 1, (
            f"one flush touching one edge produced {len(entries)} entries: {entries}. The extra one "
            f"describes an amount the database never stored."
        )
        assert entries[0]["effect"] == "D", entries
        assert _money(entries, "amount_before") == [Decimal("10.00000000")], entries
        assert _money(entries, "amount_after") == [None], entries
        assert _money(entries, "delta") == [Decimal("-10.00000000")], entries
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c4_an_edge_created_and_removed_again_still_counts_two_effects(db_session) -> None:
    """C4, API-SHAPED. Net zero is not "nothing happened".

    An operation that creates an edge at 10 and later removes it leaves `debts` exactly as it found
    it. Its `effect_count` must still be 2. This is the counterexample against summarising an
    operation by its net effect: the money moved twice, and step 6 reconstructs a period by
    replaying deltas, not by diffing endpoints.

    RED TODAY BECAUSE: `debt_operations` does not exist, so `effect_count` has nowhere to live.
    MUTATION once step 4 exists: compute `effect_count` from the number of distinct edges whose
    final state differs from their initial state.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("net-zero")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "net-zero", identity=identity):
                debt = world.debt("10.00")
                session.add(debt)
                await session.flush()
                await session.delete(debt)
                await session.flush()
            await session.commit()

        envelopes = await stored_operations(factory, identity)
        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)

        # NON-VACUITY: the operation really ended where it started.
        assert after == {}, f"stand: the edge outlived the operation: {after}"

        assert len(envelopes) == 1, envelopes
        assert envelopes[0]["effect_count"] == 2, (
            f"an operation that inserted an edge and removed it again reported "
            f"effect_count={envelopes[0]['effect_count']}: {envelopes}. Net zero is two effects, "
            f"not none."
        )
        assert [row["effect"] for row in entries or []] == ["I", "D"], entries
        assert _money(entries, "delta") == [Decimal("10.00000000"), Decimal("-10.00000000")], entries
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c4_a_deleted_edge_that_comes_back_is_an_insert_and_not_an_update(db_session) -> None:
    """C4, API-SHAPED. The journal is keyed by the EDGE, not by the row id.

    `debts` has `UNIQUE(debtor, creditor, equivalent)`, so an edge that is deleted and later
    recreated comes back under a NEW primary key. The journal keys its entries by the edge, so the
    two entries land in the same chain - and the second one must be an `I` with `amount_before`
    NULL, not a `U` from the value the deleted row used to hold. A journal that carried the old
    amount forward would claim continuity across a gap in which the edge genuinely did not exist.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist.
    MUTATION once step 4 exists: derive `amount_before` for an `I` from the last entry on the same
    edge instead of from NULL.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("reinsert")
    try:
        async with factory() as setup:
            setup.add(world.debt("10.00"))
            await setup.commit()

        async with factory() as session:
            async with await _open(api, session, world, "reinsert", identity=identity):
                debt = (
                    await session.execute(
                        select(Debt).where(Debt.equivalent_id == world.equivalent.id)
                    )
                ).scalar_one()
                first_id = debt.id
                await session.delete(debt)
                await session.flush()
                reborn = world.debt("4.00")
                session.add(reborn)
                await session.flush()
                second_id = reborn.id
            await session.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # NON-VACUITY: it really is a new row on the same edge, which is the whole premise.
        assert first_id != second_id, "stand: the edge came back under the same primary key"
        assert after == {("debtor", "creditor", "eq"): Decimal("4.00000000")}, after

        assert [row["effect"] for row in entries] == ["D", "I"], entries
        assert _money(entries, "amount_before") == [Decimal("10.00000000"), None], (
            f"the re-created edge was recorded as a continuation of the row that was deleted: "
            f"{entries}"
        )
        assert _money(entries, "delta") == [Decimal("-10.00000000"), Decimal("4.00000000")], entries
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C12 - money this dialect would silently change
# ==============================================================================================


#: `(label, value, what this tree does with it today)`. The third element is not decoration: it is
#: the measured current behaviour, quoted in the failure so that the reader of a red run sees what
#: is actually in the database rather than a claim about floats. Re-measured 2026-09-12 against a
#: real aiosqlite engine with T1525 transaction control.
_UNSTORABLE_ON_SQLITE = [
    ("more than eight decimal places", "0.123456789", "stored as 0.12345679"),
    ("at the magnitude ceiling", "1E12", "stored as 1000000000000.00000000"),
    ("not a number", "NaN", "sent as SQL NULL and refused by NOT NULL, with a misleading message"),
    ("infinite", "Infinity", "stored as Infinity - the database now holds an infinite debt"),
    ("one atom past the exact domain", "100000000000.00000001", "stored as 100000000000.00000000"),
]


@pytest.mark.parametrize(
    "label,value,today", _UNSTORABLE_ON_SQLITE, ids=[row[0] for row in _UNSTORABLE_ON_SQLITE]
)
@pytest.mark.asyncio
async def test_c12_a_value_this_dialect_cannot_hold_is_refused_before_any_debt_sql(
    db_session, label, value, today
) -> None:
    """C12, DEFECT-SHAPED. A money value the driver would change must never reach the database.

    Design v2 §4 rule 3: an operation refuses a value unless
    `result_processor(bind_processor(v)) == v` for the SESSION'S ACTUAL DIALECT, and it refuses
    BEFORE the SQL. Both halves matter. The dialect half is why this is not a constant: PostgreSQL
    stores every one of these exactly, and it is SQLite - the default `DATABASE_URL` of this
    application (`app/config.py:55`) - that silently rounds.

    The "before SQL" half is why this test watches the connection instead of reading the table
    afterwards. A statement that ran and was rolled back leaves the same empty table as a statement
    that never ran, and accepting the first would be leaning on a rollback to undo a corruption -
    the "compensation further downstream" `AGENTS.md` §9 forbids.

    RED TODAY BECAUSE: nothing checks storability, the INSERT is sent, and the database keeps a
    DIFFERENT number than the one the caller wrote - or, for `NaN`, refuses it with a NOT NULL
    message that names the wrong problem.
    MUTATION once step 4 exists: check the value against `Decimal`'s own scale and range instead of
    against the dialect's round-trip (every case here has scale <= 8 after quantisation or is
    perfectly representable as a `Decimal`), or move the check into `after_flush`.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory)
    amount = Decimal(value)
    try:
        stored, error = await _round_trip_through_the_real_database(factory, world, amount)

        # NON-VACUITY, FIRST, and measured: this value really is one this money core must not
        # accept - either because the dialect changes it, or because the database itself objects,
        # or because it is outside the domain design v2 §4 defines at all. Three of the five cases
        # here are round-trip failures and two are not (`1E12` and `Infinity` are stored back
        # BYTE-FOR-BYTE and are forbidden for other reasons), so a bare "it changed" assertion
        # would be false for them, and a test that asserted nothing here could pass having measured
        # a value that is perfectly legal.
        assert stored != amount or error is not None or not _obeys_the_money_rules(amount), (
            f"stand: this database stored {value} unchanged (read back {stored!r}) and the money "
            f"rules of design v2 §4 permit it, so `{label}` is no longer an unacceptable value here "
            f"and this counterexample has lost its subject"
        )

        recorder = _watch_debt_statements(engine)
        refusal = None
        try:
            async with factory() as session:
                async with await _open(api, session, world, "unstorable"):
                    session.add(_debt_row(world, amount))
                    refusal = await _refusal_or_database_error(api, session.flush())
                if refusal is None:
                    refusal = await _refusal_or_database_error(api, session.commit())
        except (DatabaseError, StatementError) as exc:
            # The database's own complaint is not the journal's refusal, and the `sent` assertion
            # below is what says so. Recorded here only so the scenario finishes.
            refusal = exc
        finally:
            event.remove(engine.sync_engine, "before_execute", recorder)

        after = await stored_debts(factory, world)

        # VERDICT.
        assert not recorder.seen, (
            f"a debt amount of {value} ({label}) reached the `debts` table as {recorder.seen}; this "
            f"database {today}. A value outside the money domain of design v2 §4 - not finite, past "
            f"the magnitude ceiling, or one this dialect would silently change - must be refused by "
            f"{JOURNAL_MODULE} BEFORE the statement is sent, not stored and corrected afterwards. "
            f"The table now holds {after or 'nothing, because the database itself objected'}."
        )
        assert isinstance(refusal, api.refusals), (
            f"the refusal of a debt amount of {value} ({label}) was {refusal!r}, which is not one "
            f"of {JOURNAL_MODULE}'s refusal types. This database {today}: the database's own "
            f"complaint arrives after the statement, names the wrong problem, and does not exist "
            f"for the cases this backend stores happily."
        )
        assert after == {}, f"the unstorable amount is durable: {after}"
    finally:
        await drop_world(factory, world)


@pytest.mark.parametrize("value", ["1.000000000", "67108863.99999999"])
@pytest.mark.asyncio
async def test_c12_control_a_value_this_dialect_holds_exactly_is_accepted(db_session, value) -> None:
    """C12, anti-vacuum control. GREEN today and after step 4.

    The refusals above are only meaningful if the rule they encode has a passing side. `1.000000000`
    is written with nine decimal places and is accepted, because quantisation to scale 8 does not
    change its VALUE; `67108863.99999999` is the last atom below 2^26 and round-trips exactly. A
    storability rule that refused either would stop payments from happening at all.
    """
    from tests.conftest import TestingSessionLocal as factory

    world = await seed_world(factory)
    try:
        stored, error = await _round_trip_through_the_real_database(factory, world, Decimal(value))
        assert error is None, f"the database refused a legitimate amount {value}: {error!r}"
        assert stored == Decimal(value), (
            f"{value} is supposed to round-trip exactly on this dialect and came back as {stored!r}"
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# Binding condition 4 - the DELTA must be storable, not only the endpoints
# ==============================================================================================


@pytest.mark.asyncio
async def test_condition4_a_delta_this_dialect_cannot_hold_is_refused_though_both_ends_fit(
    db_session,
) -> None:
    """Binding condition 4, DEFECT-SHAPED. The reviewer's counterexample, verbatim.

    "Точная сохраняемость проверяется у дельты, а не только у `before`/`after`:
    `100000000000 -> 0.00000001` - оба значения точны, дельта `-99999999999.99999999` читается как
    `-100000000000.00000000`. Отказ - до SQL долга." (spec, `B4` acceptance condition 4.)

    WHY A PER-VALUE CHECK IS NOT ENOUGH, which is the whole point. `100000000000` and `0.00000001`
    each round-trip through this dialect unchanged - the test measures both against the real
    database below, so the premise is not taken on faith. Their difference does not. A journal that
    validated `amount_before` and `amount_after` and then computed `delta` from them would store a
    delta that is wrong by a whole atom, and every downstream sum - step 5's chain, step 6's
    reconstruction - would be wrong by that atom with nothing to reveal it, because each of the
    three columns is individually storable and the CHECK constraints of design v2 §5 are all
    satisfied.

    RED TODAY BECAUSE: nothing checks anything, and the UPDATE goes through. The `debts` row is left
    holding `0.00000001` with the journal that should have refused it absent entirely.
    MUTATION once step 4 exists: validate `amount_before` and `amount_after` and derive `delta`
    without validating it; this test must go red again and the `C12` cases above must stay green.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory)
    start = Decimal("100000000000")
    finish = Decimal("0.00000001")
    delta = finish - start
    try:
        # NON-VACUITY, FIRST, and measured rather than reasoned: both ENDPOINTS survive this
        # dialect unchanged and the DELTA does not. If any of the three were to change, the
        # counterexample would be about the endpoints again and would prove nothing new.
        stored_start, start_error = await _round_trip_through_the_real_database(factory, world, start)
        stored_finish, finish_error = await _round_trip_through_the_real_database(
            factory, world, finish
        )
        stored_delta, _ = await _round_trip_through_the_real_database(factory, world, -delta)
        assert (start_error, finish_error) == (None, None), (start_error, finish_error)
        assert stored_start == start and stored_finish == finish, (
            f"stand: an endpoint no longer round-trips exactly here ({stored_start!r}, "
            f"{stored_finish!r}); this counterexample needs both endpoints to be storable"
        )
        assert stored_delta != -delta, (
            f"stand: this database now stores {-delta} exactly (read back {stored_delta!r}), so the "
            f"delta is no longer the unstorable part and condition 4 has lost its counterexample"
        )

        async with factory() as setup:
            setup.add(
                Debt(
                    id=uuid.uuid4(),
                    debtor_id=world.debtor.id,
                    creditor_id=world.creditor.id,
                    equivalent_id=world.equivalent.id,
                    amount=start,
                    version=0,
                )
            )
            await setup.commit()

        recorder = _watch_debt_statements(engine)
        try:
            async with factory() as session:
                async with await _open(api, session, world, "delta-storability"):
                    debt = (
                        await session.execute(
                            select(Debt).where(Debt.equivalent_id == world.equivalent.id)
                        )
                    ).scalar_one()
                    debt.amount = finish
                    refusal = await refusal_of(api, session.flush())
                if refusal is None:
                    await refusal_of(api, session.commit())
        finally:
            event.remove(engine.sync_engine, "before_execute", recorder)

        after = await stored_debts(factory, world)

        # VERDICT.
        assert not recorder.seen, (
            f"the UPDATE moving a debt from {start:f} to {finish:f} reached `debts` as "
            f"{recorder.seen}. Both endpoints are storable on this dialect - they were measured "
            f"above and came back as {stored_start:f} and {stored_finish:f} - and the delta "
            f"{delta:f} is not: its magnitude {-delta:f} reads back as {stored_delta:f}, wrong by "
            f"one whole atom. {JOURNAL_MODULE} must refuse this operation BEFORE the debt SQL, "
            f"because once the row is written the journal has no way to record the movement "
            f"truthfully: `amount_before`, `amount_after` and `delta` would each satisfy every "
            f"CHECK of design v2 §5 while their arithmetic is false. `debts` now holds {after}."
        )
        assert refusal is not None, (
            f"nothing refused a movement whose delta this dialect cannot hold; `debts` holds {after}"
        )
        assert after == {("debtor", "creditor", "eq"): start}, (
            f"the refused movement was applied anyway: {after}"
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C13 - an identity is spent once
# ==============================================================================================


@pytest.mark.parametrize("second_intent", ["the same intent", "a different intent"])
@pytest.mark.asyncio
async def test_c13_a_second_operation_with_a_spent_identity_is_refused(
    db_session, second_intent
) -> None:
    """C13, API-SHAPED. `UNIQUE(kind, identity)` is the idempotency of the journal itself.

    An operation's identity is what makes a retried writer recognisable. Opening a second operation
    under an identity that already has an envelope must be refused by the DATABASE - design v2 §5's
    `UNIQUE(kind, identity)` - and not by a preceding SELECT, because two concurrent writers can
    both read "no envelope" and both proceed. Both parametrisations must be refused: an identical
    intent is still a second operation, and a DIFFERENT intent under the same identity is the case
    that would otherwise let a replay quietly rewrite what an operation claimed to be doing.

    RED TODAY BECAUSE: `debt_operations` does not exist, so there is no envelope to collide with.
    MUTATION once step 4 exists: pre-check the identity with a SELECT and skip the INSERT when a row
    is found - the test still passes single-threaded, so the PostgreSQL sibling
    (`test_p015_b4_entries_and_money_postgres.py`) runs the two openers concurrently.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("spent-identity")
    try:
        async with factory() as first:
            async with await _open(
                api, first, world, "spent-identity", identity=identity, intent=_intent(attempt=1)
            ):
                first.add(world.debt("19.00"))
                await first.flush()
            await first.commit()

        envelopes = await stored_operations(factory, identity)

        # NON-VACUITY, FIRST: the identity really was spent by a completed operation.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert len(envelopes) == 1 and envelopes[0]["state"] == "COMPLETED", (
            f"stand: the first operation did not leave a completed envelope: {envelopes}"
        )

        intent = _intent(attempt=1) if second_intent == "the same intent" else _intent(attempt=2)
        async with factory() as second:
            reopen = await _refusal_or_database_error(
                api, _reopen(api, second, world, identity, intent)
            )
            await second.rollback()

        assert reopen is not None, (
            f"a second operation opened under the spent identity {identity!r} with "
            f"{second_intent}; `UNIQUE(kind, identity)` is what stops a retried writer from "
            f"journalling its effects twice under the same name"
        )
        assert len(await stored_operations(factory, identity) or []) == 1, (
            "the refused second opening still left an envelope behind"
        )
    finally:
        await drop_world(factory, world)


async def _reopen(api, session, world: World, identity: str, intent: dict) -> None:
    """Enter and leave an operation under an identity that is already spent."""
    async with await _open(api, session, world, "spent-identity", identity=identity, intent=intent):
        pass


# ==============================================================================================
# C15 - a process that imports only the models
# ==============================================================================================


#: Run OUT OF PROCESS on purpose. Everything else in this module runs inside a pytest process where
#: `tests/conftest.py` has already imported half the application; a listener registered there would
#: make this counterexample pass for a reason that does not exist in production. The subprocess
#: imports `app.db.models` and nothing else - the minimum a script, a migration helper or an
#: operator's REPL would import - and the journal's protection must survive that.
_UNINSTRUMENTED_WRITER = '''
import asyncio, sys, uuid
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.sqlite_transaction_control import install_sqlite_transaction_control

URL = sys.argv[1]


async def main() -> str:
    engine = create_async_engine(URL)
    install_sqlite_transaction_control(engine.sync_engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8].upper()
    async with factory() as session:
        equivalent = Equivalent(code="C15" + tag, precision=2, is_active=True, metadata_={})
        debtor = Participant(pid="C15D" + tag, display_name="d", public_key="c15d" + tag,
                             type="person", status="active", profile={})
        creditor = Participant(pid="C15C" + tag, display_name="c", public_key="c15c" + tag,
                               type="person", status="active", profile={})
        session.add_all([equivalent, debtor, creditor])
        await session.commit()
        ids = (equivalent.id, debtor.id, creditor.id)
    try:
        async with factory() as session:
            session.add(Debt(id=uuid.uuid4(), debtor_id=ids[1], creditor_id=ids[2],
                             equivalent_id=ids[0], amount=Decimal("21.00000000"), version=0))
            await session.commit()
    except BaseException as exc:
        return "REFUSED:" + type(exc).__name__
    async with factory() as fresh:
        from sqlalchemy import select
        stored = (await fresh.execute(select(Debt.amount))).scalars().all()
    await engine.dispose()
    return "STORED:" + ",".join(str(value) for value in stored)


print(asyncio.run(main()))
'''


@pytest.mark.asyncio
async def test_c15_a_process_that_imports_only_the_models_still_cannot_write_a_debt(
    tmp_path,
) -> None:
    """C15, DEFECT-SHAPED. The protection must live with the models, not with the application.

    WHY OUT OF PROCESS. Every other counterexample here runs where `tests/conftest.py` has already
    imported the world. If the journal's listeners were registered by an application entry point -
    `app/main.py`, a service constructor, a FastAPI dependency - every one of those tests would
    still pass and a `python -c` one-liner, a data-fix script or `scripts/seed_db.py` run by hand
    would write debts with no journal at all. Design v2 §1.3 puts the listeners on class-level
    `ConnectionEvents`, which is what makes importing the models enough; this is the counterexample
    that can tell the two designs apart.

    RED TODAY BECAUSE: the subprocess stores `21.00000000` and nothing objects. The failure below
    quotes what it stored.
    MUTATION once step 4 exists: register the listeners from `app/main.py`'s startup or from
    `app/db/session.py`'s engine construction instead of from the module that defines the tables.
    """
    script = tmp_path / "uninstrumented_writer.py"
    script.write_text(textwrap.dedent(_UNINSTRUMENTED_WRITER), encoding="utf-8")
    url = f"sqlite+aiosqlite:///{(tmp_path / 'c15.db').as_posix()}"

    # `app` is not installed into the virtualenv, and `python script.py` puts the SCRIPT's
    # directory on `sys.path`, not the working directory - so the repository root is passed
    # explicitly. That is what a real operator script does too.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(_repo_root())
    completed = subprocess.run(
        [sys.executable, str(script), url],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(_repo_root()),
        env=environment,
    )

    # NON-VACUITY, FIRST: the subprocess really ran its scenario. A crashed import, a missing
    # dependency or a schema failure would otherwise read as "the write was refused".
    assert completed.returncode == 0, (
        f"stand: the subprocess did not finish its scenario (exit {completed.returncode}).\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr[-2000:]}"
    )
    verdict = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    assert verdict.startswith(("REFUSED:", "STORED:")), (
        f"stand: the subprocess printed no verdict.\nstdout: {completed.stdout}\n"
        f"stderr: {completed.stderr[-2000:]}"
    )

    # VERDICT.
    assert verdict.startswith("REFUSED:"), (
        f"a process that imported only `app.db.models` wrote a debt with no operation open and "
        f"nothing refused it: the database now holds {verdict.removeprefix('STORED:')}. The "
        f"journal's protection must arrive with the models, not with an application entry point."
    )


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


# ==============================================================================================
# C17 - deleting the rows the history names
# ==============================================================================================


@pytest.mark.parametrize("target", ["equivalent", "participant"])
@pytest.mark.asyncio
async def test_c17_a_row_the_journal_history_names_cannot_be_deleted(db_session, target) -> None:
    """C17, API-SHAPED. History outlives the debts, so it must outlive their referents too.

    T1524 made `debts.equivalent_id` RESTRICT, so an equivalent with live debts cannot be deleted.
    That is not enough once a journal exists: an equivalent whose ONLY debt has been cleared has no
    `debts` row left, and deleting it would take the meaning of every journal entry denominated in
    it with it - the entries would survive as numbers pointing at nothing. `debts.debtor_id` and
    `debts.creditor_id` are still CASCADE (`app/db/models/debt.py:11-12`, deliberately, R2-13), so
    the participant half is worse: today the delete SUCCEEDS and removes the rows silently.

    Design v2 §5 makes every journal FK RESTRICT. This counterexample is what that decision is for.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist, so there is no history to protect and
    nothing to refuse the delete.
    MUTATION once step 4 exists: make the journal's FKs CASCADE - migration 021 then deletes the
    history along with the row and this test goes red again, which is exactly the mutation design
    v2 §10.1 names ("Mutation: journal FK CASCADE").
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity(f"history-outlives-{target}")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "history", identity=identity):
                debt = world.debt("23.00")
                session.add(debt)
                await session.flush()
                await session.delete(debt)
                await session.flush()
            await session.commit()

        entries = await stored_entries(factory, identity)
        debts_now = await stored_debts(factory, world)

        # NON-VACUITY, FIRST: there is history, and there are no debts left to hide behind. Without
        # this the delete below would be refused by T1524's RESTRICT on `debts` and the test would
        # pass without the journal existing at all.
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)
        assert len(entries) == 2, f"stand: the operation left {entries} instead of an I and a D"
        assert debts_now == {}, (
            f"stand: a debt is still live, so any refusal below would be T1524's and not the "
            f"journal's: {debts_now}"
        )

        if target == "equivalent":
            statement = text("DELETE FROM equivalents WHERE id = :id")
            params = {"id": str(world.equivalent.id)}
            survivors = select(Equivalent.id).where(Equivalent.id == world.equivalent.id)
        else:
            statement = text("DELETE FROM participants WHERE id = :id")
            params = {"id": str(world.debtor.id)}
            survivors = select(Participant.id).where(Participant.id == world.debtor.id)

        error = None
        async with factory() as remover:
            try:
                await remover.execute(statement, params)
                await remover.commit()
            except DatabaseError as exc:
                error = exc
                await remover.rollback()

        async with factory() as fresh:
            still_there = (await fresh.execute(survivors)).scalar_one_or_none()

        # VERDICT.
        assert error is not None, (
            f"a raw DELETE removed the {target} that {len(entries)} journal entries name, and the "
            f"database allowed it. The entries are now money history denominated in a row that does "
            f"not exist. Every journal foreign key is RESTRICT for this reason (design v2 §5)."
        )
        assert still_there is not None, f"the {target} is gone despite the refusal"
        assert len(await stored_entries(factory, identity) or []) == 2, (
            "the journal entries did not survive the refused delete"
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C18 - a concurrent version bump
# ==============================================================================================


@pytest.mark.asyncio
async def test_c18_entries_come_from_the_attempt_that_succeeded_and_not_the_stale_one(
    db_session,
) -> None:
    """C18, API-SHAPED. A losing optimistic-lock attempt must leave no trace in the journal.

    `Debt` carries `version_id_col` (`app/db/models/debt.py:23`), so an ORM writer whose row was
    bumped underneath it raises `StaleDataError` on flush, and `PaymentEngine._apply_flow`
    (`app/core/payments/engine.py:1465-1553`) answers by expiring the identity map and retrying up
    to three times. The journal has to survive that: the attempt that raised wrote no row, so it
    must contribute no entry, and the entry the retry writes must carry the CONCURRENT value as
    `amount_before` - not the value this session had loaded before losing the race. A journal that
    captured `amount_before` when the attribute was first loaded would record a continuity that
    never existed, and step 6 would reconstruct the edge from a number no database ever held.

    TWO SESSIONS, ONE CONNECTION, and that is not a compromise. Since T1525 this tier takes real
    snapshots, so two independent SQLite writers cannot coexist - the second fails with
    `database is locked`, which is a fact about the stand and not about the journal. Sharing one
    `Connection` is also the HARDER case for the design under test: one Core root transaction, two
    `Session` identities, which is the shape of clearing's `work_session`
    (`app/core/clearing/service.py:1573`) and exactly where a registry that keyed operations by the
    transaction alone would accept a foreign session's write. The two-backend form is on PostgreSQL.

    RED TODAY BECAUSE: `debt_journal_entries` does not exist.
    MUTATION once step 4 exists: capture `amount_before` in `before_flush` from
    `attributes.get_history(...).unchanged[0]` without re-reading after the retry's `expire_all()`,
    or write entries in `before_flush` rather than `after_flush`, so the failed attempt leaves one.
    """
    from tests.conftest import TestingSessionLocal as factory, engine
    from sqlalchemy.ext.asyncio import AsyncSession

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("stale-retry")
    stale_errors: list[StaleDataError] = []
    try:
        async with factory() as setup:
            setup.add(world.debt("10.00"))
            await setup.commit()

        mine_debt = select(Debt).where(Debt.equivalent_id == world.equivalent.id)
        async with engine.connect() as connection:
            await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as mine, AsyncSession(
                bind=connection, expire_on_commit=False
            ) as theirs:
                async with await _open(api, mine, world, "stale-retry", identity=identity):
                    debt = (await mine.execute(mine_debt)).scalar_one()

                    # The competitor bumps `version` AFTER this session loaded the row and BEFORE
                    # it flushes - outside the savepoint below, so the rollback of the losing
                    # attempt cannot undo the competitor's work as well.
                    other = (await theirs.execute(mine_debt)).scalar_one()
                    other.amount = exact_money("31.00")
                    await theirs.flush()

                    # The shape of `_apply_flow` (`app/core/payments/engine.py:1474-1553`): each
                    # attempt inside its own savepoint, `expire_all()` between them.
                    for attempt in range(2):
                        try:
                            async with mine.begin_nested():
                                debt.amount = exact_money("44.00")
                                await mine.flush()
                            break
                        except StaleDataError as exc:
                            stale_errors.append(exc)
                            mine.expire_all()
                            debt = (await mine.execute(mine_debt)).scalar_one()
                    else:  # pragma: no cover - the loop breaks on the retry
                        pytest.fail("stand: the retry never succeeded")
                await connection.commit()

        entries = await stored_entries(factory, identity)
        after = await stored_debts(factory, world)

        # NON-VACUITY, FIRST: the race really happened, exactly once, and the retry really won.
        assert len(stale_errors) == 1, (
            f"stand: the concurrent version bump produced {len(stale_errors)} StaleDataError(s), "
            f"not one; without it this test measures nothing about a losing attempt"
        )
        assert after == {("debtor", "creditor", "eq"): Decimal("44.00000000")}, (
            f"stand: the retry did not win the edge: {after}"
        )
        assert entries is not None, missing_journal_tables(entries, ENTRIES_TABLE)

        # VERDICT.
        updates = [row for row in entries if row["effect"] == "U"]
        assert len(entries) == 1 and len(updates) == 1, (
            f"the losing attempt left a trace in the journal: {entries}. Only the flush that "
            f"reached the database may produce an entry."
        )
        assert _money(entries, "amount_before") == [Decimal("31.00000000")], (
            f"the retry's entry records `amount_before` as the value this session had loaded before "
            f"losing the race, not the value the database actually held: {entries}"
        )
        assert _money(entries, "delta") == [Decimal("13.00000000")], (
            f"the delta was computed against a state that never existed: {entries}"
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C19 - forged journal rows
# ==============================================================================================


#: Each row: `(label, table, column overrides, why the database must refuse it)`. The overrides are
#: applied on top of a well-formed row, so each case differs from a legal one in exactly the way it
#: names - a forgery that failed for two reasons at once would not show which CHECK caught it.
_SHAPE_INVALID_FORGERIES = [
    (
        "an insert that claims a previous amount",
        "entry",
        {"effect": "I", "amount_before": "5.00000000"},
        "an I has no before; a forged one would let a reconstruction start from an invented state",
    ),
    (
        "an update that changes nothing",
        "entry",
        {"effect": "U", "amount_before": "5.00000000", "amount_after": "5.00000000",
         "delta": "0.00000000"},
        "a U whose endpoints are equal is a zero-delta entry, and `delta <> 0` forbids it",
    ),
    (
        "a completed envelope with no digest",
        "operation",
        {"state": "COMPLETED", "effect_digest": None},
        "COMPLETED implies every completion column is present (design v2 §5)",
    ),
    (
        "a completed envelope with a negative effect count",
        "operation",
        {"state": "COMPLETED", "effect_count": -1},
        "`effect_count >= 0`",
    ),
    (
        "an envelope written by a schema this build does not know",
        "operation",
        {"schema_version": 2},
        "`schema_version IN (1)` - a row from a future encoding must not be read as if it were this one",
    ),
]


@pytest.mark.parametrize(
    "label,table,overrides,why",
    _SHAPE_INVALID_FORGERIES,
    ids=[row[0] for row in _SHAPE_INVALID_FORGERIES],
)
@pytest.mark.asyncio
async def test_c19_a_shape_invalid_forged_row_is_refused_by_the_database(
    db_session, label, table, overrides, why
) -> None:
    """C19, API-SHAPED. The write guard is not the last line; the CHECK constraints are.

    Design v2 §6's `before_execute` guard does not see `text()` or `exec_driver_sql`, and says so.
    That documented hole is only acceptable because the SHAPE of a journal row is enforced by the
    database itself: a forgery that gets past the guard still has to satisfy every CHECK in design
    v2 §5. This counterexample is the inventory of what those CHECKs must actually reject.

    RED TODAY BECAUSE: the journal tables do not exist, so the forged INSERT fails with "no such
    table" - which is NOT a CHECK refusal and must not be allowed to read as one. The non-vacuity
    assertion is therefore placed first and demands the table.
    MUTATION once step 4 exists: drop the named CHECK from migration 021; this case must go red
    while the others stay green.
    """
    from tests.conftest import TestingSessionLocal as factory

    target = OPERATIONS_TABLE if table == "operation" else ENTRIES_TABLE
    probe = await stored_rows(factory, f"SELECT 1 FROM {target} LIMIT 1")  # noqa: S608

    # NON-VACUITY, FIRST.
    assert probe is not None, missing_journal_tables(probe, target)

    world = await seed_world(factory)
    try:
        error = await _forge(factory, world, table, overrides)

        # VERDICT.
        assert error is not None, (
            f"the database accepted a forged journal row - {label}. {why}. A raw writer that gets "
            f"past the `before_execute` guard (design v2 §6 does not intercept `text()` or "
            f"`exec_driver_sql`, and says so) must still be stopped by the CHECK constraints of "
            f"migration 021; otherwise the guard's documented hole is a hole in the money history."
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c19_a_shape_valid_lie_is_accepted_and_is_therefore_step_6_s_job(db_session) -> None:
    """C19, API-SHAPED. A recorded LIMIT, not a requirement - and it is red for the same reason.

    `after - before = delta` cannot be a CHECK: on SQLite the columns are REAL, and a cross-row
    constraint is not portable at all (design v2 §5). So a forged entry whose three money columns
    are individually legal but do not agree with each other WILL be accepted by the database. That
    is not a defect to fix in step 4; it is the precise boundary between what the schema can promise
    and what step 6's verifier has to check, and writing it down as an executable expectation is how
    the boundary stops being folklore.

    This test therefore asserts that the forgery IS accepted. It is red today because the table does
    not exist - the non-vacuity assertion below says so - and once step 4 lands it becomes a green
    guard whose failure would mean the boundary moved and step 6's acceptance list is out of date.
    """
    from tests.conftest import TestingSessionLocal as factory

    probe = await stored_rows(factory, f"SELECT 1 FROM {ENTRIES_TABLE} LIMIT 1")  # noqa: S608
    assert probe is not None, missing_journal_tables(probe, ENTRIES_TABLE)

    world = await seed_world(factory)
    try:
        error = await _forge(
            factory,
            world,
            "entry",
            {
                "effect": "U",
                "amount_before": "5.00000000",
                "amount_after": "6.00000000",
                # Every column is legal on its own; together they are a lie.
                "delta": "99.00000000",
            },
        )
        assert error is None, (
            f"the database refused a SHAPE-VALID forged entry: {error!r}. If migration 021 really "
            f"can reject this, design v2 §5's decision to leave `after - before = delta` to the "
            f"step-6 verifier is out of date and the step-6 acceptance list must be revised - this "
            f"is a specification change, not a passing test."
        )
    finally:
        await drop_world(factory, world)


def _envelope_row(operation_id: uuid.UUID, overrides: dict) -> dict:
    """A well-formed OPEN envelope, then `overrides` on top. Completion columns are NULL."""
    row = {
        "id": str(operation_id),
        "kind": "TEST_FIXTURE",
        "identity": _identity("forgery"),
        "tx_id": None,
        "intent": "{}",
        "intent_digest": "0" * 64,
        "schema_version": 1,
        "money_encoding_version": 1,
        "intent_encoding_version": 1,
        # Written explicitly rather than left to a server default. If step 4 makes `opened_at` NOT
        # NULL without one, every forgery below would fail on THAT and the test would pass without
        # the CHECK it names ever being exercised - a green for the wrong reason, which is what
        # these counterexamples exist to prevent.
        "opened_at": "2026-09-12T00:00:00+00:00",
        "state": "OPEN",
        "completed_at": None,
        "flush_count": None,
        "effect_count": None,
        "effect_digest": None,
    }
    row.update(overrides)
    # A COMPLETED envelope needs every completion column, so the cases that forge one supply only
    # the column they are lying about and this fills in legal values for the rest. Without it a
    # "COMPLETED with no digest" forgery would also be missing three other NOT-NULL-when-COMPLETED
    # columns, and a refusal would not say which CHECK caught it.
    if row["state"] == "COMPLETED":
        defaults = {
            "completed_at": "2026-09-12T00:00:00+00:00",
            "flush_count": 1,
            "effect_count": 1,
            "effect_digest": "1" * 64,
        }
        for column, value in defaults.items():
            if column not in overrides:
                row[column] = value
    return row


def _entry_row(operation_id: uuid.UUID, world: World, overrides: dict) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "operation_id": str(operation_id),
        "flush_ordinal": 1,
        "equivalent_id": str(world.equivalent.id),
        "debtor_id": str(world.debtor.id),
        "creditor_id": str(world.creditor.id),
        "effect": "U",
        "amount_before": "5.00000000",
        "amount_after": "6.00000000",
        "delta": "1.00000000",
    }
    row.update(overrides)
    return row


def _insert(table: str, row: dict) -> str:
    columns = list(row)
    return (
        f"INSERT INTO {table} "  # noqa: S608
        f"({', '.join(columns)}) VALUES ({', '.join(':' + name for name in columns)})"
    )


async def _forge(factory, world: World, table: str, overrides: dict):
    """INSERT a raw journal row, well-formed except for `overrides`. Returns the error, or None.

    An entry needs an envelope to point at (`operation_id` is a RESTRICT foreign key), so the entry
    cases insert BOTH in one transaction under the same `operation_id`. If the envelope were the
    thing that failed, the returned error would be about the envelope and the entry's CHECK would
    never be exercised - so the envelope is inserted unmodified and its failure is reported
    separately by the assertion in the caller, which names the table it expected to hear from.
    """
    operation_id = uuid.uuid4()
    async with factory() as session:
        try:
            envelope = _envelope_row(operation_id, overrides if table == "operation" else {})
            await session.execute(text(_insert(OPERATIONS_TABLE, envelope)), envelope)
            if table == "entry":
                entry = _entry_row(operation_id, world, overrides)
                await session.execute(text(_insert(ENTRIES_TABLE, entry)), entry)
            await session.commit()
        except DatabaseError as exc:
            await session.rollback()
            return exc
    return None
