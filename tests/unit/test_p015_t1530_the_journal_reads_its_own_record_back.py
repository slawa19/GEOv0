"""Programme 015, step 4, T1530: the journal read the debt row back and never read its own record.

WHAT THE THIRD REVIEW CIRCLE OF THE T1528 DELTA FOUND (2026-09-13), and it is the mirror of the hole
T1528 closed. `_reconcile` confirms that `debts` holds what the journal is about to claim, and only
THEN is the entry inserted. Three things meet at that point and all three were measured on this tree
before anything here existed:

* the write guard passes the entry INSERT because `_INTERNAL` is set, and compares its values with
  nothing at all (`journal.py`, the `_INTERNAL` branch of `_on_before_execute`);
* `delta` was bounded, non-zero and not-NaN, and was NOT required to be `amount_after -
  amount_before` - there was no such constraint in `app/db/journal_tables.py`;
* `_complete` then digests the STORED rows.

So a `before_execute` listener registered after this module's - an `Engine`-instance listener, which
SQLAlchemy runs after every class-level one - could rewrite the entry INSERT. MEASURED at `64f92d2`,
both shapes, with no refusal anywhere:

    tamper                              debts      stored entry            envelope
    amount_after +1 and delta +1        11         10 -> 12, delta 2       COMPLETED, digest over it
    delta +1 alone                      11         10 -> 11, delta 2       COMPLETED, digest over it

None of the T1528 counterexamples catches either: their listeners target `debts`, never
`debt_journal_entries`.

TWO LAYERS, AND THEY CATCH DIFFERENT THINGS - which is the first thing to say, because the brief's
own example shows why. `amount_after = 12, delta = 2` over a before of 10 is arithmetically CONSISTENT,
so a database constraint on the arithmetic does not see it; and `delta = 2` over `10 -> 11` is a row
that contradicts itself, which a readback against the computed effects sees and a reader of the table
alone does not. Neither layer subsumes the other:

1. A DATABASE CONSTRAINT, `chk_debt_journal_entries_delta_arithmetic`, below the entire listener
   pipeline, installed on PostgreSQL (`app/db/journal_tables.py`; the test that measured why SQLite
   could not carry it left with SQLite in 017 stage 3). On both construction paths it is
   `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`.
2. A READBACK OF THE ENTRIES (`journal.py::_verify_entries`), on the same discipline as `_reconcile`:
   after the INSERT has run, the stored rows are read back and required to be exactly the `_Effect`
   list, as a multiset, in both directions. Plus a membership check at completion, so the digest is
   taken over rows the journal can account for.

TIER. PostgreSQL since programme 017 stage 3 (2026-09-24): the stand's own engine over the tier
database, real root commits, a world of its own purged after each test (`tests/p015_b4a_stand.py::
new_postgres_stand`). Until then these rules were measured ONLY on SQLite, and the PostgreSQL
modules named below covered only what SQLite could not see. The delta-contradiction test runs on a
disposable clone without the arithmetic CHECK (see `stand_without_the_arithmetic_check`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.exc import InvalidRequestError

from app.core.ledger import journal
from app.db.journal_tables import debt_journal_entries
from app.db.models.debt import Debt
from tests.p015_b4a_stand import Stand, arm_stand, exact_money, identity, new_postgres_stand


@pytest_asyncio.fixture
async def stand():
    built = await new_postgres_stand(extra_participants=1)
    try:
        yield built
    finally:
        await built.close(purge=True)


#: The PostgreSQL-only CHECK that holds `delta = amount_after - amount_before` in the table itself.
_ARITHMETIC_CHECK = "chk_debt_journal_entries_delta_arithmetic"


@pytest_asyncio.fixture
async def stand_without_the_arithmetic_check(committed_database):
    """A stand on a DISPOSABLE clone (mode B) from which the delta-arithmetic CHECK is dropped.

    WHY: on PostgreSQL that CHECK refuses an entry whose delta contradicts its own ends before the
    journal's readback can see it - which is correct and is asserted where it belongs
    (`tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`). But the readback's own delta
    comparison is a rule of the journal, and on the tier database it can never be reached, so its
    mutation would stay green. On a clone that is dropped when the test ends, removing the CHECK
    isolates the stage that owns the rule; nothing outside this test ever sees the altered schema.
    `DROP CONSTRAINT` without `IF EXISTS` is the non-vacuity: it fails unless the CHECK was there.
    """

    async with committed_database.engine.begin() as connection:
        await connection.exec_driver_sql(
            f"ALTER TABLE {debt_journal_entries.name} DROP CONSTRAINT {_ARITHMETIC_CHECK}"
        )
    built = await arm_stand(committed_database.engine, extra_participants=1)
    try:
        yield built
    finally:
        await built.close()


#: What the rest of a scenario raises once a refusal has been swallowed to reach the assertions.
_SCENARIO_END = (journal.DebtJournalError, InvalidRequestError)


async def _refusal_of(awaitable) -> BaseException | None:
    try:
        await awaitable
    except journal.DebtJournalError as exc:
        return exc
    return None


async def _committed_debt(stand: Stand, amount: str, **edge: Any) -> uuid.UUID:
    async with stand.factory() as session:
        subject = stand.debt(amount, **edge)
        async with stand.operation("t1530-seed", session=session):
            session.add(subject)
            await session.flush()
        await session.commit()
        return subject.id


async def _stored_amount(stand: Stand, debt_id: uuid.UUID) -> Decimal | None:
    async with stand.factory() as fresh:
        return (
            await fresh.execute(select(Debt.amount).where(Debt.id == debt_id))
        ).scalar_one_or_none()


def _tamper_entries(engine, rewrite) -> Any:
    """A `before_execute` listener on the ENGINE INSTANCE that rewrites the entry INSERT's rows.

    ON THE INSTANCE, which is what makes the scenario the one T1530 describes: SQLAlchemy runs every
    class-level listener before every instance-level one, and the journal is armed on the `Engine`
    CLASS. So this listener sees the statement the journal's own guard has already approved.

    `conn.execute(insert(table), rows)` puts the rows in `multiparams` and nothing in `params`, as
    either a tuple of dicts or a one-element tuple holding a list of them
    (`sqlalchemy/engine/base.py::_invoke_before_exec_event`). BOTH shapes are handled, because a
    helper that silently recognises neither is a counterexample that measures nothing - which is why
    the listener records that it fired and every test asserts that it did.
    """

    fired: list[str] = []

    @event.listens_for(engine.sync_engine, "before_execute", retval=True)
    def _rewrite(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        table = getattr(getattr(clause, "table", None), "name", None)
        if table != debt_journal_entries.name or not type(clause).__name__.endswith("Insert"):
            return clause, multiparams, params
        if isinstance(params, dict) and params:
            # ONE ROW IS A DIFFERENT SHAPE AGAIN: SQLAlchemy distils a single-element list into
            # `params`, so a helper that only looked at `multiparams` would miss every single-effect
            # flush - which is most of them.
            changed = rewrite([dict(params)])
            if changed is None:
                return clause, multiparams, params
            fired.append("rewrote")
            if len(changed) == 1:
                return clause, multiparams, changed[0]
            # A ROW ADDED TO A SINGLE-ROW STATEMENT has to move to `multiparams`, and `params` has
            # to be emptied with it: SQLAlchemy refuses a handler that returns both
            # ("Event handler can't return non-empty multiparams and params at the same time").
            return clause, tuple(changed), {}
        items = list(multiparams or ())
        if items and all(isinstance(item, dict) for item in items):
            rows, nested = [dict(item) for item in items], False
        elif len(items) == 1 and isinstance(items[0], (list, tuple)):
            rows, nested = [dict(row) for row in items[0]], True
        else:
            return clause, multiparams, params
        changed = rewrite(rows)
        if changed is None:
            return clause, multiparams, params
        fired.append("rewrote")
        return clause, ((changed,) if nested else tuple(changed)), params

    return _rewrite, fired


# =================================================================================================
# The two tampers the review named
# =================================================================================================


@pytest.mark.asyncio
async def test_t1530_an_entry_whose_amount_was_rewritten_after_the_guard_is_refused(
    stand: Stand,
) -> None:
    """T1530 P1, DEFECT-SHAPED. The record said `10 -> 12, delta 2` while `debts` held 11.

    THE REVIEWER'S SCENARIO, VERBATIM. The arithmetic of the stored row is CONSISTENT, so the database
    constraint this slice also adds does not see it; what makes it false is that it disagrees with the
    effect the flush hook computed and with the row `_reconcile` had just verified. Measured at
    `64f92d2`: commit allowed, `debts` 11, entry `10 -> 12, delta 2`, envelope COMPLETED with
    `effect_count = 1` and a digest over the tampered row.

    MUTATION, AND IT HAD TO BE CORRECTED BY MEASUREMENT. Deleting the `_verify_entries(...)` call from
    `_after_flush` alone leaves this GREEN - measured 2026-09-13 - because the completion membership
    check catches the same tamper one step later, at the digest. The mutation that reddens exactly this
    is both halves at once: delete that call AND make `_verify_completed_entries` a no-op. The two layers
    overlap here on purpose, and the sentence that used to stand in this docstring ("there is no second
    line behind it on this tier") was wrong.
    """

    ident = identity("t1530-amount")
    debt_id = await _committed_debt(stand, "10.00")

    def _bump_both(rows: list[dict]) -> list[dict]:
        for row in rows:
            if row.get("amount_after") is None:
                return None
            row["amount_after"] = Decimal(row["amount_after"]) + 1
            row["delta"] = Decimal(row["delta"]) + 1
        return rows

    listener, fired = _tamper_entries(stand.engine, _bump_both)
    refusal: BaseException | None = None
    try:
        async with stand.factory() as session:
            async with stand.operation("t1530-amount", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
        refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", listener)

    stored = await _stored_amount(stand, debt_id)
    entries = await stand.entries(ident)
    envelopes = await stand.envelopes(ident)

    # NON-VACUITY: the tamper really rewrote the journal's own INSERT.
    assert fired == ["rewrote"], (
        f"stand: the listener never rewrote the entry INSERT ({fired}), so nothing here was tampered "
        f"with and this test measures nothing"
    )

    # VERDICT.
    assert refusal is not None, (
        f"the journal recorded a movement that did not happen and nothing refused: `debts` holds "
        f"{stored} while the entries say {entries} and the envelope is {envelopes}"
    )
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNRECORDED_JOURNAL_ENTRY, refusal
    assert stored == exact_money("10.00"), f"the refused flush is durable: {stored}"
    assert entries == [], f"a refused operation still has entries: {entries}"
    assert envelopes == [], f"a refused operation still has an envelope: {envelopes}"


@pytest.mark.asyncio
async def test_t1530_an_entry_whose_delta_contradicts_its_own_ends_is_refused(
    stand_without_the_arithmetic_check: Stand,
) -> None:
    """T1530 P1, DEFECT-SHAPED. `amount_before = 10, amount_after = 11, delta = 2` was storable.

    THE SECOND SHAPE, AND IT IS THE ONE THE TABLE ITSELF SHOULD HAVE REFUSED. Measured at `64f92d2`
    on SQLite: the row was stored, the envelope completed, and `chk_debt_journal_entries_delta`
    (non-zero, bounded, not-NaN) was satisfied the whole time. Criterion (a) of design v2 - the
    journal's per-edge deltas equal the edge's final amount minus its initial one - is false for this
    edge from the moment that row exists.

    HERE THE READBACK IS WHAT REFUSES IT, under `unrecorded_journal_entry`. With the schema intact
    PostgreSQL refuses it first, under `chk_debt_journal_entries_delta_arithmetic`, and which of the
    two speaks first is asserted there rather than guessed here
    (`tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`). This test runs on a clone
    without that CHECK (see `stand_without_the_arithmetic_check`), because what it measures is the
    journal's own comparison and not the table's.

    MUTATION that must redden this again: compare only the edge and the amounts in `_effect_code`
    (drop `_money_key(effect.delta)` from it and the matching component from `_stored_entry_code`).
    The tampered delta then matches and the refusal disappears.
    """

    stand = stand_without_the_arithmetic_check
    ident = identity("t1530-delta")
    debt_id = await _committed_debt(stand, "10.00")

    def _bump_delta(rows: list[dict]) -> list[dict]:
        for row in rows:
            row["delta"] = Decimal(row["delta"]) + 1
        return rows

    listener, fired = _tamper_entries(stand.engine, _bump_delta)
    refusal: BaseException | None = None
    try:
        async with stand.factory() as session:
            async with stand.operation("t1530-delta", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902
        refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", listener)

    entries = await stand.entries(ident)

    assert fired == ["rewrote"], f"stand: the delta was never rewritten ({fired})"
    assert refusal is not None, (
        f"an entry whose delta contradicts its own before/after was stored and nothing refused it: "
        f"{entries}"
    )
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNRECORDED_JOURNAL_ENTRY, refusal
    assert entries == [], f"a refused operation still has entries: {entries}"
    assert await _stored_amount(stand, debt_id) == exact_money("10.00")


# =================================================================================================
# The two directions of the comparison, because one of them alone is half a check
# =================================================================================================


@pytest.mark.asyncio
async def test_t1530_an_entry_the_journal_never_computed_cannot_be_added(stand: Stand) -> None:
    """T1530. A row ADDED to the INSERT is a record of a movement that never happened.

    A readback that only checked "every effect I computed is stored" would pass this: all the journal's
    own rows are there, plus one more. The comparison is a multiset equality in BOTH directions for
    exactly this reason.

    MUTATION, MEASURED: dropping the `stored - recorded` half of `_verify_entries` alone leaves this
    green, because the completion membership check refuses the invented row at the digest instead. The
    mutation that reddens exactly this is both at once - `not (recorded - stored)` in `_verify_entries`
    AND a no-op `_verify_completed_entries`. Measured 2026-09-13; with both, this test and
    `test_t1530_an_entry_altered_after_its_flush_is_refused_before_the_digest` go red together, which is
    the honest statement of where each half bites.
    """

    ident = identity("t1530-extra")
    debt_id = await _committed_debt(stand, "10.00")
    other_creditor = stand.extra_ids[0]

    def _add_a_row(rows: list[dict]) -> list[dict]:
        invented = dict(rows[0])
        invented["id"] = uuid.uuid4()
        invented["creditor_id"] = other_creditor
        return [*rows, invented]

    listener, fired = _tamper_entries(stand.engine, _add_a_row)
    refusal: BaseException | None = None
    try:
        async with stand.factory() as session:
            async with stand.operation("t1530-extra", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902
        refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", listener)

    entries = await stand.entries(ident)
    assert fired == ["rewrote"], f"stand: no row was added ({fired})"
    assert refusal is not None, f"an invented entry was stored and nothing refused it: {entries}"
    assert refusal.reason == journal.Reason.UNRECORDED_JOURNAL_ENTRY, refusal
    assert entries == [], f"a refused operation still has entries: {entries}"


@pytest.mark.asyncio
async def test_t1530_an_entry_dropped_from_the_insert_is_refused(stand: Stand) -> None:
    """T1530. A row REMOVED from the INSERT is a movement with no record.

    The flush moves two edges, the listener lets only one of them through, and `debts` holds both
    changes - which `_reconcile` confirms, because `_reconcile` is about the debt rows and says nothing
    about how many entries were written. Without the second direction of the readback the operation
    would complete with one entry for two movements.

    MUTATION that must redden this, and it is the ONE mutation that isolates the per-flush readback:
    delete the `_verify_entries(...)` call from `_after_flush`. Measured 2026-09-13 - of the five
    counterexamples in this module, only this one turns red on that mutation alone. A MISSING entry is
    the one shape the completion check cannot refuse, because a missing entry is legitimate there (see
    the savepoint control below), so this readback is the only thing in the mechanism that sees it.
    """

    ident = identity("t1530-missing")
    first = await _committed_debt(stand, "10.00")
    second = await _committed_debt(stand, "20.00", creditor_id=stand.extra_ids[0])

    def _drop_one(rows: list[dict]) -> list[dict] | None:
        if len(rows) < 2:
            return None
        return rows[:1]

    listener, fired = _tamper_entries(stand.engine, _drop_one)
    refusal: BaseException | None = None
    try:
        async with stand.factory() as session:
            async with stand.operation(
                "t1530-missing",
                session=session,
                identity=ident,
                scope_equivalent_ids=frozenset({stand.equivalent_id}),
            ):
                one = await session.get(Debt, first)
                two = await session.get(Debt, second)
                one.amount = exact_money("11.00")
                two.amount = exact_money("21.00")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902
        refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", listener)

    entries = await stand.entries(ident)
    assert fired == ["rewrote"], f"stand: the second row was never dropped ({fired})"
    assert refusal is not None, (
        f"two debts moved and one entry was written, and nothing refused it: {entries}"
    )
    assert refusal.reason == journal.Reason.UNRECORDED_JOURNAL_ENTRY, refusal
    assert await _stored_amount(stand, first) == exact_money("10.00")
    assert await _stored_amount(stand, second) == exact_money("20.00")


# =================================================================================================
# The completion half: the digest is taken over rows the journal can account for
# =================================================================================================


@pytest.mark.asyncio
async def test_t1530_an_entry_altered_after_its_flush_is_refused_before_the_digest(
    stand: Stand,
) -> None:
    """T1530, completion half. `_verify_entries` closes the flush; this closes the window after it.

    THE WINDOW IS REAL AND IT IS NAMED IN `_reconcile`'S OWN DOCSTRING: `exec_driver_sql` fires no
    `before_execute`, so the write guard does not see a statement issued that way - it is this module's
    one documented blind spot. A neighbour can therefore alter a stored entry after the flush that
    wrote it has been verified, and before the envelope's digest is taken over it. The completion check
    reads every entry of the operation back and requires each stored row to be one of the effects this
    operation computed.

    WHY MEMBERSHIP AND NOT EQUALITY, stated in the code and asserted by the next test: entries can
    legitimately be MISSING, because a `StaleDataError` retry takes a flush's entries back with its
    savepoint (migration 023). Nothing can legitimately add or alter one.

    MUTATION that must redden this: make `_verify_completed_entries` a no-op. The digest is then
    taken over the altered row and the operation completes.
    """

    ident = identity("t1530-after")
    debt_id = await _committed_debt(stand, "10.00")
    altered = 0
    refusal: BaseException | None = None

    try:
        async with stand.factory() as session:
            async with stand.operation("t1530-after", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                await session.flush()
                # THE BLIND SPOT, USED DELIBERATELY: no `before_execute`, so the write guard never
                # sees this statement. It is the only door through which a stored entry can be
                # altered at all, which is why the counterexample uses it.
                connection = await session.connection()
                result = await connection.exec_driver_sql(
                    # `amount_after` moves WITH `delta`, so the row still satisfies PostgreSQL's
                    # arithmetic CHECK (10 -> 12 is +2): a tamper the table accepts, which leaves
                    # the journal's readback as the only thing that can refuse it.
                    stand.driver_sql(
                        f"UPDATE {debt_journal_entries.name} SET amount_after = 12.0, delta = 2.0 "
                        f"WHERE operation_id IN (SELECT id FROM debt_operations WHERE identity = ?)"
                    ),
                    (ident,),
                )
                altered = result.rowcount
            refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902
        refusal = refusal if refusal is not None else exc
    except journal.DebtJournalError as exc:
        refusal = exc

    entries = await stand.entries(ident)
    envelopes = await stand.envelopes(ident)

    # NON-VACUITY: the raw UPDATE really altered a stored entry.
    assert altered == 1, (
        f"stand: the raw UPDATE altered {altered} rows; nothing was tampered with after the flush"
    )

    assert refusal is not None, (
        f"an entry altered after its flush was digested and the operation completed: entries "
        f"{entries}, envelope {envelopes}"
    )
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNRECORDED_JOURNAL_ENTRY, refusal
    assert entries == [], f"a refused operation still has entries: {entries}"
    assert await _stored_amount(stand, debt_id) == exact_money("10.00")


@pytest.mark.asyncio
async def test_t1530_entries_a_savepoint_rollback_removed_are_not_a_disagreement(
    stand: Stand,
) -> None:
    """T1530, ANTI-VACUUM for the completion check (AGENTS.md §9). Missing entries are legitimate.

    A rule that refuses must be shown not to refuse the real case. `PaymentEngine._apply_flow` answers
    a `StaleDataError` by rolling back to a savepoint and flushing again: the losing attempt's flush
    was counted and its entries went away with the savepoint, which is exactly what migration 023 was
    written for. This reproduces that shape directly - two flushes inside one operation, the second
    inside a savepoint that rolls back - and requires the operation to COMPLETE.

    MUTATION that must redden this: make `_verify_completed_entries` a multiset EQUALITY instead of a
    membership test (`Counter(stored) != computed` in place of `Counter(stored) - computed`). The payment
    path's ordinary retry then cannot complete, which is the regression this test exists to catch.
    """

    ident = identity("t1530-rolledback")
    first = await _committed_debt(stand, "10.00")
    second = await _committed_debt(stand, "20.00", creditor_id=stand.extra_ids[0])

    async with stand.factory() as session:
        async with stand.operation("t1530-rolledback", session=session, identity=ident):
            one = await session.get(Debt, first)
            one.amount = exact_money("11.00")
            await session.flush()

            nested = await session.begin_nested()
            two = await session.get(Debt, second)
            two.amount = exact_money("21.00")
            await session.flush()
            await nested.rollback()
        await session.commit()

    entries = await stand.entries(ident)
    envelopes = await stand.envelopes(ident)

    # NON-VACUITY: there really were two flushes and only one survived.
    assert len(envelopes) == 1, envelopes
    assert envelopes[0]["flush_count"] == 2, (
        f"stand: the second flush was not counted, so nothing was rolled back here: {envelopes}"
    )
    assert envelopes[0]["state"] == "COMPLETED", (
        f"an ordinary savepoint retry could not complete its operation: {envelopes}"
    )
    assert envelopes[0]["effect_count"] == 1, envelopes
    assert [entry["delta"] for entry in entries] == [exact_money("1.00")], entries
    assert await _stored_amount(stand, first) == exact_money("11.00")
    assert await _stored_amount(stand, second) == exact_money("20.00")


# =================================================================================================
# The database constraint, and why it is on one dialect only
# =================================================================================================


def test_t1530_the_migration_and_the_metadata_spell_the_same_predicate() -> None:
    """T1530, layer 1. The two sources of the constraint cannot drift apart silently.

    NEEDS NO SERVER, WHICH IS THE POINT. The real comparison - what PostgreSQL actually holds after each
    construction path - is in `tests/integration/test_p015_t1530_delta_arithmetic_postgres.py` and needs
    a database. This one is the cheap half that runs in every default-tier gate: the predicate text in
    `app/db/journal_tables.py` and the one in migration `024_debt_journal_delta` are compared as
    strings, so an edit to either that is not made to both is red at once rather than at the next
    PostgreSQL run.

    MUTATION that must redden this: change the predicate in one of the two files.
    """

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "versions" / "024_debt_journal_entries_delta_is_arithmetic.py"
    ).read_text(encoding="utf-8")
    tables = (root / "app" / "db" / "journal_tables.py").read_text(encoding="utf-8")
    predicate = "delta = COALESCE(amount_after, 0) - COALESCE(amount_before, 0)"

    assert predicate in migration, (
        f"migration 024 no longer spells the predicate as `{predicate}`; if it moved, this comparison "
        f"has to move with it rather than be deleted"
    )
    assert predicate in tables, f"`app/db/journal_tables.py` no longer spells `{predicate}`"
    assert "chk_debt_journal_entries_delta_arithmetic" in migration
    assert "chk_debt_journal_entries_delta_arithmetic" in tables
