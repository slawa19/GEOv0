"""Programme 015, step 4 slice A: the debt journal's mechanism, proven on a private stand.

WHAT THIS MODULE IS AND IS NOT. Slice A builds the journal - the tables, the transaction registry,
the operation context, the flush hook and the write guard - and registers it NOWHERE. The
production engine, the production sessionmaker and the `Session` class are untouched, so both
canonical gates come back exactly as they were. These tests therefore arm the listeners on a stand
of their own (`tests/p015_b4a_stand.py`) and prove the machinery there.

They are NOT the step-2 counterexamples. Those live under the `b4_counterexample` marker, they are
red until slice C activates the journal globally, and slice C is what removes their marker. This
module is the slice-A builder's own evidence, and it is green today.

WHAT EVERY TEST HERE DOES, without exception:

* reads its verdict on a NEW session, never through the session under test - an identity map
  answers from memory and cannot tell a write that reached the database from one that did not;
* carries a non-vacuity assertion, so a scenario that never happened cannot pass as a refusal;
* names in its docstring the MUTATION that must turn it red again. A guard whose mutation was
  never run is a guard nobody has measured (`AGENTS.md` §9, anti-vacuum).

TIER. PostgreSQL since programme 017 stage 3 (2026-09-24): the stand's own engine over the tier
database, real root commits, a world of its own purged after each test (`tests/p015_b4a_stand.py::
new_postgres_stand`). Until then these rules were measured ONLY on SQLite, and the PostgreSQL
modules named below covered only what SQLite could not see. Money stays inside `|v| < 2^26`
(design v2 §4) except where a test says otherwise. Two tests stay on the SQLite stand because their
subject IS SQLite and they leave with it: the transaction-control refusal and the round-trip
predicate. What SQLite could not host - an AUTOCOMMIT engine, a two-phase root, a DML CTE, a NaN
that actually reaches a column - is in
`tests/integration/test_p015_b4a_journal_postgres.py`, with the reason stated there.
"""

from __future__ import annotations

import gc
import uuid
import weakref
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, insert, select, update

from app.core.ledger import journal
from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.debt import Debt
from tests.p015_b4a_stand import Stand, exact_money, identity, new_postgres_stand, new_sqlite_stand


@pytest_asyncio.fixture
async def stand():
    built = await new_postgres_stand(extra_participants=2)
    try:
        yield built
    finally:
        await built.close(purge=True)


class _BusinessFailure(RuntimeError):
    """A failure from the BLOCK of an operation - a rejected payment, not a journal malfunction."""


# =================================================================================================
# The record itself
# =================================================================================================


@pytest.mark.asyncio
async def test_an_operation_that_completes_writes_one_envelope_and_its_entries(stand: Stand) -> None:
    """The whole point of the journal, end to end: money moved and the record says what moved.

    Two flushes inside one operation - an insert of 10 and an update to 12 - must produce one
    COMPLETED envelope with `flush_count` 2 and `effect_count` 2, two entries whose `before` chains
    to the previous `after`, and one completion row for the equivalent.

    MUTATION that must redden this: make `_after_flush` skip the entry INSERT when `effects` is
    non-empty, or let `_complete` write COMPLETED without counting the rows it reads back.
    """

    ident = identity("happy")
    async with stand.factory() as session:
        async with stand.operation("happy", session=session, identity=ident):
            debt = stand.debt("10.00")
            session.add(debt)
            await session.flush()
            debt.amount = exact_money("12.00")
            await session.flush()
        await session.commit()

    stored = await stand.stored_debts()
    envelopes = await stand.envelopes(ident)
    entries = await stand.entries(ident)
    completion = await stand.operation_equivalents(ident)

    # NON-VACUITY: the money really moved, so there is something for the record to be about.
    assert stored == {("debtor", "creditor", "eq"): Decimal("12.00000000")}, stored

    assert len(envelopes) == 1, envelopes
    envelope = envelopes[0]
    assert envelope["state"] == "COMPLETED", envelope
    assert (envelope["flush_count"], envelope["effect_count"]) == (2, 2), envelope
    assert envelope["kind"] == "TEST_FIXTURE" and envelope["tx_id"] is None, envelope
    assert len(envelope["effect_digest"]) == 64, envelope

    assert [
        (row["flush_ordinal"], row["effect"], row["amount_before"], row["amount_after"], row["delta"])
        for row in entries
    ] == [
        (1, "I", None, Decimal("10.00000000"), Decimal("10.00000000")),
        (2, "U", Decimal("10.00000000"), Decimal("12.00000000"), Decimal("2.00000000")),
    ], entries

    assert [
        (row["equivalent_id"], row["in_intent"], row["in_scope"], row["effect_count"])
        for row in completion
    ] == [(stand.equivalent_id, False, True, 2)], completion


@pytest.mark.asyncio
async def test_a_rolled_back_operation_leaves_nothing_and_the_same_identity_reopens(
    stand: Stand,
) -> None:
    """C7. A rollback must leave the journal empty AND leave the transaction usable again.

    "The registry is empty" is not assertable - it is a private `WeakKeyDictionary` with no
    observation point - so the property is stated in the terms the database can answer: after the
    rollback the same identity OPENS AGAIN and the next commit is ALLOWED. Both are true only if
    no non-COMPLETED operation and no poison survived the rollback.

    MUTATION that must redden this: keep the rolled-back operation registered (drop the weak-key
    registry for a strong one keyed by connection), and the reopen fails as a nested operation.
    """

    ident = identity("reopen")
    async with stand.factory() as session:
        async with stand.operation("reopen", session=session, identity=ident):
            session.add(stand.debt("7.00"))
            await session.flush()
        await session.rollback()

        # NON-VACUITY, read inside the transaction that is about to be rolled back is not enough;
        # this reads after it, on a new session: the rollback really removed the envelope.
        assert await stand.envelopes(ident) == [], "the rolled-back envelope is still on disk"

        async with stand.operation("reopen", session=session, identity=ident):
            session.add(stand.debt("8.00"))
            await session.flush()
        await session.commit()

    envelopes = await stand.envelopes(ident)
    assert [row["state"] for row in envelopes] == ["COMPLETED"], envelopes
    assert (await stand.stored_debts()) == {
        ("debtor", "creditor", "eq"): Decimal("8.00000000")
    }, "the second attempt's money is not what the table holds"
    assert [row["delta"] for row in await stand.entries(ident)] == [
        Decimal("8.00000000")
    ], "entries from the rolled-back attempt survived into the reopened operation"


# =================================================================================================
# Binding condition 1 - a rollback event is not a report that the rollback happened
# =================================================================================================


