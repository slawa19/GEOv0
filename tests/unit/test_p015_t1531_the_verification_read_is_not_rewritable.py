"""Programme 015, step 4, T1531: the readback went through the pipeline it was checking.

WHAT THE THIRD REVIEW CIRCLE FOUND (2026-09-13). `_reconcile` is the boundary T1528 put the contract
on, and its own SELECT went through `before_execute` like everything else - so a neighbour registered
after this module's listener could replace it. Worse, the journal MARKED that statement with an
execution option (`geo_journal_statement`) so that R4's tracer could tell the mechanism's statements
from the work's, and that mark was an exact ORACLE for finding the one statement an attacker wants to
find. It was spoofable in the other direction too: a writer could put the same option on its own
statement and be filtered out of the trace as journal noise. There was no anti-spoof test.

MEASURED AT `64f92d2`, the scenario in full: an `Engine`-instance `before_execute` listener recognised
the marked SELECT, returned a `Select` projecting the literal `11.00000000` in place of
`debts.amount`, and rewrote the debt UPDATE to write 12. `debts` held 12, the journal recorded
`10 -> 11, delta 1`, and nothing refused.

WHAT CHANGED, AND WHAT DID NOT - both are asserted here, because the second half is the part a
closure report would quietly drop:

* the journal's verification reads go through `exec_driver_sql`, which dispatches NO `before_execute`
  at all. VERIFIED on SQLAlchemy 2.0.25 rather than taken from the review, and in the same test the
  module's own former claim about `text()` is falsified: `text()` DOES dispatch `before_execute`.
* provenance is held by the mechanism (`journal_statement_is_own`) instead of asserted by the
  statement, so a writer cannot wear it and nothing points at the read.
* `before_cursor_execute` IS STILL DISPATCHED for `exec_driver_sql`, and a neighbour that registers
  it with `retval=True` can still rewrite the SQL and the parameters. That surface is narrower - raw
  SQL rather than a typed clause, and no oracle - and it is NOT closed. The test at the bottom
  measures it open, on purpose: when someone closes it, that test goes red and the docstrings that
  say it is open have to change with it.

TIER. PostgreSQL since programme 017 stage 3 (2026-09-24): the stand's own engine over the tier
database, real root commits, a world of its own purged after each test (`tests/p015_b4a_stand.py::
new_postgres_stand`). Until then these rules were measured ONLY on SQLite, and the PostgreSQL
modules named below covered only what SQLite could not see. The PostgreSQL halves - asyncpg's
`numeric_dollar` placeholders, its native `uuid` spelling and its `NUMERIC` results - are in
`tests/integration/test_p015_t1530_delta_arithmetic_postgres.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, literal, select, text
from sqlalchemy.exc import InvalidRequestError

from app.core.ledger import journal
from app.db.models.debt import Debt
from tests.p015_b4a_stand import Stand, exact_money, identity, new_postgres_stand


@pytest_asyncio.fixture
async def stand():
    built = await new_postgres_stand()
    try:
        yield built
    finally:
        await built.close(purge=True)


_SCENARIO_END = (journal.DebtJournalError, InvalidRequestError)


async def _refusal_of(awaitable) -> BaseException | None:
    try:
        await awaitable
    except journal.DebtJournalError as exc:
        return exc
    return None


async def _committed_debt(stand: Stand, amount: str) -> uuid.UUID:
    async with stand.factory() as session:
        subject = stand.debt(amount)
        async with stand.operation("t1531-seed", session=session):
            session.add(subject)
            await session.flush()
        await session.commit()
        return subject.id


async def _stored_amount(stand: Stand, debt_id: uuid.UUID) -> Decimal | None:
    async with stand.factory() as fresh:
        return (
            await fresh.execute(select(Debt.amount).where(Debt.id == debt_id))
        ).scalar_one_or_none()


# =================================================================================================
# The scenario that was durable
# =================================================================================================


@pytest.mark.asyncio
async def test_t1531_a_before_execute_neighbour_cannot_see_the_verification_read(
    stand: Stand,
) -> None:
    """T1531 P1, DEFECT-SHAPED. `debts` held 12 while the journal recorded 11, and nothing refused.

    THE REVIEWER'S SCENARIO with the oracle removed from the attacker's hands. The listener still does
    both halves: it rewrites the debt UPDATE to write 12 (which lands AFTER the journal's guard, so
    only a readback can catch it), and it blinds any `SELECT` of `debts.amount` it can reach. The
    second half now reaches nothing, because the readback is issued through `exec_driver_sql`.

    THE PREMISE IS ASSERTED IN BOTH DIRECTIONS, which is what makes this more than a green run: the
    money tamper really fired, AND the readback really never appeared in `before_execute` - so the
    refusal is the readback seeing 12, not the attacker failing to install itself.

    MUTATION that must redden this: put `_reconcile`'s read back on `conn.execute(select(...))`. The
    blinding then lands, the refusal disappears, and `debts` keeps 12 under a record that says 11.
    """

    ident = identity("t1531-blind")
    debt_id = await _committed_debt(stand, "10.00")
    moved: list[str] = []
    selects_seen: list[str] = []
    refusal: BaseException | None = None

    @event.listens_for(stand.engine.sync_engine, "before_execute", retval=True)
    def _attacker(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        name = type(clause).__name__
        if name == "Select":
            compiled = str(clause)
            selects_seen.append(" ".join(compiled.split()))
            if "debts.amount" in compiled and "IN (" in compiled:
                # THE BLINDING, if it could ever reach the journal's read: project a literal in place
                # of the stored amount so the comparison passes against a row it never looked at.
                blinded = select(
                    Debt.__table__.c.id,
                    Debt.__table__.c.equivalent_id,
                    Debt.__table__.c.debtor_id,
                    Debt.__table__.c.creditor_id,
                    literal(Decimal("11.00000000")).label("amount"),
                ).where(clause.whereclause)
                return blinded, multiparams, params
        table = getattr(getattr(clause, "table", None), "name", None)
        if table == "debts" and name.endswith("Update") and isinstance(params, dict) and params:
            if "amount" in params:
                changed = dict(params)
                changed["amount"] = Decimal("12.00000000")
                moved.append("moved-money")
                return clause, multiparams, changed
        return clause, multiparams, params

    try:
        async with stand.factory() as session:
            async with stand.operation("t1531-blind", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
        refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", _attacker)

    entries = await stand.entries(ident)
    stored = await _stored_amount(stand, debt_id)

    # NON-VACUITY, FIRST HALF: the attacker was installed and its money tamper landed.
    assert moved == ["moved-money"], (
        f"stand: the UPDATE was never rewritten ({moved}), so nothing needed catching here"
    )

    # NON-VACUITY, SECOND HALF AND THE REAL SUBJECT: the journal's readback never arrived.
    readbacks = [sql for sql in selects_seen if "debts.amount" in sql and "IN (" in sql]
    assert readbacks == [], (
        f"the journal's verification read reached `before_execute`, where a neighbour can replace it: "
        f"{readbacks}"
    )

    # VERDICT: the readback saw the real row.
    assert refusal is not None, (
        f"`debts` holds {stored} under a record that says {entries}, and nothing refused"
    )
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNRECONCILED_DEBT_ROW, refusal
    assert stored == exact_money("10.00"), f"the refused write is durable: {stored}"
    assert entries == [], f"a refused operation still has entries: {entries}"


# =================================================================================================
# Provenance the mechanism owns
# =================================================================================================


@pytest.mark.asyncio
async def test_t1531_a_writers_statement_cannot_claim_the_journals_provenance(stand: Stand) -> None:
    """T1531. ANTI-SPOOF, and there was no such test before.

    WHAT THIS REPLACED. R4's tracer told the journal's statements from the writer's by reading an
    execution option the journal put on its own statement, and it filtered everything carrying that
    option out of the compared trace. A writer that set the same option was therefore invisible to the
    check that arming adds nothing but the journal's own statements - a spoof in the direction nobody
    had tested.

    WHAT IS ASSERTED. A writer's statement carrying the old option's name is NOT the journal's, and a
    statement issued while the journal is executing IS - both read through the public predicate, at the
    moment the statement runs. Provenance held by the mechanism cannot be put on a statement.

    THE MUTATION IS ON THE INSTRUMENT, AND THAT IS NOT AN EVASION - it is the measured shape of the
    property. `journal_statement_is_own` is handed a CONNECTION and nothing else, so there is no
    statement for it to read a mark off: the spoof is not merely refused, it is unrepresentable through
    that signature, and no mutation of the function reintroduces it (tried, 2026-09-13: adding a read of
    `conn._execution_options` leaves this test green, because a statement-level option is not on the
    connection). What the mutation has to show is that the ASSERTION has teeth, so it is applied to the
    observation: replace `journal_statement_is_own(conn)` in this test's own listener with
    `context.execution_options.get("geo_journal_statement")` - the old mechanism - and this test turns
    red on the writer's marked statement. Measured.
    """

    ident = identity("t1531-spoof")
    debt_id = await _committed_debt(stand, "10.00")
    observed: list[tuple[str, bool]] = []

    @event.listens_for(stand.engine.sync_engine, "after_cursor_execute")
    def _observe(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        observed.append((" ".join(statement.split()), journal.journal_statement_is_own(conn)))

    try:
        async with stand.factory() as session:
            # THE SPOOF: a writer's own read, wearing the mark the journal used to use.
            await session.execute(
                select(Debt.amount)
                .where(Debt.id == debt_id)
                .execution_options(**{"geo_journal_statement": True})
            )
            async with stand.operation("t1531-spoof", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                await session.flush()
            await session.commit()
    finally:
        event.remove(stand.engine.sync_engine, "after_cursor_execute", _observe)

    spoofed = [own for sql, own in observed if "SELECT debts.amount" in sql and "WHERE" in sql]
    journal_reads = [
        own for sql, own in observed if sql.startswith("SELECT id, equivalent_id, debtor_id")
    ]
    entry_reads = [own for sql, own in observed if "FROM debt_journal_entries" in sql]

    # NON-VACUITY: both statements really ran.
    assert spoofed, f"stand: the writer's marked SELECT never ran: {[sql for sql, _ in observed]}"
    assert journal_reads, (
        f"stand: the journal's debt readback never ran: {[sql for sql, _ in observed]}"
    )
    assert entry_reads, "stand: the journal's entry readback never ran"

    # VERDICT.
    assert spoofed == [False] * len(spoofed), (
        "a writer's statement claimed the journal's provenance by carrying a mark, which is the "
        "spoof the execution option allowed"
    )
    assert all(journal_reads), "the journal's own debt readback was not recognised as its own"
    assert all(entry_reads), "the journal's own entry readback was not recognised as its own"
    assert journal.journal_statement_is_own(stand.engine) is False, (
        "provenance outlived the statement it belongs to"
    )


# =================================================================================================
# The SQLAlchemy facts this design rests on, measured rather than cited
# =================================================================================================


@pytest.mark.asyncio
async def test_t1531_text_dispatches_before_execute_and_exec_driver_sql_does_not(
    stand: Stand,
) -> None:
    """T1531. The pin under the whole design - and the correction of this module's own former claim.

    `_on_before_execute`'s docstring said "`exec_driver_sql` and `text()` are documented exceptions:
    they fire no `before_execute` at all". HALF OF THAT WAS FALSE, and the review was right: `text()`
    dispatches `before_execute` with a `TextClause`. The conclusion that a `text()` WRITE is unseen
    survives, but for a different reason - `_dml_tables` only recognises `UpdateBase` - and a correct
    conclusion under a false premise is the defect this programme has now caught in itself three
    times.

    Measured here on the live dialect rather than read out of SQLAlchemy's source, because the design
    of `_own_select` depends on the second half being true on THIS version (2.0.25).

    MUTATION that must redden this: none is needed - a SQLAlchemy upgrade is the mutation, which is
    the point of the pin.
    """

    seen: list[str] = []
    cursor_seen: list[str] = []

    @event.listens_for(stand.engine.sync_engine, "before_execute")
    def _before(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        seen.append(type(clause).__name__)

    @event.listens_for(stand.engine.sync_engine, "after_cursor_execute")
    def _cursor(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        cursor_seen.append(statement.strip())

    try:
        async with stand.engine.begin() as connection:
            seen.clear()
            cursor_seen.clear()
            await connection.execute(select(literal(1)))
            from_select = list(seen)

            seen.clear()
            await connection.execute(text("SELECT 2"))
            from_text = list(seen)

            seen.clear()
            cursor_seen.clear()
            await connection.exec_driver_sql("SELECT 3")
            from_driver, driver_cursor = list(seen), list(cursor_seen)
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", _before)
        event.remove(stand.engine.sync_engine, "after_cursor_execute", _cursor)

    assert from_select == ["Select"], from_select
    assert from_text == ["TextClause"], (
        f"`text()` no longer dispatches `before_execute` ({from_text}). The module docstring's "
        f"correction - that the blindness to `text()` comes from `_dml_tables`, not from a missing "
        f"event - has to be re-derived."
    )
    assert from_driver == [], (
        f"`exec_driver_sql` dispatched `before_execute` ({from_driver}), so the journal's "
        f"verification reads are rewritable again and T1531 is reopened"
    )
    assert driver_cursor == ["SELECT 3"], (
        f"`exec_driver_sql` did not reach the cursor events ({driver_cursor}); the savepoint account "
        f"of T1532 and this module's honest statement about `before_cursor_execute` both depend on it"
    )


@pytest.mark.asyncio
async def test_t1531_before_cursor_execute_is_still_a_surface_and_is_not_claimed_closed(
    stand: Stand,
) -> None:
    """T1531. WHAT IS NOT CLOSED, measured open so that nobody reports a closure that did not happen.

    `exec_driver_sql` removes `before_execute`. It does NOT remove `before_cursor_execute`, which a
    neighbour may register with `retval=True` to replace the SQL string and the parameters
    (`ConnectionEvents.before_cursor_execute`). This test does exactly that to the journal's debt
    readback - returning a statement that reports the amount the journal wants to see while the row
    holds something else - and asserts that the tamper SUCCEEDS.

    IT IS A MEASUREMENT AND NOT AN ACCEPTANCE. What it buys is that the exposure cannot rot quietly in
    a docstring: the day the surface is closed, this test goes red and the prose that calls it open has
    to be corrected in the same change. What is genuinely narrower than before is stated and not
    measured away - the neighbour must now rewrite raw SQL rather than a typed clause, and the
    execution option that used to point at this exact statement no longer exists.

    MUTATION that must redden this: nothing in the journal - this asserts a hole. It reddens when the
    hole closes.
    """

    ident = identity("t1531-cursor")
    debt_id = await _committed_debt(stand, "10.00")
    rewrote: list[str] = []
    moved: list[str] = []

    @event.listens_for(stand.engine.sync_engine, "before_cursor_execute", retval=True)
    def _attacker(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        flattened = " ".join(statement.split())
        if flattened.startswith("SELECT id, equivalent_id, debtor_id, creditor_id, amount FROM"):
            rewrote.append(flattened)
            return (
                stand.driver_sql(
                    "SELECT id, equivalent_id, debtor_id, creditor_id, 11.0 AS amount FROM debts "
                    "WHERE id IN (?)"
                ),
                parameters,
            )
        return statement, parameters

    @event.listens_for(stand.engine.sync_engine, "before_execute", retval=True)
    def _money(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        table = getattr(getattr(clause, "table", None), "name", None)
        if table == "debts" and type(clause).__name__.endswith("Update"):
            if isinstance(params, dict) and "amount" in params:
                changed = dict(params)
                changed["amount"] = Decimal("12.00000000")
                moved.append("moved-money")
                return clause, multiparams, changed
        return clause, multiparams, params

    refusal: BaseException | None = None
    try:
        async with stand.factory() as session:
            async with stand.operation("t1531-cursor", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
    except _SCENARIO_END as exc:  # noqa: B902
        refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_cursor_execute", _attacker)
        event.remove(stand.engine.sync_engine, "before_execute", _money)

    stored = await _stored_amount(stand, debt_id)
    entries = await stand.entries(ident)

    assert moved == ["moved-money"], f"stand: the money tamper did not land ({moved})"
    assert rewrote, (
        "the journal's readback was not recognisable in `before_cursor_execute`. If the statement "
        "moved, update this measurement; if the surface closed, say so in `_own_select` and in the "
        "module docstring - do not leave the prose claiming an exposure that no longer exists."
    )
    assert refusal is None and stored == exact_money("12.00000000") and len(entries) == 1, (
        "`before_cursor_execute` no longer reaches the verification read. THIS IS GOOD NEWS AND A "
        "DOCUMENTATION BUG: `_own_select`, `_reconcile` and this module's docstring all say the "
        f"surface is open. refusal={refusal!r} stored={stored} entries={entries}"
    )


def test_t1531_an_unknown_paramstyle_refuses_instead_of_binding_nothing() -> None:
    """T1531, ANTI-VACUUM for the hand-written SQL (AGENTS.md §9).

    The verification reads spell their own placeholders, so a dialect whose paramstyle this module has
    never run on is a dialect on which the read cannot be written. The honest outcome is a refusal:
    a read that bound nothing, or bound into the wrong syntax, would either raise something unrelated
    or - far worse - return no rows, and "no rows" reads exactly like "the row is gone".

    The two paramstyles this repository actually uses are measured by the reads themselves on both
    tiers; this test is about the third case.

    MUTATION that must redden this: give `_raw_params` a fallback (`marks = ["?"] * len(values)`) for
    an unrecognised paramstyle.
    """

    from types import SimpleNamespace

    for style, expected in (("qmark", "?"), ("numeric_dollar", "$1"), ("pyformat", "%(p0)s")):
        marks, params = journal._raw_params(SimpleNamespace(paramstyle=style), ["x"])
        assert marks == [expected], (style, marks)
        assert params in (("x",), {"p0": "x"}), (style, params)

    with pytest.raises(journal.DebtJournalError) as refused:
        journal._raw_params(SimpleNamespace(paramstyle="qmark2"), ["x"])
    assert refused.value.reason == journal.Reason.UNREADABLE_VERIFICATION, refused.value


def test_t1531_the_journal_no_longer_exports_a_mark_a_statement_can_carry() -> None:
    """T1531. The oracle is gone from the module's surface, not merely unused inside it.

    An execution option left exported is an option someone re-adopts: the mark was public
    (`JOURNAL_STATEMENT_OPTION` in `__all__`) precisely so a tracer could read it, and a tracer is
    what has to be changed instead. This asserts the replacement is the public answer.

    MUTATION that must redden this: re-export the constant.
    """

    assert not hasattr(journal, "JOURNAL_STATEMENT_OPTION"), (
        "the spoofable mark is back on the journal's surface"
    )
    assert "journal_statement_is_own" in journal.__all__
    assert "JOURNAL_STATEMENT_OPTION" not in journal.__all__
