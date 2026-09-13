"""Programme 015, step 4, T1532: listener order is not winnable, so the account stopped depending on it.

WHAT WAS MEASURED, NOT TAKEN ON TRUST (2026-09-13, re-measured here). A `rollback_savepoint` listener
registered on the `Connection` CLASS runs before this module's, and it does so whether it is registered
before or after the connection exists - both orders give `['CLASS', 'instance(insert=True)']`. So the
sentence T1528 put in `_on_engine_connect` ("a neighbour added later cannot pre-empt it") is false as
written, and it is unwinnable in principle: `insert=True` is won by whoever registers last.

AND THE CONSEQUENCE WAS A DURABLE DEBT NOBODY ASKED TO KEEP. Measured at `64f92d2`: a class-level
neighbour raising in `rollback_savepoint` pre-empted the recorder, `pending_savepoint_rollbacks` stayed
EMPTY, the `ROLLBACK TO SAVEPOINT` statement was never issued at all, SQLAlchemy deactivated the nested
transaction in its `finally` anyway, the commit was NOT refused, and a debt of 42 the writer had asked
to undo became durable.

THE BRIEF'S PROPOSAL AND WHAT MEASUREMENT DID TO IT - both are here, because the second half is a
correction and not a decoration. The proposal was to invert `_confirm_savepoint_rollback`: make the SQL
observation primary and treat a rollback seen with no pending record as a refusal. That inversion is
right and is implemented (second test below). IT DOES NOT REACH THE SCENARIO ABOVE, and the measurement
says why in one line: **the pre-empted event prevented the statement, so there was no SQL to observe.**
An inversion of the confirmation has nothing to invert there.

WHAT DOES REACH IT is an account kept entirely in the SQL stream: `SAVEPOINT x` seen, an operation
bound to `x`, and SQLAlchemy no longer holding a nested transaction for it, with neither
`RELEASE SAVEPOINT x` nor `ROLLBACK TO SAVEPOINT x` ever observed. That is `LOST_SAVEPOINT_CLOSE`, and
it consults no event, so no listener ordering can take it away.

THE COST OF THE INVERTED DEFAULT WAS LOOKED FOR BEFORE IT SHIPPED, because a default that turns a
working path into a refusal is worse than the hole. Two things keep it narrow, and both are asserted
below: the gate is `name in op.chain` - the SAME gate `_on_rollback_savepoint` records under, so the
pair is symmetric - and the savepoints the payment path rolls back on a `StaleDataError` retry are
DEEPER than the operation's chain and are therefore outside both halves. The whole default SQLite tier
(2136 passed, 2 skipped, 222 deselected) was green with this in place.

WHAT REMAINS OPEN is in the last test, measured rather than argued.

TIER. SQLite, the default tier, one database file per test under `tmp_path`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import InvalidRequestError

from app.core.ledger import journal
from app.db.models.debt import Debt
from tests.p015_b4a_stand import Stand, exact_money, identity, new_sqlite_stand


@pytest_asyncio.fixture
async def stand(tmp_path):
    built = await new_sqlite_stand(tmp_path, extra_participants=2)
    try:
        yield built
    finally:
        await built.close()


class _PreventedTheRollback(BaseException):
    """What a neighbouring `rollback_savepoint` listener raises. Stands for any failure there."""


_SCENARIO_END = (journal.DebtJournalError, InvalidRequestError)


def _savepoint_sql(engine) -> tuple[list[str], object]:
    """Everything the SQL stream says about savepoints, in order."""

    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _watch(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        flattened = statement.strip()
        if flattened.upper().startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            seen.append(flattened)

    return seen, _watch


# =================================================================================================
# The scenario the T1528 fix did not reach
# =================================================================================================


@pytest.mark.asyncio
async def test_t1532_a_class_level_neighbour_that_prevents_a_rollback_cannot_hide_it(
    stand: Stand,
) -> None:
    """T1532 P1, DEFECT-SHAPED. The recorder was pre-empted and a debt of 42 was made durable.

    THE SCENARIO, AND IT IS THE ONE T1528 CLAIMED TO HAVE CLOSED. A completed operation inside a
    savepoint, and a neighbour on the `Connection` CLASS that raises in `rollback_savepoint`. The class
    level runs before every listener this module can register - including the per-connection one T1528
    added from `engine_connect` - so `pending_savepoint_rollbacks` stays empty. Measured at `64f92d2`:
    commit allowed, `debts` holding both 9 and 42, the savepoint's 42 never undone.

    WHAT REFUSES IT NOW, and it is not an event: the SQL stream shows `SAVEPOINT sa_savepoint_1` and
    neither of the two statements that can end it, while SQLAlchemy no longer holds a nested
    transaction for it. This test asserts that the SQL stream really is in that state, so the refusal
    is attributed to the fact it is built on rather than to a coincidence.

    MUTATION that must redden this: drop the `lost_savepoints` branch from `_blocking_problem` (or stop
    passing it from `_on_commit`). The commit is then allowed and `debts` holds 42 again.
    """

    outer = identity("t1532-outer")
    inner = identity("t1532-inner")
    prevented: BaseException | None = None
    commit_refusal: BaseException | None = None
    inside: Decimal | None = None
    recorded: list[str] = []
    sql, watcher = _savepoint_sql(stand.engine)

    def _a_neighbour_that_raises(conn, name, context) -> None:  # noqa: ANN001
        raise _PreventedTheRollback(f"a neighbour prevented the rollback of {name}")

    try:
        async with stand.factory() as session:
            async with stand.operation("t1532-outer", session=session, identity=outer):
                session.add(stand.debt("9.00"))
                await session.flush()

            sync_connection = (await session.connection()).sync_connection
            nested = await session.begin_nested()
            async with stand.operation("t1532-inner", session=session, identity=inner):
                session.add(stand.debt("42.00", creditor_id=stand.extra_ids[0]))
                await session.flush()
            inside = await session.scalar(
                select(Debt.amount).where(Debt.creditor_id == stand.extra_ids[0])
            )

            event.listen(Connection, "rollback_savepoint", _a_neighbour_that_raises)
            try:
                await nested.rollback()
            except BaseException as exc:  # noqa: BLE001 - the prevented rollback is the premise
                prevented = exc
            finally:
                event.remove(Connection, "rollback_savepoint", _a_neighbour_that_raises)

            state = journal._REGISTRY.get(sync_connection.get_transaction())
            recorded = sorted(state.pending_savepoint_rollbacks) if state is not None else []
            still_nested = sync_connection.get_nested_transaction()

            try:
                await session.commit()
            except BaseException as exc:  # noqa: BLE001 - the refusal is the subject
                commit_refusal = exc
    finally:
        event.remove(stand.engine.sync_engine, "after_cursor_execute", watcher)

    durable = await stand.stored_debts()

    # NON-VACUITY, FOUR HALVES, because every one of them is a premise of the verdict.
    assert inside == exact_money("42.00"), f"stand: the savepoint wrote nothing ({inside})"
    assert isinstance(prevented, _PreventedTheRollback), (
        f"stand: the neighbour did not prevent the rollback ({prevented!r})"
    )
    assert recorded == [], (
        f"stand: the journal DID record the rollback request ({recorded}), so this is the T1528 "
        f"scenario and not the class-level pre-emption T1532 is about"
    )
    assert still_nested is None, (
        "stand: SQLAlchemy still holds the nested transaction, so nothing was lost yet"
    )

    # AND THE FACT THE REFUSAL IS BUILT ON: the savepoint was opened in SQL and never closed there.
    assert [statement for statement in sql if "sa_savepoint_1" in statement] == [
        "SAVEPOINT sa_savepoint_1"
    ], (
        f"stand: the SQL stream is not in the state this refusal reads - {sql}. If a ROLLBACK TO or a "
        f"RELEASE is in there, the pre-emption did not prevent the statement and the inversion, not "
        f"this check, is what applies."
    )

    # VERDICT.
    assert commit_refusal is not None, (
        f"the root committed after a prevented savepoint rollback: `debts` holds {durable}"
    )
    assert isinstance(commit_refusal, journal.DebtJournalError), repr(commit_refusal)
    assert commit_refusal.reason == journal.Reason.LOST_SAVEPOINT_CLOSE, commit_refusal
    assert durable == {}, f"the savepoint's debt is durable: {durable}"


# =================================================================================================
# The inversion the brief asked for, and the work it must not refuse
# =================================================================================================


@pytest.mark.asyncio
async def test_t1532_a_savepoint_rollback_the_journal_never_recorded_is_a_refusal(
    stand: Stand,
) -> None:
    """T1532. The SQL observation is the primary fact; an unrecorded rollback is no longer a no-op.

    BEFORE THIS, `_confirm_savepoint_rollback` returned early when there was no pending record - so an
    observed rollback of a savepoint an operation is bound to, which the journal was never told about,
    did nothing at all. Here the rollback is issued through `exec_driver_sql`, which dispatches no
    `rollback_savepoint` event, so the journal learns of it ONLY from the statement.

    THE GATE IS THE SYMMETRIC ONE: `name in op.chain`. The next test is its anti-vacuum half.

    MUTATION that must redden this: restore the early `return` in `_confirm_savepoint_rollback` when
    `pending_savepoint_rollbacks.pop` answers None.
    """

    ident = identity("t1532-unrecorded")
    refusal: BaseException | None = None

    try:
        async with stand.factory() as session:
            nested = await session.begin_nested()
            async with stand.operation("t1532-unrecorded", session=session, identity=ident):
                session.add(stand.debt("7.00"))
                await session.flush()

            connection = await session.connection()
            name = connection.sync_connection.get_nested_transaction()._savepoint
            # NO EVENT AT ALL: `exec_driver_sql` dispatches no `rollback_savepoint`, so this is a
            # rollback the journal can only learn about from the SQL.
            await connection.exec_driver_sql(f"ROLLBACK TO SAVEPOINT {name}")

            state = journal._REGISTRY.get(connection.sync_connection.get_transaction())
            poison = state.poison if state is not None else None

            try:
                await nested.rollback()
            except BaseException:  # noqa: BLE001 - SQLAlchemy's own bookkeeping, not the subject
                pass
            try:
                await session.commit()
            except BaseException as exc:  # noqa: BLE001
                refusal = exc
    except _SCENARIO_END as exc:  # noqa: B902
        refusal = refusal if refusal is not None else exc

    durable = await stand.stored_debts()

    # NON-VACUITY: the poison was set by the statement, before anything else could refuse.
    assert poison == journal.Reason.UNRECORDED_SAVEPOINT_ROLLBACK, (
        f"an unrecorded rollback of a savepoint an operation is bound to did not poison the "
        f"transaction (poison={poison!r})"
    )
    assert refusal is not None, f"the commit was allowed: `debts` holds {durable}"
    assert journal.Reason.UNRECORDED_SAVEPOINT_ROLLBACK in str(refusal), refusal
    assert durable == {}, f"something became durable: {durable}"


@pytest.mark.asyncio
async def test_t1532_ordinary_savepoint_work_is_not_refused(stand: Stand) -> None:
    """T1532, ANTI-VACUUM for both new refusals (AGENTS.md §9). The real paths still work.

    THREE SHAPES, and the third is the one that would have broken the main payment path:

    1. a savepoint an operation is bound to, RELEASED normally;
    2. the same, ROLLED BACK normally through SQLAlchemy;
    3. a savepoint opened INSIDE an operation and rolled back - deeper than the operation's chain,
       which is the shape `PaymentEngine._apply_flow` produces on every `StaleDataError` retry
       (migration 023). Neither half of the new pair looks at it, and the operation must complete.

    MUTATION that must redden this: widen `_confirm_savepoint_rollback`'s gate from "bound to an
    operation" to "any savepoint", or widen `_lost_savepoint_closes` from `op.chain` to every observed
    savepoint. Shape 3 then refuses, and so does the payment path.
    """

    # 1. RELEASED.
    released = identity("t1532-released")
    async with stand.factory() as session:
        async with session.begin_nested():
            async with stand.operation("t1532-released", session=session, identity=released):
                session.add(stand.debt("3.00"))
                await session.flush()
        await session.commit()

    # 2. ROLLED BACK through SQLAlchemy.
    rolled = identity("t1532-rolled")
    async with stand.factory() as session:
        nested = await session.begin_nested()
        async with stand.operation("t1532-rolled", session=session, identity=rolled):
            session.add(stand.debt("4.00", creditor_id=stand.extra_ids[0]))
            await session.flush()
        await nested.rollback()
        await session.commit()

    # 3. A SAVEPOINT DEEPER THAN THE OPERATION - the payment retry's shape.
    retried = identity("t1532-retried")
    async with stand.factory() as session:
        async with stand.operation("t1532-retried", session=session, identity=retried):
            session.add(stand.debt("5.00", creditor_id=stand.extra_ids[1]))
            await session.flush()
            nested = await session.begin_nested()
            subject = stand.debt("6.00", equivalent_id=stand.equivalent_id, creditor_id=stand.debtor_id, debtor_id=stand.creditor_id)
            session.add(subject)
            await session.flush()
            await nested.rollback()
        await session.commit()

    envelopes = {
        row["identity"]: row["state"]
        for identity_value in (released, rolled, retried)
        for row in await stand.envelopes(identity_value)
    }
    durable = await stand.stored_debts()

    assert envelopes.get(released) == "COMPLETED", envelopes
    assert envelopes.get(retried) == "COMPLETED", (
        f"an operation whose inner savepoint rolled back could not complete - this is the payment "
        f"path's ordinary retry: {envelopes}"
    )
    # The rolled-back operation's envelope went with its savepoint, which is correct and is why it is
    # absent rather than COMPLETED.
    assert rolled not in envelopes, envelopes
    assert durable == {
        ("debtor", "creditor", "eq"): exact_money("3.00"),
        ("debtor", "extra1", "eq"): exact_money("5.00"),
    }, durable


# =================================================================================================
# The statements this account is read from, and what it still does not see
# =================================================================================================


@pytest.mark.asyncio
async def test_t1532_the_savepoint_statements_are_the_ones_sqlalchemy_emits(stand: Stand) -> None:
    """T1532. The parser is pinned to the SQL SQLAlchemy actually sends, on this dialect.

    The whole account is a prefix match on three statements. If a dialect ever spells one differently -
    quoted, or `RELEASE` without `SAVEPOINT` - the account silently stops recording and the refusals
    above become vacuous. So the spellings are measured, and the parser is run over exactly what was
    observed rather than over strings this test invented.

    MUTATION that must redden this: change any of the three prefixes in `_SAVEPOINT_PREFIXES`.
    """

    sql, watcher = _savepoint_sql(stand.engine)
    try:
        async with stand.factory() as session:
            async with session.begin_nested():
                await session.execute(select(Debt.id).limit(1))
            nested = await session.begin_nested()
            await session.execute(select(Debt.id).limit(1))
            await nested.rollback()
            await session.commit()
    finally:
        event.remove(stand.engine.sync_engine, "after_cursor_execute", watcher)

    parsed = [journal._savepoint_statement(statement) for statement in sql]
    kinds = [None if item is None else item[0] for item in parsed]

    assert sql, "stand: no savepoint statement was observed at all"
    assert None not in kinds, (
        f"the parser did not recognise a savepoint statement SQLAlchemy emitted: "
        f"{list(zip(sql, kinds))}"
    )
    assert set(kinds) == {"open", "close", "rollback"}, (
        f"all three savepoint statements must be reachable in one scenario, got {list(zip(sql, kinds))}"
    )
    for statement, item in zip(sql, parsed):
        assert item[1] in statement, (statement, item)
    assert journal._savepoint_statement("SELECT 1") is None
    assert journal._savepoint_statement("ROLLBACK") is None


@pytest.mark.asyncio
async def test_t1532_what_the_sql_account_still_does_not_see(stand: Stand) -> None:
    """T1532. The remaining edge, measured - the account is read in a listener and listeners are last.

    THE BRIEF NAMED THIS AND IT IS REAL: a neighbour can raise out of `after_cursor_execute` ahead of
    the journal's handler, and then the statement has already run while the journal does not learn of
    it. What the measurement adds is WHICH statement matters and what the cost to the attacker is.

    For `ROLLBACK TO SAVEPOINT` the loss is conservative: the journal keeps the operation registered,
    so a not-yet-completed one still refuses the commit. The dangerous one is the `SAVEPOINT` statement
    itself, because losing it empties the account this module's strongest refusal reads. This measures
    what it costs the attacker to lose it: the exception propagates out of `do_savepoint` to whatever
    asked for the connection - `AsyncSession.begin_nested()` does NOT send the statement itself, which
    is measured here too - so the unit of work stops rather than continuing over an unaccounted
    savepoint. That is not a closure and is not claimed as one; it is the price, and it is why the edge
    is narrow rather than gone.

    MUTATION that must redden this: none - this asserts the shape of an exposure. It reddens if
    SQLAlchemy ever swallows an `after_cursor_execute` exception, at which point the account can be
    silently emptied and `_lost_savepoint_closes` needs a different foundation.
    """

    class _Swallowed(BaseException):
        pass

    def _neighbour(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        if statement.strip().upper().startswith("SAVEPOINT"):
            raise _Swallowed(statement)

    escaped: BaseException | None = None
    observed: list[str] = []

    event.listen(Connection, "after_cursor_execute", _neighbour)
    try:
        # ONE BROAD `except`: `AsyncSession.begin_nested()` does not send the `SAVEPOINT` itself -
        # measured here - so the neighbour's exception surfaces at whatever next asks the session for
        # its connection, and from there the Session's nesting is half-made and every further attempt
        # raises the same thing. The two facts this test needs are captured before that.
        try:
            async with stand.factory() as session:
                async with stand.operation("t1532-edge", session=session):
                    session.add(stand.debt("8.00"))
                    await session.flush()
                    root = (await session.connection()).sync_connection.get_transaction()
                    await session.begin_nested()
                    state = journal._REGISTRY.get(root)
                    observed = list(state.savepoints_open) if state is not None else []
                    await session.flush()
                await session.commit()
        except BaseException as exc:  # noqa: BLE001 - the unwinding is not the measurement
            escaped = exc
    finally:
        event.remove(Connection, "after_cursor_execute", _neighbour)

    assert isinstance(escaped, _Swallowed), (
        f"a neighbour that raises on the `SAVEPOINT` statement no longer stops the unit of work "
        f"({escaped!r}). The account can then be emptied while the savepoint stays usable, and "
        f"`_lost_savepoint_closes` needs a foundation that is not a listener."
    )
    assert observed == [], (
        f"the journal recorded the savepoint even though its listener was pre-empted: {observed}"
    )