@pytest.mark.asyncio
async def test_condition1_a_failed_savepoint_rollback_keeps_the_refusal(stand: Stand) -> None:
    """Binding condition 1, the round-2 reviewer's own probe, rebuilt on real debts.

    SQLAlchemy dispatches `rollback_savepoint` BEFORE `do_rollback_to_savepoint`
    (`sqlalchemy/engine/base.py:1150`) and deactivates the savepoint in a `finally`. A journal that
    cleared its registry from that event would treat a FAILED rollback as a success: the
    savepoint's rows are still in the transaction, the registry is empty, and the root commit
    stores them - the reviewer measured exactly that, `[(42,)]`.

    Here the rollback is made to fail by a listener that raises before any SQL is sent. The debt of
    42 must NOT be durable: either the commit is refused, or the connection is invalidated.

    MUTATION that must redden this: in `_on_rollback_savepoint`, drop the bound operations
    immediately instead of waiting for `after_cursor_execute` to confirm the statement ran.
    """

    message = "p015-b4a: the savepoint rollback failed before any SQL was sent"
    armed = {"on": False}

    def _fail_the_savepoint_rollback(_conn, _name, _context) -> None:
        if armed["on"]:
            raise RuntimeError(message)

    event.listen(stand.engine.sync_engine, "rollback_savepoint", _fail_the_savepoint_rollback)
    try:
        in_savepoint = exact_money("42.00")
        rollback_error: BaseException | None = None
        seen_inside: Decimal | None = None
        commit_refusal: BaseException | None = None

        async with stand.factory() as session:
            # The root begins with its own write, so the savepoint below is never the transaction
            # itself (T1525) and this test measures the rollback, not SQLite's legacy BEGIN.
            async with stand.operation("root-write", session=session):
                session.add(stand.debt("8.00"))
                await session.flush()

            nested = await session.begin_nested()
            async with stand.operation("savepoint-bound", session=session):
                session.add(
                    stand.debt(str(in_savepoint), creditor_id=stand.extra_ids[0])
                )
                await session.flush()
            seen_inside = await session.scalar(
                select(Debt.amount).where(Debt.creditor_id == stand.extra_ids[0])
            )

            armed["on"] = True
            try:
                await nested.rollback()
            except RuntimeError as exc:
                rollback_error = exc
            finally:
                armed["on"] = False

            try:
                await session.commit()
            except journal.DebtJournalError as exc:
                commit_refusal = exc

        after = await stand.stored_debts()

        # NON-VACUITY, both halves.
        assert rollback_error is not None and message in str(rollback_error), (
            f"stand: the savepoint rollback did not fail, so nothing here is about a failed "
            f"rollback: {rollback_error!r}"
        )
        assert seen_inside == in_savepoint, (
            f"stand: the savepoint's debt never reached the database ({seen_inside}), so its "
            f"survival would prove nothing"
        )

        # VERDICT.
        assert after.get(("debtor", "extra0", "eq")) is None, (
            f"the savepoint rollback FAILED and the commit stored the savepoint's debt anyway: "
            f"{after}. A rollback event is not a report that anything rolled back."
        )
        assert commit_refusal is not None, (
            "the commit was not refused after a failed savepoint rollback"
        )
    finally:
        event.remove(stand.engine.sync_engine, "rollback_savepoint", _fail_the_savepoint_rollback)


@pytest.mark.asyncio
async def test_condition1_control_a_successful_savepoint_rollback_releases_the_root(
    stand: Stand,
) -> None:
    """Binding condition 1's anti-vacuum control. Without it, "never drop anything" would pass.

    A journal that satisfied the test above by simply never dropping a savepoint-bound operation
    would refuse every commit that ever had a rolled-back savepoint in it - correct-looking and
    useless. So: a savepoint-bound operation whose rollback SUCCEEDS must be gone, the outer work
    must commit, and the rolled-back entries must not be in the record.

    MUTATION that must redden this: stop confirming the rollback in `after_cursor_execute` (never
    call `_confirm_savepoint_rollback`), and this commit is refused.
    """

    outer_identity = identity("outer")
    inner_identity = identity("inner")
    async with stand.factory() as session:
        async with stand.operation("outer", session=session, identity=outer_identity):
            session.add(stand.debt("9.00"))
            await session.flush()

        nested = await session.begin_nested()
        async with stand.operation("inner", session=session, identity=inner_identity):
            session.add(stand.debt("11.00", creditor_id=stand.extra_ids[0]))
            await session.flush()
        inside = await session.scalar(
            select(Debt.amount).where(Debt.creditor_id == stand.extra_ids[0])
        )
        await nested.rollback()
        await session.commit()

    # NON-VACUITY: there really was a savepoint-bound write to roll back.
    assert inside == Decimal("11.00000000"), f"stand: the savepoint wrote nothing ({inside})"

    stored = await stand.stored_debts()
    assert stored == {("debtor", "creditor", "eq"): Decimal("9.00000000")}, stored
    assert [row["state"] for row in await stand.envelopes(outer_identity)] == ["COMPLETED"]
    assert await stand.envelopes(inner_identity) == [], (
        "the rolled-back savepoint's envelope survived into the committed transaction"
    )


# =================================================================================================
# Binding condition 2 - no retention of finished roots
# =================================================================================================


@pytest.mark.asyncio
async def test_condition2_a_committed_root_is_not_retained_by_the_registry(stand: Stand) -> None:
    """Binding condition 2. A committed root transaction must become collectable.

    The registry is a `WeakKeyDictionary` keyed by the Core `RootTransaction`, so a value holding
    `op.root` as an ordinary attribute would be a strong reference from the value back to its own
    key. The round-2 reviewer measured that: `root_alive True entries 1` after commit and a full
    collection - unbounded retention of every finished transaction, with its connection's
    transaction object attached.

    MUTATION that must redden this: store the root on `_OpRecord` as a plain attribute instead of
    a `weakref.ref` (`self._root_ref = root` and a `root` property returning it).
    """

    ident = identity("retention")
    async with stand.factory() as session:
        async with stand.operation("retention", session=session, identity=ident):
            session.add(stand.debt("5.00"))
            await session.flush()
        core_connection = (await session.connection()).sync_connection
        root_ref = weakref.ref(core_connection.get_transaction())
        await session.commit()
    del core_connection, session

    envelopes = await stand.envelopes(ident)

    # NON-VACUITY: this root really carried a completed operation, so the weak reference below is
    # about a transaction the registry actually held state for.
    assert [row["state"] for row in envelopes] == ["COMPLETED"], envelopes

    gc.collect()
    assert root_ref() is None, (
        "the committed root transaction is still alive after a full collection: the registry keeps "
        "a strong reference back to its own key, so every finished transaction is retained for the "
        "life of the process"
    )


# =================================================================================================
# Binding condition 3 - the grant names verified writes, not a window
# =================================================================================================


