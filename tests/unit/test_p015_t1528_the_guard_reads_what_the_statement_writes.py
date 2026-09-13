"""Programme 015, step 4, T1528: the write guard inferred meaning from a parameter dict.

WHAT THE FIX-DELTA REVIEW FOUND (2026-09-13). The T1527 fix made the guard match a row against the
edge and the amount the flush hook had verified - and it read both out of the statement's PARAMETER
DICT, deciding what a statement writes from the types of the values in that dict and from which keys
are absent from it. Three holes follow from that one decision, and all three are reproduced below:

1. A column written with a SQL EXPRESSION is not in the parameter dict at all. SQLAlchemy's ORM puts
   such a value in the statement's own `_values` (`persistence.py`, `statement.values(value_params)`)
   and compiles `SET amount=(debts.amount + :amount_1)`. The guard saw no `amount` parameter, read
   that as "this UPDATE does not touch the money", and matched the metadata-only expectation
   `_NO_MONEY_MOVED`. A debt went from 10 to 11 with an EMPTY entry list - the exact negation of
   what the journal is for.
2. The same absence let a key column move: `debt.creditor_id = literal(other.id)` is a SQL
   expression too, so `_row_edge` read the missing parameter as "this column is not being written",
   the full-key check of T1527 compared nothing, and the obligation moved to another creditor under
   an entry that still named the old edge.
3. The amount itself was found by SCANNING THE ROW FOR DECIMALS. One extra `Decimal` anywhere in the
   parameter dict therefore offered a second candidate, and a row that actually wrote 12 matched a
   grant for 11.

THE ROOT IS ONE AND THE FIX IS TWO LAYERS, in this order of authority:

* THE EXECUTION BOUNDARY (`_reconcile`). After the flush's SQL has run, the journal reads the rows
  it claims to have recorded BACK OUT OF THE DATABASE and requires each one to be exactly what the
  entry says: the same edge, the same amount, present or gone. Nothing about that check is an
  inference about a statement - it is the stored row. It holds whatever a statement's parameters
  looked like, whoever changed them, and in whatever listener order, which is why it is the layer
  that makes this class of defect unreachable rather than merely harder.
* THE PARAMETER-LEVEL GRANT, corrected. It still refuses before the write reaches the database,
  which is the only thing that can refuse a write no flush ever verified (Core DML, the `bulk_*`
  doors), and it no longer guesses: the amount comes from the `amount` bind BY NAME, a column the
  statement writes with an expression this module cannot read is UNREADABLE rather than absent, and
  unreadable matches nothing.

The two layers are told apart here by the REFUSAL'S NAME, which is also how each test's mutation
gets its teeth. A tamper that lands BEFORE the guard (a `Connection`-level listener, which
SQLAlchemy runs before every `Engine`-level one) is refused as `unverified_debt_write`; a tamper that
lands AFTER it (an `Engine`-instance listener, which runs after every class-level one) can only be
caught by reading the row back, and is refused as `unreconciled_debt_row`. Asserting the name
therefore asserts the ordering premise of the scenario as well.

AND THE `begin` GUARD'S THIRD STATE (review item 4). `bool(probe())` collapsed a driver that
ANSWERED NOTHING into "no transaction is open", so the carry-over the guard exists to stop was not
excluded when the question could not be asked. The four answers - True, False, None and a probe that
raises - are now classified separately and tested separately, and an unmeasured state is REFUSED.

TIER. SQLite, default tier, one database file per test under `tmp_path`, money inside `|v| < 2^26`
(design v2 §4). The PostgreSQL halves - UUID spelling through `literal()`, asyncpg's own transaction
probe, and the readback against a driver that returns `NUMERIC` as `Decimal` - are in
`tests/integration/test_p015_t1528_the_statement_is_read_not_guessed_postgres.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, literal, select
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


# =================================================================================================
# Helpers
# =================================================================================================


#: What the rest of a scenario raises once a refusal has been swallowed to reach the assertions: the
#: journal's refusal again (from the operation's completion, which flushes what is still pending) or
#: `PendingRollbackError`, when the refusal happened inside the flush's own SQL. Both are the refusal
#: continuing to hold; the FIRST one is always the verdict.
_SCENARIO_END = (journal.DebtJournalError, InvalidRequestError)


async def _refusal_of(awaitable) -> BaseException | None:
    """Run `awaitable`; return the journal's refusal if it raised one, else None."""

    try:
        await awaitable
    except journal.DebtJournalError as exc:
        return exc
    return None


