"""Programme 015, step 4, T1538: completion accepted a record that did not match what happened.

THE DEFECT, AS MEASURED AT `34cdbc7` BEFORE ANYTHING HERE EXISTED. `_verify_completed_entries`
computed `Counter(stored) - computed` and passed when the difference was empty. That is a membership
test in ONE direction, and an EMPTY stored set satisfies it: an operation whose journal entries were
no longer in the table completed anyway, the envelope was written `COMPLETED` with
`effect_count = 0` and a digest taken over no rows, and the debt movement the operation was opened to
record committed underneath it. Reproduced by `test_t1538_an_operation_whose_entries_are_gone`
below - `debts` at `11.00000000`, envelope COMPLETED, `effect_count = 0`,
`effect_digest = e3b0c442...` (the sha256 of the empty string), zero entries, exit 0 from the commit.

WHY THE CHECK WAS ONE-DIRECTIONAL, AND WHY THE REASON WAS REAL. Entries CAN legitimately be missing
at completion: `PaymentEngine._apply_flow` answers a `StaleDataError` by rolling its flush back to a
savepoint, and that takes the flush's entries with it (migration 023 removed
`flush_count <= effect_count` for exactly this). The old docstring said so and was right. What it
could not do was tell that case from "the entries are not there", because nothing in the module
recorded WHICH ATTEMPTS WERE STILL IN THE TRANSACTION. Four one-directional checks in a row were read
by the external direction assessment of 2026-09-13 as one missing state model rather than four slips,
and a fifth local subtraction was forbidden by name.

WHAT IS BUILT INSTEAD, and it is a fact and not a check. `_observe_savepoint` already kept the
savepoint stack from the SQL stream (T1532, so that no listener can take the account away). It now
distinguishes the two ways a savepoint ends - a `RELEASE` keeps the work, a `ROLLBACK TO` undoes it
and everything nested inside it - and gives each observed savepoint a token that is not its name.
Each flush records the tokens it ran inside (`_OpRecord.attempts`), and completion compares

    surviving computed effects  ==  entries stored for this operation

as an exact multiset, in both directions. A rolled-back attempt is subtracted BY NAME instead of
being tolerated by omission.

THE THREAT MODEL IS ACCIDENTAL, AND THAT IS NOT A FORM OF WORDS. The direction assessment narrowed it
on measured grounds - a source scan found no production listener that rewrites statements outside the
journal itself - so hostile in-process code is out of scope and `T1531` and `T1536` are parked.
Nothing here is built for them. What these tests measure is the OPERATION BOUNDARY'S RESPONSE TO A
STATE: "this operation's entries are not what it still claims to have moved". The state is what a
defect in a maintained writer, or in this module, produces; `exec_driver_sql` is used below only as
the INSTRUMENT that puts the stand into that state cheaply, because it is the one documented way into
these tables that dispatches no `before_execute`. Which route produced the state is not what is under
test, and no barrier against that route is built or claimed.

TIER. SQLite, the default tier, one database file per test under `tmp_path`, money inside `|v| < 2^26`
(design v2 §4).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.core.ledger import journal
from app.db.models.debt import Debt
from tests.p015_b4a_stand import Stand, new_sqlite_stand


@pytest_asyncio.fixture
async def stand(tmp_path):
    built = await new_sqlite_stand(tmp_path, extra_participants=1)
    try:
        yield built
    finally:
        await built.close()


async def _committed_debt(stand: Stand, amount: str, **edge: Any) -> uuid.UUID:
    async with stand.factory() as session:
        subject = stand.debt(amount, **edge)
        async with stand.operation("t1538-seed", session=session):
            session.add(subject)
            await session.flush()
        await session.commit()
        return subject.id


async def _stored_amount(stand: Stand, debt_id: uuid.UUID) -> Decimal | None:
    async with stand.factory() as fresh:
        return (
            await fresh.execute(select(Debt.amount).where(Debt.id == debt_id))
        ).scalar_one_or_none()


#: The rows of the operation that is still OPEN, and only those. Scoped rather than swept, because a
#: helper that also removed the seed operation's entries would be disturbing a COMPLETED record whose
#: envelope nothing re-reads - a different scenario wearing this one's name.
_OPEN_OPERATION_ROWS = (
    "{verb} FROM debt_journal_entries WHERE operation_id IN "
    "(SELECT id FROM debt_operations WHERE state = 'OPEN')"
)


async def _remove_the_entries(session: Any) -> int:
    """Put this operation's entries out of the table, and report how many rows went.

    THE INSTRUMENT, NOT THE THREAT. See the module docstring: what is under test is what completion
    does when the entries are not what the operation still claims, and this is the cheapest way to
    reach that state from a test. `exec_driver_sql` dispatches no `before_execute`
    (`sqlalchemy/engine/base.py`), which is the write guard's one documented blind spot and the same
    door `tests/p015_b4a_stand.py::purge` uses for teardown.

    THE COUNT IS RETURNED AND EVERY CALLER ASSERTS ON IT. A helper that quietly removed nothing would
    turn every test below into a measurement of an operation that was never disturbed - a green that
    means the opposite of what it reads as (`AGENTS.md` §9, anti-vacuum).
    """

    connection = await session.connection()
    result = await connection.exec_driver_sql(_OPEN_OPERATION_ROWS.format(verb="DELETE"))
    return result.rowcount


async def _refusal_of(awaitable) -> journal.DebtJournalError | None:
    try:
        await awaitable
    except journal.DebtJournalError as exc:
        return exc
    return None


# =================================================================================================
# The defect
# =================================================================================================


async def test_t1538_an_operation_whose_entries_are_gone(stand: Stand):
    """An operation that cannot show the entries it computed must not complete.

    MEASURED BEFORE THIS EXISTED, at `34cdbc7`: the same scenario committed. `debts` held
    `11.00000000`, the envelope was `COMPLETED` with `flush_count = 1`, `effect_count = 0` and
    `effect_digest = e3b0c442...` - the sha256 of nothing - and the entries table was empty. The
    record said the operation moved no money while the money had moved.
    """

    debt_id = await _committed_debt(stand, "10.00")
    op_identity = f"p015-b4a/t1538-gone/{uuid.uuid4().hex[:12]}"
    removed = 0

    async def scenario() -> None:
        nonlocal removed
        async with stand.factory() as session:
            async with stand.operation("t1538-gone", session=session, identity=op_identity):
                subject = (
                    await session.execute(select(Debt).where(Debt.id == debt_id))
                ).scalar_one()
                subject.amount = Decimal("11.00000000")
                await session.flush()
                removed = await _remove_the_entries(session)
            await session.commit()

    refusal = await _refusal_of(scenario())

    assert removed == 1, (
        f"the instrument removed {removed} entries, so the operation completed with its record "
        f"intact and this test measured nothing"
    )
    assert refusal is not None, (
        "completion accepted an operation whose entries were gone: the envelope closed over an "
        "empty set while the debt movement was still in the transaction"
    )
    assert refusal.reason == journal.Reason.LOST_JOURNAL_ENTRY, (
        f"the refusal came from {refusal.reason}, not from the missing-entry half of the "
        f"correspondence. A refusal by the wrong mechanism reads exactly like the right one."
    )
    assert await _stored_amount(stand, debt_id) == Decimal("10.00000000"), (
        "the debt movement survived the refusal"
    )
    assert await stand.envelopes(op_identity) == [], "an envelope survived the refused operation"


async def test_t1538_an_entry_the_operation_never_computed_is_still_refused(stand: Stand):
    """The direction the old check DID cover is still covered, and under its own name.

    Replacing a one-way subtraction with an equality must not quietly drop the half that worked.
    """

    debt_id = await _committed_debt(stand, "10.00")
    op_identity = f"p015-b4a/t1538-invented/{uuid.uuid4().hex[:12]}"
    injected = 0

    async def scenario() -> None:
        nonlocal injected
        async with stand.factory() as session:
            async with stand.operation("t1538-invented", session=session, identity=op_identity):
                subject = (
                    await session.execute(select(Debt).where(Debt.id == debt_id))
                ).scalar_one()
                subject.amount = Decimal("11.00000000")
                await session.flush()
                connection = await session.connection()
                # A second row on the same flush, on the other edge: a movement no effect describes.
                result = await connection.exec_driver_sql(
                    "INSERT INTO debt_journal_entries "
                    "(id, operation_id, flush_ordinal, equivalent_id, debtor_id, creditor_id, "
                    " effect, amount_before, amount_after, delta) "
                    + _OPEN_OPERATION_ROWS.format(
                        verb="SELECT ?, operation_id, flush_ordinal, equivalent_id, creditor_id, "
                        "debtor_id, effect, amount_before, amount_after, delta"
                    ),
                    (uuid.uuid4().hex,),
                )
                injected = result.rowcount
            await session.commit()

    refusal = await _refusal_of(scenario())

    assert injected == 1, f"the instrument inserted {injected} rows; nothing was measured"
    assert refusal is not None, "completion accepted a movement this operation never computed"
    assert refusal.reason == journal.Reason.UNRECORDED_JOURNAL_ENTRY, (
        f"the refusal came from {refusal.reason}, not from the stored-and-not-computed half"
    )
    assert await _stored_amount(stand, debt_id) == Decimal("10.00000000")


# =================================================================================================
# The legitimate work the guard must not refuse
# =================================================================================================


async def test_t1538_the_stale_data_retry_still_completes(stand: Stand):
    """`PaymentEngine._apply_flow`'s shape, with a REAL `StaleDataError`, still commits.

    A GUARD THAT REFUSES REAL WORK IS THE SAME DEFECT AS ONE THAT ADMITS FALSE WORK, and this is the
    likeliest way to get T1538 wrong: the losing attempt's effects stay in `_OpRecord.effects`
    forever, so an equality that did not subtract rolled-back attempts would refuse the main payment
    path every time it retried.

    The shape is `_apply_flow`'s, not a paraphrase of it: `async with session.begin_nested()`, a
    concurrent version bump inside the savepoint, the `StaleDataError` the ORM raises when the
    versioned UPDATE matches no row, `expire_all()`, and a second attempt that succeeds.
    """

    debt_id = await _committed_debt(stand, "10.00")
    op_identity = f"p015-b4a/t1538-retry/{uuid.uuid4().hex[:12]}"
    stale: StaleDataError | None = None

    async with stand.factory() as session:
        async with stand.operation("t1538-retry", session=session, identity=op_identity):
            subject = (await session.execute(select(Debt).where(Debt.id == debt_id))).scalar_one()
            try:
                async with session.begin_nested():
                    connection = await session.connection()
                    # A concurrent writer moves the row's version on. Undone with the savepoint.
                    await connection.exec_driver_sql(
                        f"UPDATE debts SET version = version + 5 WHERE id = '{debt_id.hex}'"
                    )
                    subject.amount = Decimal("11.00000000")
                    await session.flush()
            except StaleDataError as exc:
                stale = exc
            session.expire_all()
            subject = (await session.execute(select(Debt).where(Debt.id == debt_id))).scalar_one()
            subject.amount = Decimal("11.00000000")
            await session.flush()
        await session.commit()

    assert stale is not None, (
        "the losing attempt did not raise StaleDataError, so this test measured an ordinary "
        "operation and not the retry it is named after"
    )
    envelope = await stand.envelopes(op_identity)
    assert len(envelope) == 1 and envelope[0]["state"] == "COMPLETED", envelope
    assert envelope[0]["flush_count"] == 2, (
        f"flush_count is {envelope[0]['flush_count']}: the rolled-back attempt was not counted, so "
        f"the scenario never produced the state this test is about"
    )
    assert envelope[0]["effect_count"] == 1, envelope
    entries = await stand.entries(op_identity)
    assert [entry["flush_ordinal"] for entry in entries] == [2], entries
    assert await _stored_amount(stand, debt_id) == Decimal("11.00000000")


async def test_t1538_a_rollback_undoes_the_savepoints_nested_inside_it(stand: Stand):
    """`ROLLBACK TO SAVEPOINT x` undoes everything after `x`, and the account has to say so.

    The inner savepoint gets no statement of its own when the outer one is rolled back. An attempt
    account that only marked the savepoint NAMED BY THE STATEMENT would leave the inner flush's
    effects outstanding, and the operation would be refused for work that was correctly undone.
    """

    debt_id = await _committed_debt(stand, "10.00")
    op_identity = f"p015-b4a/t1538-nested/{uuid.uuid4().hex[:12]}"

    async with stand.factory() as session:
        async with stand.operation("t1538-nested", session=session, identity=op_identity):
            subject = (await session.execute(select(Debt).where(Debt.id == debt_id))).scalar_one()
            subject.amount = Decimal("11.00000000")
            await session.flush()  # ordinal 1, at the root: survives
            outer = await session.begin_nested()
            inner = await session.begin_nested()
            subject.amount = Decimal("12.00000000")
            await session.flush()  # ordinal 2, inside BOTH savepoints
            await inner.commit()  # RELEASE the inner one - its work is still in the outer scope
            await outer.rollback()  # and the outer rollback takes it after all
            session.expire_all()
        await session.commit()

    envelope = await stand.envelopes(op_identity)
    assert len(envelope) == 1 and envelope[0]["state"] == "COMPLETED", envelope
    assert envelope[0]["flush_count"] == 2, envelope
    assert envelope[0]["effect_count"] == 1, envelope
    assert [entry["flush_ordinal"] for entry in await stand.entries(op_identity)] == [1]
    assert await _stored_amount(stand, debt_id) == Decimal("11.00000000")


async def test_t1538_a_released_savepoint_is_not_a_rolled_back_one(stand: Stand):
    """Work inside a RELEASED savepoint still has to show its entries.

    THE COUNTER-CHECK FOR THE NEW BRANCH, and without it the branch could be vacuous in the
    dangerous direction: an account that marked every closed savepoint as rolled back would subtract
    released attempts too, and the missing-entry half would then pass for any operation that used a
    savepoint at all - which is every payment. The scenario is the one above with the rollback
    replaced by a release, and the verdict is the opposite.
    """

    debt_id = await _committed_debt(stand, "10.00")
    op_identity = f"p015-b4a/t1538-released/{uuid.uuid4().hex[:12]}"
    removed = 0

    async def scenario() -> None:
        nonlocal removed
        async with stand.factory() as session:
            async with stand.operation("t1538-released", session=session, identity=op_identity):
                subject = (
                    await session.execute(select(Debt).where(Debt.id == debt_id))
                ).scalar_one()
                nested = await session.begin_nested()
                subject.amount = Decimal("11.00000000")
                await session.flush()
                await nested.commit()  # RELEASE: the movement stays in the transaction
                removed = await _remove_the_entries(session)
            await session.commit()

    refusal = await _refusal_of(scenario())

    assert removed == 1, f"the instrument removed {removed} entries; nothing was measured"
    assert refusal is not None, (
        "an operation whose only flush ran inside a RELEASED savepoint completed without its "
        "entries: the account is treating a release as a rollback"
    )
    assert refusal.reason == journal.Reason.LOST_JOURNAL_ENTRY, refusal.reason
    assert await _stored_amount(stand, debt_id) == Decimal("10.00000000")


async def test_t1538_an_ordinary_operation_is_untouched(stand: Stand):
    """No savepoint anywhere: the exact correspondence holds and says nothing.

    The baseline the other tests are read against - an operation with an empty attempt account must
    complete exactly as it did before T1538.
    """

    debt_id = await _committed_debt(stand, "10.00")
    op_identity = f"p015-b4a/t1538-plain/{uuid.uuid4().hex[:12]}"

    async with stand.factory() as session:
        async with stand.operation("t1538-plain", session=session, identity=op_identity):
            subject = (await session.execute(select(Debt).where(Debt.id == debt_id))).scalar_one()
            subject.amount = Decimal("11.00000000")
            await session.flush()
            subject.amount = Decimal("12.00000000")
            await session.flush()
        await session.commit()

    envelope = await stand.envelopes(op_identity)
    assert len(envelope) == 1 and envelope[0]["state"] == "COMPLETED", envelope
    assert (envelope[0]["flush_count"], envelope[0]["effect_count"]) == (2, 2), envelope
    assert [entry["flush_ordinal"] for entry in await stand.entries(op_identity)] == [1, 2]
    assert await _stored_amount(stand, debt_id) == Decimal("12.00000000")


# =================================================================================================
# The state model itself, read directly
# =================================================================================================


async def test_t1538_the_attempt_account_names_the_scope_each_flush_ran_in(stand: Stand):
    """`_OpRecord.attempts` holds the SQL stream's tokens, and a rollback marks them undone.

    Read on the live state rather than inferred from a verdict: a test that only watched the
    envelope could not tell an account that works from one that is empty and a comparison that never
    fires. `AGENTS.md` §9 - a rule that excludes something carries a counter-check that the
    exclusion is real.
    """

    debt_id = await _committed_debt(stand, "10.00")
    op_identity = f"p015-b4a/t1538-account/{uuid.uuid4().hex[:12]}"
    observed: dict[str, Any] = {}

    async with stand.factory() as session:
        async with stand.operation("t1538-account", session=session, identity=op_identity):
            subject = (await session.execute(select(Debt).where(Debt.id == debt_id))).scalar_one()
            subject.amount = Decimal("11.00000000")
            await session.flush()  # ordinal 1, no savepoint
            nested = await session.begin_nested()
            subject.amount = Decimal("12.00000000")
            await session.flush()  # ordinal 2, inside the savepoint
            root = (await session.connection()).sync_connection.get_transaction()
            state = journal._REGISTRY.get(root)
            record = state.ops[-1]
            observed["attempts"] = dict(record.attempts)
            observed["open"] = [savepoint.name for savepoint in state.savepoints_open]
            await nested.rollback()
            observed["rolled_back"] = set(state.rolled_back_savepoints)
            observed["surviving"] = [
                effect.flush_ordinal for effect in journal._surviving_effects(record, state)
            ]
            session.expire_all()
        await session.commit()

    assert observed["attempts"][1] == (), observed["attempts"]
    assert len(observed["attempts"][2]) == 1, (
        f"the flush inside the savepoint recorded {observed['attempts'][2]} as its scope; the "
        f"account never saw the SAVEPOINT statement"
    )
    assert len(observed["open"]) == 1, observed["open"]
    assert observed["rolled_back"] == set(observed["attempts"][2]), observed
    assert observed["surviving"] == [1], observed["surviving"]


async def test_t1538_a_savepoint_token_is_not_reused(stand: Stand):
    """Two savepoints of the same NAME in one transaction are two scopes, not one.

    SQLAlchemy's savepoint counter lives on the `Connection` and is not reset, so this cannot arise
    on this tree today - which is the reason to pin it rather than to assume it. If identity were the
    name, a rollback of the second scope would mark the first one's flush undone as well, and the
    entries of a flush that is still in the transaction would stop being required.
    """

    state = journal._TxState()
    journal._observe_savepoint(state, "open", "sa_savepoint_1")
    first = state.savepoints_open[0].token
    journal._observe_savepoint(state, "close", "sa_savepoint_1")
    journal._observe_savepoint(state, "open", "sa_savepoint_1")
    second = state.savepoints_open[0].token

    assert first != second, (
        "the same savepoint name was handed the same token twice; the attempt account cannot tell "
        "two scopes apart"
    )

    journal._observe_savepoint(state, "rollback", "sa_savepoint_1")
    assert state.rolled_back_savepoints == {second}, state.rolled_back_savepoints


@pytest.mark.parametrize(
    "kind, expected",
    [("close", set()), ("rollback", {0, 1})],
    ids=["release-keeps-the-work", "rollback-undoes-it-and-everything-inside"],
)
def test_t1538_the_two_closings_are_told_apart(kind: str, expected: set[int]):
    """The whole state model in four lines, with no database in the way."""

    state = journal._TxState()
    journal._observe_savepoint(state, "open", "outer")
    journal._observe_savepoint(state, "open", "inner")
    journal._observe_savepoint(state, kind, "outer")

    assert state.savepoints_open == []
    assert state.rolled_back_savepoints == expected