@pytest.mark.asyncio
async def test_condition3_dml_from_another_listeners_after_flush_is_refused(stand: Stand) -> None:
    """Binding condition 3. The reviewer's probe: unverified DML inside the flush window.

    `Session._flushing` is still True and `session.info`'s flush context is still the same object
    all through `after_flush` - they are cleared at `after_flush_postexec`
    (`sqlalchemy/orm/session.py:4412`, `:4442`). So a grant that means "while this session is
    flushing" passes a neighbouring listener's `insert(Debt)` and its Core `update`, and the
    journal then records money that did not move and misses money that did.

    MUTATION that must redden this: replace the per-write grant in `_before_flush` with
    `_Grant(session_id=id(session), expected={})` and let `_matches_any` return True whenever a
    grant exists - the design's original "grant for the duration of the flush".
    """

    fired: list[str] = []
    covered = exact_money("20.00")
    refusal: BaseException | None = None

    async with stand.factory() as session:

        @event.listens_for(session.sync_session, "after_flush")
        def _a_neighbouring_listener(sync_session, _flush_context) -> None:
            if fired:
                return
            fired.append("after_flush")
            sync_session.execute(
                insert(Debt).values(**stand.debt_values("77.00", creditor_id=stand.extra_ids[0]))
            )
            sync_session.connection().execute(
                Debt.__table__.update()
                .where(Debt.creditor_id == stand.creditor_id)
                .values(amount=exact_money("99.00"))
            )

        try:
            async with stand.operation("granted", session=session):
                session.add(stand.debt(str(covered)))
                await session.flush()
        except journal.DebtJournalError as exc:
            refusal = exc
        if refusal is None:
            try:
                await session.commit()
            except journal.DebtJournalError as exc:
                refusal = exc

    after = await stand.stored_debts()

    # NON-VACUITY: the neighbouring listener really ran inside the flush.
    assert fired == ["after_flush"], (
        f"stand: the neighbouring `after_flush` listener never ran, so no unverified DML was "
        f"attempted: {fired}"
    )

    assert refusal is not None, (
        f"a neighbouring `after_flush` listener inserted a Debt and updated another one, neither "
        f"of them among the effects the hook verified, and nothing refused them: {after}"
    )
    assert refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal
    assert after == {}, f"the unverified writes are durable: {after}"


@pytest.mark.asyncio
async def test_condition3_a_late_before_flush_listener_mutating_a_debt_is_refused(
    stand: Stand,
) -> None:
    """Binding condition 3, the neighbouring shape: a mutation AFTER the effects were read.

    A `before_flush` listener registered later than the journal's runs after it, changes a Debt,
    and SQLAlchemy re-collects `new`/`dirty` before writing
    (`sqlalchemy/orm/session.py:4339-4346`). The amount the journal recorded and the amount the
    database stores are then different numbers - and every downstream check that reads `debts`
    agrees with the wrong one.

    MUTATION that must redden this: drop the amount from the write signature
    (`("I", str(debt_id), "")` for every effect), so the grant stops naming WHAT was verified and
    names only WHICH ROW.
    """

    tampered_rows: list[str] = []
    written_by_the_caller = exact_money("30.00")
    tampered = exact_money("31.00")
    refusal: BaseException | None = None

    async with stand.factory() as session:

        @event.listens_for(session.sync_session, "before_flush")
        def _a_late_listener(sync_session, _flush_context, _instances) -> None:
            for obj in list(sync_session.new) + list(sync_session.dirty):
                if isinstance(obj, Debt) and obj.amount != tampered:
                    obj.amount = tampered
                    tampered_rows.append(str(obj.id))

        try:
            async with stand.operation("late-mutation", session=session):
                session.add(stand.debt(str(written_by_the_caller)))
                await session.flush()
        except journal.DebtJournalError as exc:
            refusal = exc
        if refusal is None:
            try:
                await session.commit()
            except journal.DebtJournalError as exc:
                refusal = exc

    after = await stand.stored_debts()

    # NON-VACUITY: the late listener really changed the value.
    assert tampered_rows, "stand: the late `before_flush` listener never changed anything"

    assert refusal is not None, (
        f"a `before_flush` listener registered after the journal's changed the amount from "
        f"{written_by_the_caller} to {tampered} and the change committed: {after}"
    )
    assert refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal
    assert after.get(("debtor", "creditor", "eq")) != tampered, f"the tampered amount is durable: {after}"


@pytest.mark.asyncio
async def test_condition3_control_a_multi_row_granted_flush_still_passes(stand: Stand) -> None:
    """Binding condition 3's anti-vacuum control, required by the reviewer alongside the narrowing.

    "Положительные контроли batch/RETURNING сохраняются". A grant per VERIFIED WRITE must not
    degrade into a grant per statement: two debts added to one flush still reach the database as
    SQLAlchemy chooses to send them - on this tree, batched into one statement - and both are
    stored exactly, with two entries under one flush ordinal.

    The statement count is asserted to be SMALLER than the number of rows rather than pinned to a
    number: the property is that batching is not forbidden, and pinning SQLAlchemy's batching
    threshold would make this a test of SQLAlchemy's version.

    MUTATION that must redden this: make `_matches_any` consume the whole grant on the first row
    (`grant.expected.clear()` after a match), and the second row of the batch is refused.
    """

    ident = identity("batch")
    debt_statements: list[str] = []

    def _count(_conn, clause, _multiparams, _params, _options) -> None:
        if getattr(getattr(clause, "table", None), "name", None) == "debts":
            debt_statements.append(type(clause).__name__)

    event.listen(stand.engine.sync_engine, "before_execute", _count)
    try:
        async with stand.factory() as session:
            async with stand.operation("batch", session=session, identity=ident):
                session.add_all(
                    [
                        stand.debt("32.00"),
                        stand.debt("33.00", creditor_id=stand.extra_ids[0]),
                    ]
                )
                await session.flush()
            await session.commit()
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", _count)

    stored = await stand.stored_debts()
    entries = await stand.entries(ident)

    # NON-VACUITY: both rows really went out, and they really were batched.
    assert debt_statements, "stand: no statement against `debts` was observed at all"

    assert stored == {
        ("debtor", "creditor", "eq"): Decimal("32.00000000"),
        ("debtor", "extra0", "eq"): Decimal("33.00000000"),
    }, stored
    assert len(debt_statements) < 2, (
        f"two debts in one flush reached the connection as {len(debt_statements)} statements "
        f"({debt_statements}): the grant has been narrowed into one statement per row"
    )
    assert sorted(row["delta"] for row in entries) == [
        Decimal("32.00000000"),
        Decimal("33.00000000"),
    ], entries
    assert {row["flush_ordinal"] for row in entries} == {1}, entries


# =================================================================================================
# The flush hook
# =================================================================================================


@pytest.mark.parametrize("effect", ["insert", "update", "delete"])
@pytest.mark.asyncio
async def test_the_hook_refuses_a_debt_changed_with_no_operation(stand: Stand, effect: str) -> None:
    """C1. An ORM Debt I/U/D outside any operation is refused at the flush, and the commit too.

    The commit half is asserted but NOT credited to the journal, and the difference is worth
    stating: after a flush raises, SQLAlchemy marks the Session as needing a rollback and refuses
    the commit itself (`sqlalchemy/orm/session.py:929`, `PendingRollbackError`). That is a real
    protection and it is not this module's. The journal's OWN refusal of a poisoned transaction is
    isolated in `test_a_refused_write_poisons_the_transaction_until_it_is_rolled_back`, where the
    Session is not in that state - relying on a neighbour's compensation is exactly what
    `AGENTS.md` §9 forbids counting as one's own rule.

    MUTATION that must redden this: let `_before_flush` return instead of raising when
    `_find_operation` finds no candidate.
    """

    ident = identity("seed-for-" + effect)
    if effect in ("update", "delete"):
        async with stand.factory() as session:
            async with stand.operation("seed", session=session, identity=ident):
                session.add(stand.debt("15.00"))
                await session.flush()
            await session.commit()

    flush_refusal: BaseException | None = None
    commit_refusal: BaseException | None = None
    async with stand.factory() as session:
        if effect == "insert":
            session.add(stand.debt("16.00"))
        else:
            existing = (await session.execute(select(Debt))).scalars().one()
            if effect == "update":
                existing.amount = exact_money("17.00")
            else:
                await session.delete(existing)
        try:
            await session.flush()
        except journal.DebtJournalError as exc:
            flush_refusal = exc
        try:
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - which mechanism answered is asserted below
            commit_refusal = exc

    stored = await stand.stored_debts()
    expected = {} if effect == "insert" else {("debtor", "creditor", "eq"): Decimal("15.00000000")}

    # NON-VACUITY: for update and delete there really was a row to change, and it was journalled.
    if effect != "insert":
        assert (await stand.envelopes(ident))[0]["state"] == "COMPLETED", (
            "stand: the seed operation did not complete, so the row under test is not journalled"
        )

    assert flush_refusal is not None, f"an uninstrumented {effect} was not refused: {stored}"
    assert flush_refusal.reason == journal.Reason.NO_OPERATION, flush_refusal
    assert commit_refusal is not None, "the swallowed refusal did not survive into the commit"
    assert stored == expected, f"an uninstrumented {effect} changed the table: {stored}"