async def _committed_debt(stand: Stand, amount: str, **edge: Any) -> tuple[uuid.UUID, int]:
    """One debt of this world, committed inside its own operation. Returns (id, version).

    `edge` overrides the default directed edge, because `debts` is UNIQUE on
    (debtor_id, creditor_id, equivalent_id): a scenario that needs two stored debts needs two edges.
    """

    async with stand.factory() as session:
        subject = stand.debt(amount, **edge)
        async with stand.operation("t1528-seed", session=session):
            session.add(subject)
            await session.flush()
        await session.commit()
        debt_id = subject.id
    async with stand.factory() as fresh:
        row = (await fresh.execute(select(Debt.id, Debt.version).where(Debt.id == debt_id))).one()
    return row[0], row[1]


async def _stored_row(stand: Stand, debt_id: uuid.UUID) -> dict[str, Any] | None:
    """One debt AS THE DATABASE HOLDS IT, read on a new session. None when it is not there."""

    async with stand.factory() as fresh:
        row = (
            await fresh.execute(
                select(Debt.amount, Debt.debtor_id, Debt.creditor_id, Debt.equivalent_id).where(
                    Debt.id == debt_id
                )
            )
        ).one_or_none()
    if row is None:
        return None
    return {
        "amount": Decimal(str(row[0])),
        "debtor_id": row[1],
        "creditor_id": row[2],
        "equivalent_id": row[3],
    }


def _tamper(target: Any, rewrite) -> Any:
    """A `before_execute` listener on `target` that rewrites a `debts` DML row, and nothing else.

    `retval=True`, which is the documented way for a listener to change what is executed
    (`ConnectionEvents.before_execute`). Registered on the target the scenario names, because WHERE
    it is registered is what decides whether it runs before or after the journal's own guard.
    """

    @event.listens_for(target, "before_execute", retval=True)
    def _rewrite(conn, clause, multiparams, params, execution_options):  # noqa: ANN001
        name = type(clause).__name__
        table = getattr(getattr(clause, "table", None), "name", None)
        if table == "debts" and name.endswith(("Insert", "Update")) and isinstance(params, dict):
            if params:
                changed = rewrite(dict(params))
                if changed is not None:
                    return clause, multiparams, changed
        return clause, multiparams, params

    return _rewrite


# =================================================================================================
# Review item 1 - a SQL expression is not an absent column
# =================================================================================================


@pytest.mark.asyncio
async def test_t1528_a_sql_expression_amount_cannot_ride_a_metadata_only_grant(
    stand: Stand,
) -> None:
    """T1528 P1, DEFECT-SHAPED. The amount moved and the operation's entry list stayed empty.

    THE REVIEWER'S SCENARIO. A Debt is dirty for its version and for nothing else, so the hook
    grants the metadata-only write (`_NO_MONEY_MOVED`) - correctly; that is T1527's positive
    control. A `before_flush` listener registered AFTER the journal's then sets
    `debt.amount = Debt.amount + 1`. The ORM puts that value in the statement's own `_values`
    instead of in the parameter dict and compiles `SET amount=(debts.amount + :amount_1)`, so the
    row carries NO `amount` parameter - and "no amount parameter" was exactly the signature of a
    metadata-only UPDATE. Measured at `c17fa26`: the commit was allowed, the debt was 11, and the
    operation's entries were empty.

    THE PREMISE IS ASSERTED, not assumed: the listener really fired, and the amount the journal
    verified really was the unchanged one.

    MUTATION that must redden this again: read the amount from the parameters alone in
    `_matches_any` (`row.get("amount", _ABSENT)` in place of `_written(row, declared, "amount")`).
    The refusal then comes from the readback instead, under the name `unreconciled_debt_row`, and the
    assertion on the reason fails - which is the point of naming it: this hole is closed BEFORE the
    write reaches the database, and the boundary behind it is a second line, not the only one.
    """

    ident = identity("t1528-expression-amount")
    debt_id, _ = await _committed_debt(stand, "10.00")
    fired: list[str] = []
    refusal: BaseException | None = None

    async with stand.factory() as session:

        @event.listens_for(session.sync_session, "before_flush")
        def _a_late_listener(sync_session, _flush_context, _instances) -> None:
            if fired:
                return
            for obj in list(sync_session.dirty):
                if isinstance(obj, Debt) and obj.id == debt_id:
                    obj.amount = Debt.amount + 1
                    fired.append("before_flush")

        try:
            async with stand.operation(
                "t1528-expression-amount", session=session, identity=ident
            ):
                subject = await session.get(Debt, debt_id)
                subject.version = subject.version + 1
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
        except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
            refusal = refusal if refusal is not None else exc

    stored = await _stored_row(stand, debt_id)
    entries = await stand.entries(ident)

    # NON-VACUITY: the late listener really put a SQL expression into the statement.
    assert fired == ["before_flush"], (
        f"stand: the late `before_flush` listener never changed the amount ({fired}), so no SQL "
        f"expression reached the UPDATE and this test measures nothing"
    )

    # VERDICT.
    assert refusal is not None, (
        f"a debt's amount was changed by a SQL expression inside a grant for a write that moves no "
        f"money, and nothing refused it: the row now holds {stored} and the operation recorded "
        f"{entries}. A debt that changed with no entry is the negation of this mechanism."
    )
    assert stored is not None and stored["amount"] == exact_money("10.00"), (
        f"the unrecorded amount is durable: {stored}"
    )
    assert entries == [], f"an operation that was refused still wrote entries: {entries}"

    # AND BY THE RIGHT MECHANISM: refused before the write, at the parameter boundary.
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal


@pytest.mark.asyncio
async def test_t1528_a_sql_expression_key_column_cannot_ride_a_money_grant(stand: Stand) -> None:
    """T1528 P1, DEFECT-SHAPED. The full key of T1527 was not checked on this UPDATE form.

    `debt.creditor_id = literal(other.id)` moves the obligation to another participant through a
    value the parameter dict never carries, so `_row_edge` read the column as one this statement
    does not write and the edge comparison compared nothing. Measured at `c17fa26`: the old edge
    vanished, the new edge got 11, and the entry recorded `U(old edge, 10 -> 11)`.

    WHY A LATE LISTENER AND NOT A PLAIN ASSIGNMENT: an assignment the hook can see is already
    refused as `key_field_changed` (`_effects_of_flush`). The hole is only reachable after the hook
    has read its effects, which is what the late `before_flush` listener reproduces.

    MUTATION that must redden this again: make `_row_edge` ignore `_statement_values` (read the
    edge from the parameter dict alone).
    """

    ident = identity("t1528-expression-edge")
    debt_id, _ = await _committed_debt(stand, "10.00")
    other_creditor = stand.extra_ids[0]
    fired: list[str] = []
    refusal: BaseException | None = None

    async with stand.factory() as session:

        @event.listens_for(session.sync_session, "before_flush")
        def _a_late_listener(sync_session, _flush_context, _instances) -> None:
            if fired:
                return
            for obj in list(sync_session.dirty):
                if isinstance(obj, Debt) and obj.id == debt_id:
                    obj.creditor_id = literal(other_creditor)
                    fired.append("before_flush")

        try:
            async with stand.operation("t1528-expression-edge", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = exact_money("11.00")
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
        except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
            refusal = refusal if refusal is not None else exc

    stored = await _stored_row(stand, debt_id)
    entries = await stand.entries(ident)

    # NON-VACUITY.
    assert fired == ["before_flush"], (
        f"stand: the late listener never reassigned `creditor_id` ({fired}); no edge moved and "
        f"there is nothing here to refuse"
    )

    # VERDICT.
    assert refusal is not None, (
        f"a stored debt was moved to another creditor through a SQL expression and nothing refused "
        f"it: the row now holds {stored} while the journal recorded {entries}. An edge is the "
        f"debt's identity."
    )
    assert stored is not None and stored["creditor_id"] == stand.creditor_id, (
        f"the debt is durable on the creditor the journal never recorded: {stored}"
    )
    assert stored["amount"] == exact_money("10.00"), f"the amount moved as well: {stored}"
    assert entries == [], f"an operation that was refused still wrote entries: {entries}"
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal


# =================================================================================================
# Review item 3 - the amount was whichever Decimal happened to match
# =================================================================================================


@pytest.mark.asyncio
async def test_t1528_an_unused_decimal_parameter_cannot_stand_in_for_the_amount(
    stand: Stand,
) -> None:
    """T1528 P1, DEFECT-SHAPED. The grant was consumed by a number the statement does not write.

    `_matches_any` collected every `Decimal` in the parameter dict as a candidate amount, so a row
    that writes 12 to `amount` while carrying an unused `audit_amount` of 11 offered the grant the
    11 it was waiting for. Measured at `c17fa26`: the commit passed, the stored amount was 12, and
    the entry said 10 -> 11.

    THE TAMPER IS REGISTERED ON THE CONNECTION, deliberately. SQLAlchemy runs every
    `Connection`-level listener before every `Engine`-level one (`sqlalchemy/event/attr.py:607-636`,
    measured in T1527), so this rewrite lands BEFORE the journal's guard and the guard has to catch
    it by reading the parameters correctly. The refusal's name asserts that ordering: an
    `unreconciled_debt_row` here would mean the tamper happened after the guard and this test was
    measuring the other layer.

    MUTATION that must redden this again: restore the type scan in `_matches_any` - offer
    `[v for v in row.values() if isinstance(v, Decimal)]` to `grant.take_row` as candidate amounts
    before reading the `amount` bind.
    """

    ident = identity("t1528-decimal-scan")
    debt_id, _ = await _committed_debt(stand, "10.00")
    verified = exact_money("11.00")
    actually_written = exact_money("12.00")
    rewritten: list[dict] = []
    refusal: BaseException | None = None

    async with stand.factory() as session:
        async_connection = await session.connection()
        sync_connection = async_connection.sync_connection

        def _rewrite(params: dict) -> dict | None:
            if params.get("amount") != verified:
                return None
            params["amount"] = actually_written
            params["audit_amount"] = verified
            rewritten.append(dict(params))
            return params

        listener = _tamper(sync_connection, _rewrite)
        try:
            async with stand.operation("t1528-decimal-scan", session=session, identity=ident):
                subject = await session.get(Debt, debt_id)
                subject.amount = verified
                refusal = await _refusal_of(session.flush())
            if refusal is None:
                refusal = await _refusal_of(session.commit())
        except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
            refusal = refusal if refusal is not None else exc
        finally:
            event.remove(sync_connection, "before_execute", listener)

    stored = await _stored_row(stand, debt_id)
    entries = await stand.entries(ident)

    # NON-VACUITY: the statement really carried two Decimals, one of them the grant's.
    assert rewritten, (
        "stand: the connection-level listener never rewrote the UPDATE's parameters, so no second "
        "Decimal was ever offered to the grant"
    )
    assert rewritten[0]["amount"] == actually_written, rewritten[0]
    assert rewritten[0]["audit_amount"] == verified, rewritten[0]

    # VERDICT.
    assert refusal is not None, (
        f"a row that writes {actually_written} consumed a grant for {verified} because an unused "
        f"parameter carried that number: `debts` holds {stored} and the journal recorded {entries}"
    )
    assert stored is not None and stored["amount"] == exact_money("10.00"), (
        f"the amount the journal never verified is durable: {stored}"
    )
    assert entries == [], f"an operation that was refused still wrote entries: {entries}"
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal


# =================================================================================================
# Review item 5, the write guard's mirror - and the boundary that answers it
# =================================================================================================


@pytest.mark.asyncio
async def test_t1528_a_parameter_changed_after_verification_is_caught_by_the_readback(
    stand: Stand,
) -> None:
    """T1528 P1, DEFECT-SHAPED. Verification happens before execution, so it cannot be the last word.

    THE REVIEWER'S ITEM 5, WRITE-GUARD HALF. An `Engine`-INSTANCE `before_execute` listener runs
    AFTER every class-level one (`_ListenerCollection` iterates `parent_listeners`, which hold the
    class-level functions, first), and the journal is armed on the `Engine` CLASS. So a neighbour
    can change an INSERT's amount from 11 to 12 after the guard has verified 11 and consumed its
    grant: the database stored 12 while the journal said 11, and no parameter-level check can ever
    see it, because the parameters it checked were the honest ones.

    THIS IS THE TEST FOR THE EXECUTION BOUNDARY. `_reconcile` reads the row back out of the database
    after the flush's SQL has run and compares it with what the entries claim. It needs no
    assumption about listener order, which is why it is the layer that closes the class.

    MUTATION that must redden this: remove the `_reconcile` call from `_after_flush`. Nothing else
    in this module can see this write - the guard verified the parameters it was given, and they
    were correct when it saw them.
    """

    ident = identity("t1528-after-verification")
    verified = exact_money("11.00")
    actually_written = exact_money("12.00")
    rewritten: list[dict] = []
    refusal: BaseException | None = None
    debt_id = uuid.uuid4()

    def _rewrite(params: dict) -> dict | None:
        if params.get("amount") != verified:
            return None
        params["amount"] = actually_written
        rewritten.append(dict(params))
        return params

    listener = _tamper(stand.engine.sync_engine, _rewrite)
    try:
        async with stand.factory() as session:
            try:
                async with stand.operation(
                    "t1528-after-verification", session=session, identity=ident
                ):
                    session.add(stand.debt("11.00", id=debt_id))
                    refusal = await _refusal_of(session.flush())
                if refusal is None:
                    refusal = await _refusal_of(session.commit())
            except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
                refusal = refusal if refusal is not None else exc
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", listener)

    stored = await _stored_row(stand, debt_id)
    entries = await stand.entries(ident)

    # NON-VACUITY: the tamper really happened, and it really happened AFTER the guard (otherwise
    # the guard would have refused it and the reason asserted below would be the other one).
    assert rewritten, (
        "stand: the engine-level listener never rewrote the INSERT's amount, so nothing was "
        "written behind the guard's back"
    )
    assert rewritten[0]["amount"] == actually_written, rewritten[0]

    # VERDICT.
    assert refusal is not None, (
        f"a neighbour changed the amount after the guard verified it and the row committed: "
        f"`debts` holds {stored} while the journal recorded {entries}"
    )
    assert stored is None, f"the tampered row is durable: {stored}"
    assert entries == [], f"an operation that was refused still wrote entries: {entries}"
    assert isinstance(refusal, journal.DebtJournalError), refusal
    assert refusal.reason == journal.Reason.UNRECONCILED_DEBT_ROW, refusal


# =================================================================================================
# Positive controls - a guard that refuses legitimate writes is the same defect
# =================================================================================================


@pytest.mark.asyncio
async def test_t1528_control_one_flush_with_an_insert_an_update_and_a_delete_commits(
    stand: Stand,
) -> None:
    """T1528 ANTI-VACUUM CONTROL. The readback must pass everything the journal actually recorded.

    The cheapest way for `_reconcile` to be wrong is to refuse the ordinary case - a flush with all
    three effects in it, read back on one statement. If this breaks, every writer in the system
    breaks, so it is asserted next to the refusals rather than trusted.

    GREEN BEFORE AND AFTER. The mutation that must redden it: compare the stored amount against
    `amount_before` instead of `amount_after` in `_reconcile`.
    """

    ident = identity("t1528-control-three-effects")
    changed_id, _ = await _committed_debt(stand, "10.00")
    deleted_id, _ = await _committed_debt(stand, "7.00", creditor_id=stand.extra_ids[0])
    inserted_id = uuid.uuid4()

    async with stand.factory() as session:
        async with stand.operation("t1528-control", session=session, identity=ident):
            changed = await session.get(Debt, changed_id)
            changed.amount = exact_money("13.00")
            deleted = await session.get(Debt, deleted_id)
            await session.delete(deleted)
            session.add(stand.debt("5.00", id=inserted_id, creditor_id=stand.extra_ids[1]))
            await session.flush()
        await session.commit()

    entries = await stand.entries(ident)
    assert await _stored_row(stand, changed_id) is not None
    assert (await _stored_row(stand, changed_id))["amount"] == exact_money("13.00")
    assert await _stored_row(stand, deleted_id) is None
    assert (await _stored_row(stand, inserted_id))["amount"] == exact_money("5.00")
    assert sorted(row["effect"] for row in entries) == ["D", "I", "U"], entries


@pytest.mark.asyncio
async def test_t1528_control_a_metadata_only_update_is_still_allowed(stand: Stand) -> None:
    """T1528 ANTI-VACUUM CONTROL for the two rules the defects above added.

    T1527's own inverse defect, re-asserted here because the readback is a new way to break it: a
    write that moves no money still has to be allowed, and the row it leaves behind still has to
    satisfy `_reconcile` - which means the journal has to claim the amount the row ALREADY HELD for
    it. That claim is what review item 1 is closed by, so this control and that counterexample are
    two directions of one rule.

    NOT A CARRIER FOR `_GRANTED_COLUMNS`: narrowing `_statement_values` to the columns the grant
    speaks about changes no decision (see its own comment), so no mutation of that set can redden
    anything, and claiming one here would be a guard nobody measured.

    GREEN BEFORE AND AFTER. The mutation that must redden it: record the metadata-only row's state
    with `amount=None` in `_effects_of_flush` - "the row must be gone" instead of "the row still
    holds what it held" - which is the shape of claim `_reconcile` would need if no money moving
    meant no row state at all.
    """

    ident = identity("t1528-control-metadata")
    debt_id, version_before = await _committed_debt(stand, "46.00")

    async with stand.factory() as session:
        async with stand.operation("t1528-control-metadata", session=session, identity=ident):
            subject = await session.get(Debt, debt_id)
            subject.version = subject.version + 1
            await session.flush()
        await session.commit()

    async with stand.factory() as fresh:
        version_after = (
            await fresh.execute(select(Debt.version).where(Debt.id == debt_id))
        ).scalar_one()
    stored = await _stored_row(stand, debt_id)
    entries = await stand.entries(ident)

    # NON-VACUITY: an UPDATE really reached the database.
    assert version_after > version_before, (
        f"stand: the version bump never reached `debts` ({version_before} -> {version_after}), so "
        f"no metadata-only UPDATE was granted and nothing here was measured"
    )
    assert stored is not None and stored["amount"] == exact_money("46.00"), stored
    assert entries == [], f"a write that moved no money produced an entry: {entries}"


# =================================================================================================
# Review item 4 - the `begin` guard's third state
# =================================================================================================


class _ProbeRaised(RuntimeError):
    """What a driver's transaction probe raises. Stands for any driver that cannot answer."""


def _fake_connection(*, driver: Any) -> Any:
    """The smallest thing `_on_begin` reads: an engine, a closed flag, and a driver connection.

    DEBUG PATH, DELIBERATELY AND MARKED AS SUCH (`AGENTS.md` §5). The four answers a driver can give
    cannot all be produced by a real driver - that is the point of the finding - so the policy is
    measured directly on the listener, and the CONTRACT that no supported driver ever gives the
    unmeasured answer is measured on a real engine in the test below and on the PostgreSQL tier.
    """

    return SimpleNamespace(
        engine=SimpleNamespace(),
        closed=False,
        invalidated=False,
        connection=SimpleNamespace(driver_connection=driver),
    )


def _method_driver(answer: Any) -> Any:
    def is_in_transaction() -> Any:
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return SimpleNamespace(is_in_transaction=is_in_transaction)


#: The two `Reason` values this parametrisation expects, spelled as the literals they are. Reading
#: them off `journal.Reason` at COLLECTION time would turn the red state of an unimplemented refusal
#: into a collection error, and a collection error is not a test that is red for its property.
_STALE = "stale_db_transaction"
_UNMEASURED = "unmeasured_db_transaction"


@pytest.mark.parametrize(
    ("label", "driver", "expected"),
    [
        ("method says True", _method_driver(True), _STALE),
        ("method says False", _method_driver(False), None),
        ("method says None", _method_driver(None), _UNMEASURED),
        ("method says 1", _method_driver(1), _UNMEASURED),
        (
            "method raises",
            _method_driver(_ProbeRaised("the driver would not say")),
            _UNMEASURED,
        ),
        ("attribute is True", SimpleNamespace(in_transaction=True), _STALE),
        ("attribute is False", SimpleNamespace(in_transaction=False), None),
        (
            "attribute is None",
            SimpleNamespace(in_transaction=None),
            _UNMEASURED,
        ),
        ("no probe at all", SimpleNamespace(), _UNMEASURED),
        ("no driver at all", None, _UNMEASURED),
    ],
)
def test_t1528_the_begin_guard_classifies_each_driver_answer_separately(
    label: str, driver: Any, expected: str | None
) -> None:
    """T1528, review item 4. `bool(probe())` made "would not say" mean "nothing is open".

    `_driver_transaction_is_live` returned `bool(probe())`, so a probe that answered `None` - or one
    that answered with anything that is not a boolean - was read as `False`, and `_on_begin` then
    allowed a new root to be born over a database transaction whose state it had never established.
    An unmeasured state is not a clean one (`AGENTS.md` §1), and the guard exists precisely for the
    case where SQLAlchemy's bookkeeping and the driver disagree.

    FOUR ANSWERS, SEPARATELY, both spellings: a method (`asyncpg.Connection.is_in_transaction()`)
    and an attribute (`sqlite3.Connection.in_transaction`), each with True, False and an unusable
    answer, plus a probe that raises and a driver with no probe at all.

    MUTATION that must redden this: restore `return bool(probe())` in the probe, or let `_on_begin`
    return early on an unmeasured state instead of refusing.
    """

    connection = _fake_connection(driver=driver)
    if expected is None:
        assert journal._on_begin(connection) is None, (
            f"{label}: a driver that positively reports no open transaction must not be refused"
        )
        return
    with pytest.raises(journal.DebtJournalError) as refusal:
        journal._on_begin(connection)
    assert refusal.value.reason == expected, f"{label}: {refusal.value}"


@pytest.mark.asyncio
async def test_t1528_the_sqlite_driver_answers_the_transaction_probe_with_a_bool(
    stand: Stand,
) -> None:
    """T1528, review item 4, THE CONTRACT STATED AS A MEASUREMENT rather than as a comment.

    The refusal above is only harmless because no driver this repository runs on ever produces the
    unmeasured answer. That is a claim about aiosqlite and asyncpg, so it is measured on each tier
    rather than asserted in prose: here, that the probe answers a real `bool` both outside and
    inside a transaction, and that it says True for a transaction that is open.

    Without this, "refuse when the driver will not say" could be refusing every `begin` in the
    system and the tests above would still pass.

    MUTATION that must redden this: return `None` from the probe whenever the driver answers through
    an ATTRIBUTE rather than through a method. Measured: the whole module turns red, because every
    SQLite `begin` in the process is then refused as `unmeasured_db_transaction` - including the
    stand's own. That is the cost this test exists to bound, and the reason the refusal is safe is
    this measurement and not an argument.
    """

    async with stand.engine.connect() as aconn:
        sync_connection = aconn.sync_connection
        idle = journal._driver_transaction_is_live(sync_connection)
        transaction = await aconn.begin()
        await aconn.execute(select(Debt.id).limit(1))
        live = journal._driver_transaction_is_live(sync_connection)
        await transaction.rollback()

    assert idle is False, (
        f"aiosqlite did not answer the transaction probe with False on an idle connection "
        f"({idle!r}); the `begin` guard would refuse every transaction in this process"
    )
    assert live is True, (
        f"aiosqlite did not report an open transaction as live ({live!r}), so the guard's only "
        f"positive finding is unreachable on this tier"
    )


# =================================================================================================
# Review item 5 - the listener the neighbour can skip
# =================================================================================================


class _PreventedTheRollback(BaseException):
    """What a neighbouring `rollback_savepoint` listener raises. Stands for any failure there."""


@pytest.mark.asyncio
async def test_t1528_a_connection_level_neighbour_cannot_hide_a_prevented_savepoint_rollback(
    stand: Stand,
) -> None:
    """T1528, review item 5, the savepoint half. Binding condition 1 was weaker than its docstring.

    THE REVIEWER'S SCENARIO. A completed operation inside a savepoint, and a neighbour registered on
    the CONNECTION that raises in `rollback_savepoint`. SQLAlchemy runs every connection-level
    listener before every engine-level one (`_JoinedListener`, `sqlalchemy/event/attr.py:607-636`),
    and the journal was armed on the `Engine` CLASS, so the neighbour's exception pre-empted
    `_on_rollback_savepoint` entirely: `pending_savepoint_rollbacks` stayed EMPTY, the savepoint's
    rows were never rolled back in the database, and the root commit made a debt of 42 durable under
    a record that says the savepoint was undone. `insert=True` cannot fix that - it only orders the
    journal among ENGINE-level listeners.

    WHAT CLOSES IT, and it is not a re-ordering. The journal now registers the recorder ON EVERY
    CONNECTION AS THE CONNECTION IS CREATED (`engine_connect`, `insert=True`), which is the earliest
    point at which a `Connection` exists. It is therefore FIRST among that connection's own
    listeners, before any a caller adds afterwards - measured, and this test is that measurement.

    WHAT REMAINS OPEN, stated here because it is what a risk acceptance has to say: the recorder is
    first among a connection's own listeners only because it is registered as the connection is built.
    Anyone whose own `engine_connect` handler runs BEFORE the journal's can register ahead of it on
    that connection and pre-empt it again, and which class-level handler runs first is SQLAlchemy's
    order rather than the journal's to enforce. Measured on this tree: the journal's ran first even
    against a later `insert=True` class-level registration, because it is registered when
    `app.db.models` is imported - an observation about this tree, not a property of the mechanism.

    MUTATION that must redden this: remove `("engine_connect", _on_engine_connect)` from
    `_CONNECTION_LISTENERS` - the journal is then only on the `Engine` class, which is exactly the
    state the reviewer measured.
    """

    outer = identity("t1528-outer")
    inner = identity("t1528-inner")
    prevented: BaseException | None = None
    commit_refusal: BaseException | None = None
    inside = None
    recorded: list[str] = []

    async with stand.factory() as session:
        async with stand.operation("t1528-outer", session=session, identity=outer):
            session.add(stand.debt("9.00"))
            await session.flush()

        sync_connection = (await session.connection()).sync_connection
        nested = await session.begin_nested()
        async with stand.operation("t1528-inner", session=session, identity=inner):
            session.add(stand.debt("42.00", creditor_id=stand.extra_ids[0]))
            await session.flush()
        inside = await session.scalar(
            select(Debt.amount).where(Debt.creditor_id == stand.extra_ids[0])
        )

        def _a_neighbour_that_raises(conn, name, context) -> None:  # noqa: ANN001
            raise _PreventedTheRollback(f"a neighbour prevented the rollback of {name}")

        event.listen(sync_connection, "rollback_savepoint", _a_neighbour_that_raises)
        try:
            await nested.rollback()
        except BaseException as exc:  # noqa: BLE001 - the prevented rollback is the premise
            prevented = exc
        finally:
            event.remove(sync_connection, "rollback_savepoint", _a_neighbour_that_raises)

        state = journal._REGISTRY.get(sync_connection.get_transaction())
        recorded = sorted(state.pending_savepoint_rollbacks) if state is not None else []

        try:
            commit_refusal = await _refusal_of(session.commit())
        except _SCENARIO_END as exc:  # noqa: B902 - the refusal is the subject
            commit_refusal = exc

    durable = await stand.stored_debts()

    # NON-VACUITY, THREE HALVES: the savepoint really wrote money, the neighbour really prevented
    # its rollback, and the write was really still there to be committed.
    assert inside == exact_money("42.00"), f"stand: the savepoint wrote nothing ({inside})"
    assert isinstance(prevented, _PreventedTheRollback), (
        f"stand: the neighbour did not prevent the savepoint rollback ({prevented!r}); there was "
        f"nothing for the commit to inherit"
    )

    # VERDICT, the journal's own half: it LEARNED that a rollback was asked for.
    assert recorded, (
        "a connection-level neighbour pre-empted the journal's `rollback_savepoint` listener: the "
        "journal never learned that a rollback was requested, so it cannot refuse a commit of "
        "whatever the savepoint still holds"
    )

    # AND THE COMMIT DID NOT CARRY THE SAVEPOINT'S MONEY.
    assert commit_refusal is not None, (
        f"the root committed after a prevented savepoint rollback: `debts` holds {durable}"
    )
    assert durable == {}, f"the savepoint's debt is durable: {durable}"


# =================================================================================================
# The private SQLAlchemy attribute this fix reads
# =================================================================================================


def test_t1528_the_statement_values_attribute_this_fix_reads_still_exists() -> None:
    """`ValuesBase._values` is private and pinned to SQLAlchemy 2.0.25. A version bump lands here.

    WHY A PIN AND WHY HERE. `tests/unit/test_p015_b4a_journal_mechanism.py` owns the same pin for the
    four private attributes the registry reads, and the rule it states is the reason this exists:
    using a private attribute knowingly means owning a test that fails loudly when it moves, rather
    than a guard that silently stops seeing something. T1528 added a fifth - the statement's own
    values - and it is pinned in the module that added it rather than bolted onto the other's list.

    THE TWO SHAPES ARE THE ONES SQLALCHEMY'S ORM PRODUCES, keyed by `Column` exactly as
    `_emit_update_statements` builds them from `value_params`: a SQL expression, which is OPAQUE, and
    a literal bind, which is readable and therefore comparable with the recorded edge.

    MUTATION that must redden this: none is needed - a SQLAlchemy upgrade is the mutation, which is
    the point. What makes it more than a tautology is the second half: `_statement_values` has to
    return the right one of `_OPAQUE` and a value for each shape.
    """

    import sqlalchemy

    table = Debt.__table__
    other = uuid.uuid4()

    assert sqlalchemy.__version__ == "2.0.25", (
        f"SQLAlchemy is {sqlalchemy.__version__}; re-verify `_values` and the parameter shapes in "
        f"app/core/ledger/journal.py before changing this pin"
    )

    expression = table.update().values({table.c.amount: table.c.amount + 1})
    assert journal._statement_values(expression) == {"amount": journal._OPAQUE}, (
        "a column written with a SQL expression is no longer readable out of the statement, so the "
        "guard is back to reading that column as one the statement does not write"
    )

    literal_bind = table.update().values({table.c.creditor_id: literal(other)})
    assert journal._statement_values(literal_bind) == {"creditor_id": other}, (
        "a literal bind in the statement's own values is no longer readable, so an edge written "
        "that way can only be refused blindly rather than compared"
    )

    assert journal._statement_values(table.update()) == {}, (
        "an UPDATE with no values of its own now declares something, which would make every "
        "ordinary ORM update carry a declared column"
    )


@pytest.mark.asyncio
async def test_t1528_a_connection_that_accepts_no_listener_is_skipped_and_not_exposed_by_it(
    stand: Stand,
) -> None:
    """T1528. Per-connection registration must not break the connections that refuse it.

    MEASURED WHEN IT BROKE THEM (2026-09-13, on the PostgreSQL gate). A connection born from an
    `execution_options()` engine - an `OptionEngine`, which
    `tests/integration/test_clearing_commit_replay_postgres.py` builds to get SERIALIZABLE - carries
    a doubly-joined dispatch that SQLAlchemy's `Events._accept_with` does not recognise, so
    `event.listen(conn, <any ConnectionEvents event>, ...)` raises `InvalidRequestError`. The first
    version of `_on_engine_connect` called it unconditionally and every transaction on such an engine
    died in `Connection.__init__` - two SERIALIZABLE clearing tests, a guard breaking legitimate work
    exactly as §9 warns.

    AND THE SKIP IS NOT A HOLE, which is the half worth measuring rather than asserting: the refusal
    is SQLAlchemy's, about the target, and it is the same for every caller. A neighbour cannot
    register a connection-level listener there either, so on such a connection the class-level
    listener is the whole field and there is nothing to get ahead of. This test measures that
    symmetry; it does not measure the recorder's behaviour on such a connection, which is the
    class-level registration's job and is covered by the savepoint test above.

    MUTATION that must redden this: remove the `InvalidRequestError` branch from
    `_on_engine_connect`.
    """

    # The async engine's own `execution_options`, because that is how the application and the
    # failing tests get one; it wraps exactly the `OptionEngine` this test is about and shares the
    # stand's pool, so it is not disposed here.
    option_engine = stand.engine.execution_options(isolation_level="SERIALIZABLE")

    def _a_neighbour(conn, name, context) -> None:  # noqa: ANN001 - never called
        raise AssertionError("this listener can never be registered")

    # THE JOURNAL'S OWN REGISTRATION MUST NOT BREAK THE CONNECTION.
    async with option_engine.connect() as connection:
        rows = (await connection.execute(select(Debt.id).limit(1))).all()

        # AND NOBODY ELSE CAN REGISTER THERE EITHER - the reason the skip costs nothing.
        with pytest.raises(InvalidRequestError):
            event.listen(connection.sync_connection, "rollback_savepoint", _a_neighbour)

    assert isinstance(rows, list), "the statement ran; its result is not the subject"
    assert journal.journal_is_installed(option_engine), (
        "the journal is not armed on an `execution_options()` engine at all, so the class-level "
        "listener this test relies on as the remaining field is not there either"
    )