@pytest.mark.asyncio
async def test_a_refused_write_poisons_the_transaction_until_it_is_rolled_back(
    stand: Stand,
) -> None:
    """The journal's OWN refusal of a transaction that already tried to write unrecorded money.

    This is the half the previous test deliberately does not claim. A Core `update(Debt)` is
    refused by the write guard, and - unlike a failed flush - it leaves the Session perfectly
    usable, so SQLAlchemy has no opinion about the commit that follows. What refuses that commit is
    this module's poison, by its own name, and only a real rollback lifts it.

    MUTATION that must redden this: stop calling `_poison_connection` in `_on_before_execute`, and
    the caller that swallowed the refusal commits whatever else the transaction was holding.
    """

    async with stand.factory() as session:
        with pytest.raises(journal.DebtJournalError) as refused_write:
            await session.execute(update(Debt).values(amount=exact_money("40.00")))
        with pytest.raises(journal.DebtJournalError) as refused_commit:
            await session.commit()

        # NON-VACUITY: the poison is lifted by a real rollback, so the refusal above is a state and
        # not a permanent disability of the stand.
        await session.rollback()
        ident = identity("after-poison")
        async with stand.operation("after-poison", session=session, identity=ident):
            session.add(stand.debt("41.00"))
            await session.flush()
        await session.commit()

    assert refused_write.value.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refused_write.value
    assert refused_commit.value.reason == journal.Reason.ROOT_POISONED, refused_commit.value
    assert [row["state"] for row in await stand.envelopes(ident)] == ["COMPLETED"]
    assert await stand.stored_debts() == {("debtor", "creditor", "eq"): Decimal("41.00000000")}


@pytest.mark.asyncio
async def test_the_hook_refuses_an_effect_outside_the_declared_scope(stand: Stand) -> None:
    """C20. An operation that declared one equivalent and moved money in another is refused.

    MUTATION that must redden this: drop the `op.scope_equivalent_ids` check in
    `_effects_of_flush`, and the operation records a movement it never said it would make.
    """

    refusal: BaseException | None = None
    async with stand.factory() as session:
        try:
            async with stand.operation("scope", session=session):
                session.add(stand.debt("18.00", equivalent_id=stand.other_equivalent_id))
                await session.flush()
        except journal.DebtJournalError as exc:
            refusal = exc
        try:
            await session.commit()
        except journal.DebtJournalError:
            pass

    stored = await stand.stored_debts()
    assert refusal is not None, f"an out-of-scope effect was recorded: {stored}"
    assert refusal.reason == journal.Reason.OUT_OF_SCOPE, refusal
    # NON-VACUITY: the same write INSIDE the scope is accepted, so the refusal is about the scope
    # and not about this stand being unable to write to that equivalent at all.
    async with stand.factory() as session:
        async with stand.operation(
            "scope-control",
            session=session,
            scope_equivalent_ids=frozenset({stand.other_equivalent_id}),
        ):
            session.add(stand.debt("18.00", equivalent_id=stand.other_equivalent_id))
            await session.flush()
        await session.commit()
    assert (await stand.stored_debts()) == {
        ("debtor", "creditor", "other"): Decimal("18.00000000")
    }, "the control write was refused too, so the scope check proved nothing"


@pytest.mark.asyncio
async def test_the_hook_refuses_moving_a_stored_debt_to_another_edge(stand: Stand) -> None:
    """C3. An edge is an identity: a debt does not change who owes whom.

    MUTATION that must redden this: remove the `history.deleted` check on the key columns in
    `_effects_of_flush`, and a debt silently migrates to another creditor with no D and no I.
    """

    async with stand.factory() as session:
        async with stand.operation("seed", session=session):
            session.add(stand.debt("19.00"))
            await session.flush()
        await session.commit()

    refusal: BaseException | None = None
    async with stand.factory() as session:
        existing = (await session.execute(select(Debt))).scalars().one()
        with pytest.raises(journal.DebtJournalError) as caught:
            async with stand.operation("move", session=session):
                existing.creditor_id = stand.extra_ids[0]
                await session.flush()
        refusal = caught.value
        await session.rollback()
    stored = await stand.stored_debts()

    assert refusal is not None, f"the debt was moved to another edge unrecorded: {stored}"
    assert refusal.reason == journal.Reason.KEY_FIELD_CHANGED, refusal
    # NON-VACUITY: the row that was to be moved is still where it was.
    assert stored == {("debtor", "creditor", "eq"): Decimal("19.00000000")}, stored


# =================================================================================================
# The write guard
# =================================================================================================


@pytest.mark.parametrize(
    "form",
    ["core_update", "core_delete", "core_insert", "bulk_save_objects", "bulk_insert_mappings"],
)
@pytest.mark.asyncio
async def test_the_write_guard_refuses_every_route_that_skips_the_flush_plan(
    stand: Stand, form: str
) -> None:
    """C2. Core DML and the `bulk_*` entry points never reach the hook, so the guard refuses them.

    These are not exotic: `bulk_save_objects` sets `Session._flushing` itself
    (`sqlalchemy/orm/session.py:4691-4719`), which is precisely why a grant keyed on that flag
    alone let it through in the design's first probe.

    MUTATION that must redden this: return early from `_on_before_execute` when the statement is
    not an `UpdateBase` whose own `.table` is `debts` - which is the version without the
    `visitors.iterate` scan and without the fail-closed "no readable rows" branch.
    """

    async with stand.factory() as session:
        async with stand.operation("seed", session=session):
            session.add(stand.debt("21.00"))
            await session.flush()
        await session.commit()
    before = await stand.stored_debts()

    refusal: BaseException | None = None
    async with stand.factory() as session:
        try:
            if form == "core_update":
                await session.execute(update(Debt).values(amount=exact_money("99.00")))
            elif form == "core_delete":
                await session.execute(delete(Debt))
            elif form == "core_insert":
                await session.execute(
                    insert(Debt).values(**stand.debt_values("22.00", creditor_id=stand.extra_ids[0]))
                )
            elif form == "bulk_save_objects":
                await session.run_sync(
                    lambda sync: sync.bulk_save_objects(
                        [stand.debt("23.00", creditor_id=stand.extra_ids[0])]
                    )
                )
            else:
                await session.run_sync(
                    lambda sync: sync.bulk_insert_mappings(
                        Debt, [stand.debt_values("24.00", creditor_id=stand.extra_ids[0])]
                    )
                )
        except journal.DebtJournalError as exc:
            refusal = exc
        try:
            await session.commit()
        except Exception:  # noqa: BLE001 - the commit must not go through; by which route is the
            pass          # subject of `test_a_refused_write_poisons_...`, not of this test

    after = await stand.stored_debts()

    # NON-VACUITY: there was a row to damage, and the guard is what stopped it.
    assert before == {("debtor", "creditor", "eq"): Decimal("21.00000000")}, before
    assert refusal is not None, f"{form} reached `debts` with no operation: {after}"
    assert refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal
    assert after == before, f"{form} changed the table: {after}"


@pytest.mark.asyncio
async def test_the_write_guard_control_reads_and_other_tables_are_untouched(stand: Stand) -> None:
    """C2's anti-vacuum control. A guard that refused everything would pass the test above.

    A SELECT over `debts` and a DML against a table that is not `debts` must both go through
    untouched, and the journal's own tables must be readable.

    MUTATION that must redden this: make `_on_before_execute` refuse whenever `_dml_tables` is
    non-empty, without checking which tables they are.
    """

    from app.db.models.equivalent import Equivalent

    async with stand.factory() as session:
        rows = (await session.execute(select(Debt))).scalars().all()
        await session.execute(
            update(Equivalent)
            .where(Equivalent.id == stand.other_equivalent_id)
            .values(description="p015-b4a control")
        )
        await session.commit()

    assert rows == [], "stand: the world already had debts, so the SELECT proves less than it looks"
    changed = await stand.rows(
        select(Equivalent.description).where(Equivalent.id == stand.other_equivalent_id)
    )
    assert changed == [{"description": "p015-b4a control"}], changed
    assert await stand.counts() == {
        "debt_operations": 0,
        "debt_journal_entries": 0,
        "debt_operation_equivalents": 0,
    }


@pytest.mark.asyncio
async def test_the_write_guard_refuses_core_writes_to_the_journal_tables(stand: Stand) -> None:
    """The journal's own tables accept writes from the journal and from nothing else.

    Only the Core forms are tested, and that is the whole set that exists: these tables are
    unmapped, so `session.add`, `merge` and the three `bulk_*` entry points cannot address them at
    all. Testing those would exercise SQLAlchemy's inability to find a mapper, not this guard - a
    measurement of nothing. The test that the tables STAY unmapped is the one below.

    MUTATION that must redden this: drop the `journal_targets` branch from `_on_before_execute`,
    and a forged envelope walks in.
    """

    ident = identity("forge")
    async with stand.factory() as session:
        async with stand.operation("forge", session=session, identity=ident):
            session.add(stand.debt("25.00"))
            await session.flush()
        await session.commit()

    real = (await stand.envelopes(ident))[0]

    refusals = {}
    async with stand.factory() as session:
        for name, statement in (
            (
                "insert",
                insert(debt_operations).values(
                    id=uuid.uuid4(),
                    kind="TEST_FIXTURE",
                    identity=identity("forged"),
                    intent={},
                    intent_digest="0" * 64,
                    schema_version=1,
                    money_encoding_version=1,
                    intent_encoding_version=1,
                    state="OPEN",
                ),
            ),
            ("update", update(debt_operations).values(kind="PAYMENT")),
            ("delete", delete(debt_journal_entries)),
        ):
            try:
                await session.execute(statement)
            except journal.DebtJournalError as exc:
                refusals[name] = exc.reason
        try:
            await session.commit()
        except journal.DebtJournalError:
            pass

    # NON-VACUITY: there was a real envelope to forge next to and to damage.
    assert real["state"] == "COMPLETED", real

    assert refusals == {
        "insert": journal.Reason.JOURNAL_TABLE_WRITE,
        "update": journal.Reason.JOURNAL_TABLE_WRITE,
        "delete": journal.Reason.JOURNAL_TABLE_WRITE,
    }, refusals
    assert await stand.counts() == {
        "debt_operations": 1,
        "debt_journal_entries": 1,
        "debt_operation_equivalents": 1,
    }


def test_the_journal_tables_are_not_mapped_by_anything() -> None:
    """The guard's enforcement rests on the journal tables being unreachable from the ORM.

    The day one of them acquires a mapper, `session.add`, `merge`, the three `bulk_*` entry points
    and every cascade become write paths nothing inspects - and the test above stops describing the
    whole set of ways in.

    MUTATION that must redden this: map any declarative class onto `debt_operations`.
    """

    assert journal.mapped_journal_tables() == set()


@pytest.mark.asyncio
async def test_the_write_guard_names_exec_driver_sql_as_its_one_blind_spot(stand: Stand) -> None:
    """The documented limit, with the contrasting half that makes it a boundary and not a gap.

    `exec_driver_sql` fires no `before_execute` at all (`sqlalchemy/engine/base.py:1712-1778`), so
    the guard cannot see it. Asserting only that would be a test that is green today and green
    forever, certifying the hole. The pair is what measures a boundary: the SAME update, through
    `session.execute`, is refused.

    MUTATION that must redden this: any change that makes the two halves agree - either the driver
    path starts being intercepted (then the first assertion fails and the limit has moved, which
    the docstring of `_on_before_execute` must be updated to say), or the expression path stops
    being refused.
    """

    async with stand.factory() as session:
        async with stand.operation("seed", session=session):
            session.add(stand.debt("26.00"))
            await session.flush()
        await session.commit()

    async with stand.engine.begin() as connection:
        # Scoped to this stand's equivalent: the database is the tier's, not a file of this test's.
        await connection.exec_driver_sql(
            f"UPDATE debts SET amount = 27.0 WHERE equivalent_id = '{stand.equivalent_id.hex}'"
        )
    through_the_driver = await stand.stored_debts()

    refusal: BaseException | None = None
    async with stand.factory() as session:
        try:
            await session.execute(update(Debt).values(amount=exact_money("28.00")))
        except journal.DebtJournalError as exc:
            refusal = exc
        try:
            await session.commit()
        except journal.DebtJournalError:
            pass
    through_the_expression = await stand.stored_debts()

    assert through_the_driver == {("debtor", "creditor", "eq"): Decimal("27.00000000")}, (
        "stand: `exec_driver_sql` did not change the row, so the documented limit is not what this "
        "half measured"
    )
    assert refusal is not None and refusal.reason == journal.Reason.UNVERIFIED_DEBT_WRITE, refusal
    assert through_the_expression == through_the_driver, (
        f"the expression-level UPDATE changed the table despite the refusal: "
        f"{through_the_expression}"
    )


# =================================================================================================
# Money: four predicates, each refusing under its own name
# =================================================================================================


@pytest.mark.parametrize(
    ("raw", "expected_reason"),
    [
        (Decimal("NaN"), journal.Reason.MONEY_FINITENESS),
        (float("nan"), journal.Reason.MONEY_FINITENESS),
        (Decimal("Infinity"), journal.Reason.MONEY_FINITENESS),
        (Decimal("1E12"), journal.Reason.MONEY_MAGNITUDE),
        (Decimal("0.123456789"), journal.Reason.MONEY_QUANTIZATION),
    ],
)
@pytest.mark.asyncio
async def test_the_hook_refuses_an_unstorable_amount_by_the_predicate_it_violates(
    stand: Stand, raw, expected_reason: str
) -> None:
    """C12. Four predicates, and each refusal has to name the one it violated.

    They are not interchangeable and running them together as one "storability" set is wrong for
    two of them: `1E12` and `Infinity` round-trip on SQLite BYTE FOR BYTE, so a round-trip
    assertion on those passes while proving nothing about them. `NaN` is the sharpest case -
    `debts.amount NOT NULL` refuses it on SQLite today, because the driver turns a bound NaN into
    NULL, and that is a constraint answering a different question (T1526, `app/db/types.py`).

    Every refusal here happens BEFORE any SQL: no debt statement reaches the connection at all.

    MUTATION that must redden this: collapse `_check_storable` into a single `is_storable` raising
    one reason, and each parametrisation fails on the reason it expected.

    THE FOURTH PREDICATE, ROUND TRIP, IS NOT A CASE HERE, and that is a measurement of PostgreSQL,
    not a gap: `NUMERIC(20, 8)` through asyncpg is exact, so every value the first three predicates
    let through reads back unchanged and nothing on this tier can be refused as `money_round_trip`.
    The value that used to be its case, `100000000000.00000001`, is asserted STORED EXACTLY here by
    `test_a_value_sqlite_would_change_is_exact_money_on_postgresql`; the refusal itself is still
    measured on the SQLite stand by `test_the_round_trip_predicate_refuses_what_sqlite_would_change`,
    the only dialect in this repository on which it can fire.
    """

    await _assert_refused_before_any_debt_sql(stand, raw, expected_reason)


@pytest.mark.asyncio
async def test_the_round_trip_predicate_refuses_what_sqlite_would_change(tmp_path) -> None:
    """C12, the fourth predicate, on the one dialect where it can fire: SQLite's float binding.

    `100000000000.00000001` passes finiteness, magnitude and quantization, and SQLite would store
    it as a different number. A SQLITE MECHANISM TEST, kept on the SQLite stand on purpose and left
    for the slice that removes SQLite (programme 017 stage 3) - on PostgreSQL the same value is
    exact money (`test_a_value_sqlite_would_change_is_exact_money_on_postgresql`).

    MUTATION that must redden this: drop the `_round_trip` comparison from `_check_storable`.
    """

    built = await new_sqlite_stand(tmp_path, extra_participants=2)
    try:
        await _assert_refused_before_any_debt_sql(
            built, Decimal("100000000000.00000001"), journal.Reason.MONEY_ROUND_TRIP
        )
    finally:
        await built.close()


@pytest.mark.asyncio
async def test_a_value_sqlite_would_change_is_exact_money_on_postgresql(stand: Stand) -> None:
    """The round-trip predicate's silence on PostgreSQL is correct, and this shows why.

    The value SQLite refuses as `money_round_trip` is inside `NUMERIC(20, 8)` and PostgreSQL holds
    it byte for byte: the debt, the entry and the read-back agree to the last atom. If the predicate
    were silent because it was blind rather than because the value is exact, the stored amount below
    would differ from the one written.

    MUTATION that must redden this: store `float(value)` in `_effects_of_flush`'s entry amounts, or
    make `_round_trip` quantize to fewer places.
    """

    ident = identity("pg-exact")
    value = Decimal("100000000000.00000001")
    async with stand.factory() as session:
        async with stand.operation("pg-exact", session=session, identity=ident):
            session.add(stand.debt("0", raw_amount=value))
            await session.flush()
        await session.commit()

    assert await stand.stored_debts() == {("debtor", "creditor", "eq"): value}
    entries = await stand.entries(ident)
    assert [(row["effect"], row["amount_after"], row["delta"]) for row in entries] == [
        ("I", value, value)
    ], entries


async def _assert_refused_before_any_debt_sql(stand: Stand, raw, expected_reason: str) -> None:
    debt_statements: list[str] = []

    def _watch(_conn, clause, _multiparams, _params, _options) -> None:
        if getattr(getattr(clause, "table", None), "name", None) == "debts":
            debt_statements.append(type(clause).__name__)

    event.listen(stand.engine.sync_engine, "before_execute", _watch)
    refusal: BaseException | None = None
    try:
        async with stand.factory() as session:
            try:
                async with stand.operation("unstorable", session=session):
                    session.add(stand.debt("0", raw_amount=raw))
                    await session.flush()
            except journal.DebtJournalError as exc:
                refusal = exc
            try:
                await session.commit()
            except journal.DebtJournalError:
                pass
    finally:
        event.remove(stand.engine.sync_engine, "before_execute", _watch)

    assert refusal is not None, f"{raw!r} was accepted as money"
    assert refusal.reason == expected_reason, (
        f"{raw!r} was refused as {refusal.reason}, not {expected_reason}: the refusal names a "
        f"mechanism other than the one that actually excludes it"
    )
    assert debt_statements == [], (
        f"the refusal happened after a statement against `debts` had already been built: "
        f"{debt_statements}"
    )
    assert await stand.stored_debts() == {}


@pytest.mark.asyncio
async def test_nan_is_refused_by_the_hook_and_not_by_the_not_null_that_fires_today(
    stand: Stand,
) -> None:
    """C12's non-vacuity control for NaN, which needs one of its own on SQLite.

    Removing the hook does NOT make a NaN write succeed here - it fails anyway, on
    `debts.amount NOT NULL`, because `sqlite3` converts a bound NaN to SQL NULL. A test that only
    asserted "it failed" would therefore be green with or without the journal and would be
    measuring the wrong constraint. This one shows the two mechanisms are DISTINGUISHABLE: with
    the journal, the refusal arrives before any statement and names finiteness; with the journal
    stood down, the same value reaches the driver and the failure is a database error naming
    NOT NULL.

    MUTATION that must redden this: make `_check_storable` skip the finiteness predicate, and the
    first half starts producing the second half's failure.
    """

    from sqlalchemy.exc import StatementError

    # With the journal: refused before SQL, by finiteness.
    async with stand.factory() as session:
        with pytest.raises(journal.DebtJournalError) as with_journal:
            async with stand.operation("nan", session=session):
                session.add(stand.debt("0", raw_amount=float("nan")))
                await session.flush()
        try:
            await session.commit()
        except journal.DebtJournalError:
            pass
    assert with_journal.value.reason == journal.Reason.MONEY_FINITENESS, with_journal.value

    # Journal stood down on this stand: the same value now reaches the driver, and what refuses it
    # is something else entirely.
    journal.uninstall_journal(stand.engine, stand.session_class)
    async with stand.factory() as session:
        with pytest.raises((StatementError, Exception)) as without_journal:
            session.add(stand.debt("0", raw_amount=float("nan")))
            await session.flush()
        await session.rollback()
    journal.install_journal(stand.engine, stand.session_class)

    text_of_failure = str(without_journal.value)
    assert not isinstance(without_journal.value, journal.DebtJournalError), (
        "stand: the journal was still armed for the second half"
    )
    assert "non-finite" in text_of_failure or "NOT NULL" in text_of_failure, (
        f"stand: the un-journalled NaN write failed for a third reason, so this test no longer "
        f"distinguishes the two it is about: {text_of_failure}"
    )
    assert await stand.stored_debts() == {}


# =================================================================================================
# Open-time refusals
# =================================================================================================


@pytest.mark.asyncio
async def test_an_operation_refuses_to_open_on_an_engine_with_no_write_guard() -> None:
    """An operation on an un-instrumented engine would record what it was told and miss the rest.

    MUTATION that must redden this: drop the `journal_is_installed` check from
    `_refuse_unusable_transaction`.
    """

    built = await new_postgres_stand()
    journal.uninstall_journal(built.engine, built.session_class)
    try:
        async with built.factory() as session:
            with pytest.raises(journal.DebtJournalError) as refusal:
                async with built.operation("unarmed", session=session):
                    pass
        assert refusal.value.reason == journal.Reason.ENGINE_NOT_INSTRUMENTED, refusal.value
        # NON-VACUITY: arming the same engine makes the same call succeed.
        journal.install_journal(built.engine, built.session_class)
        ident = identity("armed")
        async with built.factory() as session:
            async with built.operation("armed", session=session, identity=ident):
                pass
            await session.commit()
        assert [row["state"] for row in await built.envelopes(ident)] == ["COMPLETED"]
    finally:
        await built.close(purge=True)


@pytest.mark.asyncio
async def test_an_operation_refuses_to_open_on_sqlite_without_transaction_control(tmp_path) -> None:
    """T1525 is a precondition, not an optional extra, and the journal checks it at the door.

    Without explicit transaction control a savepoint opened before the first write IS the
    transaction on SQLite, its RELEASE commits it, and a root rollback undoes nothing - so the
    journal's entire "the record commits with the money" promise would be false on that engine.

    MUTATION that must redden this: drop the `sqlite_transaction_control_is_installed` branch from
    `_refuse_unusable_transaction`.
    """

    from sqlalchemy.orm import Session
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.db.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'raw.db').as_posix()}")

    class _RawSession(Session):
        pass

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, sync_session_class=_RawSession, autoflush=False
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    journal.install_journal(engine, _RawSession)
    try:
        async with factory() as session:
            with pytest.raises(journal.DebtJournalError) as refusal:
                async with journal.debt_operation(
                    session, kind="SEED", identity=identity("uncontrolled"), intent={}
                ):
                    pass
        assert refusal.value.reason == journal.Reason.NO_TRANSACTION_CONTROL, refusal.value

        # NON-VACUITY: installing the control makes the same open succeed on the same engine.
        from app.db.sqlite_transaction_control import install_sqlite_transaction_control

        install_sqlite_transaction_control(engine.sync_engine)
        async with factory() as session:
            async with journal.debt_operation(
                session, kind="SEED", identity=identity("controlled"), intent={}
            ):
                pass
            await session.commit()
    finally:
        journal.uninstall_journal(engine, _RawSession)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_operation_refuses_to_open_inside_another_one(stand: Stand) -> None:
    """Operations do not nest: two open envelopes cannot divide one flush's effects between them.

    MUTATION that must redden this: drop the `not existing.is_settled` loop in `debt_operation`.
    """

    async with stand.factory() as session:
        async with stand.operation("outer", session=session):
            with pytest.raises(journal.DebtJournalError) as refusal:
                async with stand.operation("inner", session=session):
                    pass
            assert refusal.value.reason == journal.Reason.NESTED_OPERATION, refusal.value
        # NON-VACUITY: the outer operation itself was fine, and completes.
        await session.commit()
    assert (await stand.counts())["debt_operations"] == 1


@pytest.mark.asyncio
async def test_a_business_failure_discards_its_own_operation_and_refuses_the_commit(
    stand: Stand,
) -> None:
    """C11's body half. A failure in the caller's code is not a journal malfunction - but the
    transaction that carried it still may not commit unrecorded work.

    MUTATION that must redden this: complete the record in a `finally` instead of on the clean
    path, and the failed operation's envelope appears as COMPLETED.
    """

    ident = identity("failing")
    async with stand.factory() as session:
        with pytest.raises(_BusinessFailure):
            async with stand.operation("failing", session=session, identity=ident):
                session.add(stand.debt("29.00"))
                await session.flush()
                raise _BusinessFailure("the payment was rejected")
        with pytest.raises(journal.DebtJournalError) as refusal:
            await session.commit()
    assert refusal.value.reason == journal.Reason.OPERATION_NOT_COMPLETED, refusal.value

    # NON-VACUITY plus verdict: nothing of the failed operation is durable.
    assert await stand.stored_debts() == {}
    assert await stand.envelopes(ident) == []


@pytest.mark.asyncio
async def test_a_debt_already_pending_when_the_operation_opens_is_refused(stand: Stand) -> None:
    """An operation may not adopt work it never declared.

    MUTATION that must redden this: drop the pending-Debt check in `debt_operation`, and a debt
    created before the block is attributed to it.
    """

    async with stand.factory() as session:
        session.add(stand.debt("31.00"))
        with pytest.raises(journal.DebtJournalError) as refusal:
            async with stand.operation("adopt", session=session):
                pass
        assert refusal.value.reason == journal.Reason.INCOMPLETE_DEBT, refusal.value
        await session.rollback()

    # NON-VACUITY: with the debt added INSIDE the block the same code path succeeds.
    ident = identity("declared")
    async with stand.factory() as session:
        async with stand.operation("declared", session=session, identity=ident):
            session.add(stand.debt("31.00"))
            await session.flush()
        await session.commit()
    assert [row["state"] for row in await stand.envelopes(ident)] == ["COMPLETED"]
    assert await stand.stored_debts() == {("debtor", "creditor", "eq"): Decimal("31.00000000")}


@pytest.mark.asyncio
async def test_a_second_open_of_the_same_identity_is_refused_by_the_database(stand: Stand) -> None:
    """C13's duplicate half. `UNIQUE(kind, identity)` is what makes an identity mean something.

    MUTATION that must redden this: drop `uq_debt_operations_kind_identity` from
    `app/db/journal_tables.py` and migration 022, and the same unit of work records itself twice.
    """

    from sqlalchemy.exc import IntegrityError

    ident = identity("duplicate")
    async with stand.factory() as session:
        async with stand.operation("duplicate", session=session, identity=ident):
            session.add(stand.debt("34.00"))
            await session.flush()
        await session.commit()

    async with stand.factory() as session:
        with pytest.raises(IntegrityError):
            async with stand.operation("duplicate", session=session, identity=ident):
                pass
        await session.rollback()

    envelopes = await stand.envelopes(ident)
    assert len(envelopes) == 1 and envelopes[0]["state"] == "COMPLETED", envelopes
    assert await stand.stored_debts() == {("debtor", "creditor", "eq"): Decimal("34.00000000")}


@pytest.mark.asyncio
async def test_a_forged_entry_shape_is_refused_by_the_check_constraints(stand: Stand) -> None:
    """C19. The CHECKs close the shapes a raw writer could otherwise forge.

    The write guard does not see `exec_driver_sql`, so what stands between a raw statement and a
    nonsensical entry is the database itself: an I with a `before`, a U whose two amounts are
    equal, a delta of zero. Shape-VALID forgeries are not closed here and are a step-6 verifier
    item; that is a stated boundary, not a silence.

    MUTATION that must redden this: drop `chk_debt_journal_entries_shape` or
    `chk_debt_journal_entries_delta`.
    """

    ident = identity("shape")
    async with stand.factory() as session:
        async with stand.operation("shape", session=session, identity=ident):
            session.add(stand.debt("36.00"))
            await session.flush()
        await session.commit()
    operation_id = (await stand.envelopes(ident))[0]["id"]

    # ONE TRANSACTION PER SHAPE. On PostgreSQL the first refused statement aborts its transaction,
    # and every later statement in it fails with "current transaction is aborted" whatever its shape
    # - a refusal that would be about the transaction and not about the row. SQLite, where these
    # used to share one transaction, keeps a transaction usable after a constraint failure.
    refused = {}
    why: dict[str, str] = {}
    for name, values in (
        ("insert_with_before", "'I', 1.0, 2.0, 1.0"),
        ("update_that_changed_nothing", "'U', 2.0, 2.0, 1.0"),
        ("zero_delta", "'U', 2.0, 3.0, 0"),
    ):
        async with stand.engine.begin() as connection:
            try:
                await connection.exec_driver_sql(
                    "INSERT INTO debt_journal_entries (id, operation_id, flush_ordinal, "
                    "equivalent_id, debtor_id, creditor_id, effect, amount_before, amount_after, "
                    f"delta) VALUES ('{uuid.uuid4().hex}', '{operation_id.hex}', 9, "
                    f"'{stand.equivalent_id.hex}', '{stand.debtor_id.hex}', "
                    f"'{stand.extra_ids[0].hex}', {values})"
                )
            except Exception as exc:  # noqa: BLE001 - the database's refusal is the subject
                refused[name] = type(exc).__name__
                why[name] = str(exc)

    # NON-VACUITY: a well-shaped row through the same raw path IS accepted, so the refusals above
    # are about the shapes and not about this statement being unusable.
    async with stand.engine.begin() as connection:
        await connection.exec_driver_sql(
            "INSERT INTO debt_journal_entries (id, operation_id, flush_ordinal, equivalent_id, "
            "debtor_id, creditor_id, effect, amount_before, amount_after, delta) VALUES "
            f"('{uuid.uuid4().hex}', '{operation_id.hex}', 9, '{stand.equivalent_id.hex}', "
            f"'{stand.debtor_id.hex}', '{stand.extra_ids[0].hex}', 'U', 2.0, 3.0, 1.0)"
        )

    assert set(refused) == {"insert_with_before", "update_that_changed_nothing", "zero_delta"}, refused
    # Each refusal is a CHECK constraint's, and not a transaction already aborted by the one before.
    assert all("check constraint" in text.lower() for text in why.values()), why
    assert (await stand.counts())["debt_journal_entries"] == 2


@pytest.mark.asyncio
async def test_the_registry_survives_nothing_a_rollback_should_have_cleared(stand: Stand) -> None:
    """A Debt written after the operation's transaction ended is refused (C9, first subcase).

    MUTATION that must redden this: drop `_REGISTRY`'s weak keys for a dict keyed by connection,
    and the finished operation keeps covering the next transaction on the same connection.
    """

    ident = identity("after-the-end")
    async with stand.factory() as session:
        async with stand.operation("after-the-end", session=session, identity=ident):
            session.add(stand.debt("37.00"))
            await session.flush()
        await session.commit()

        # Same session, new transaction: the completed operation must cover nothing here.
        session.add(stand.debt("38.00", creditor_id=stand.extra_ids[0]))
        with pytest.raises(journal.DebtJournalError) as refusal:
            await session.flush()
        await session.rollback()

    assert refusal.value.reason == journal.Reason.NO_OPERATION, refusal.value
    # NON-VACUITY: the first write, inside the operation, really is durable.
    assert await stand.stored_debts() == {("debtor", "creditor", "eq"): Decimal("37.00000000")}


@pytest.mark.asyncio
async def test_the_sqlalchemy_internals_this_module_pins_still_exist(stand: Stand) -> None:
    """The registry reads four private SQLAlchemy attributes. This is where a version bump lands.

    `NestedTransaction._savepoint`, `NestedTransaction._previous_nested`,
    `Connection._execution_options` and `Dialect._on_connect_isolation_level` are private and
    pinned to SQLAlchemy 2.0.25 (design v2 §1.3, R2-1). Using them knowingly means owning a test
    that fails loudly when they move, rather than a journal that silently stops finding savepoints.

    MUTATION that must redden this: none is needed - a SQLAlchemy upgrade is the mutation, which
    is the point.
    """

    import sqlalchemy
    from sqlalchemy.engine.base import NestedTransaction

    assert sqlalchemy.__version__ == "2.0.25", (
        f"SQLAlchemy is {sqlalchemy.__version__}; re-verify the private attributes in "
        f"app/core/ledger/journal.py before changing this pin"
    )
    assert "_savepoint" in NestedTransaction.__slots__ or hasattr(NestedTransaction, "_savepoint")
    async with stand.factory() as session:
        connection = (await session.connection()).sync_connection
        assert isinstance(connection._execution_options, dict) or hasattr(
            connection._execution_options, "get"
        )
        assert hasattr(connection.engine.dialect, "_on_connect_isolation_level")
        nested = await session.begin_nested()
        connection = (await session.connection()).sync_connection
        innermost = connection.get_nested_transaction()
        assert innermost._savepoint.startswith("sa_savepoint")
        assert innermost._previous_nested is None
        await nested.rollback()
        await session.rollback()


@pytest.mark.asyncio
async def test_an_operation_records_an_intent_equivalent_it_never_touched(stand: Stand) -> None:
    """C20's second half. Declaring an equivalent and not touching it is a fact worth recording.

    MUTATION that must redden this: build `debt_operation_equivalents` only from the entries that
    exist, and the intent equivalent with no effects disappears - so "the operation said it would
    and did not" becomes indistinguishable from "it never said so".
    """

    ident = identity("untouched")
    async with stand.factory() as session:
        async with stand.operation(
            "untouched",
            session=session,
            identity=ident,
            scope_equivalent_ids=frozenset({stand.equivalent_id, stand.other_equivalent_id}),
            intent_equivalent_ids=(stand.equivalent_id, stand.other_equivalent_id),
        ):
            session.add(stand.debt("39.00"))
            await session.flush()
        await session.commit()

    completion = {
        row["equivalent_id"]: (row["in_intent"], row["in_scope"], row["effect_count"])
        for row in await stand.operation_equivalents(ident)
    }
    # NON-VACUITY: the touched half really has effects, so a zero count means "not touched" rather
    # than "nothing was recorded at all".
    assert completion[stand.equivalent_id] == (True, True, 1), completion
    assert completion[stand.other_equivalent_id] == (True, True, 0), completion
