"""Programme 015, `B4` step 4: R4 - what arming the journal does to a migrated test, measured.

WHAT R4 ASKS FOR. `specs/015-financial-core-verification/spec.md:1437` replaced the original
trace-equality requirement with an observable one: "`before_cursor_execute` не видит DBAPI
commit/rollback, неявный BEGIN и исключения. Нужны события транзакций, исход завершения, явные
маркеры достижения негативных путей, сохранение параметров и кратности; мутации «убран rollback»,
«перенесён flush», «изменена сумма» обязаны делать гейт красным."

WHAT WAS THERE BEFORE THIS MODULE, AND WHY IT IS NOT ENOUGH. Slice B (`1a5bebc`) did not build the
tracer. It substituted a proof by construction: strip every `debt_fixture_setup` wrapper, splice the
body back into its parent, drop the import, compare the AST with HEAD - 58 of 59 files identical. That
is a real and useful result, and it is the whole of R3 (statement identity). It is NOT R4, for a
reason that only became true when slice C armed the journal: an ACTIVE fixture context is not a no-op.
`tests/debt_setup.py:141` enters `debt_operation`, which flushes at open and flushes again at
completion, so identical statements can still run at different moments. AST equality cannot see that
by construction, and a check that cannot see the thing that changed cannot fail on it.

WHAT THIS MODULE DOES INSTEAD. It records, for one scenario, a trace of what the DATABASE was told and
what happened to the transaction - Core `begin`/`commit`/`rollback`/savepoint events, every statement
with its parameters in order, every error the dialect reported, and the scenario's own outcome - and
then compares three runs of the SAME scenario body:

* `T0` - the UNWRAPPED body (bare `session.add`), journal stood down on this engine;
* `T1` - the WRAPPED body (inside `debt_fixture_setup`), journal stood down;
* `T2` - the WRAPPED body, journal ARMED.

`T1 == T0` exactly, with no filtering at all, is the no-op claim slice B made about the wrapper -
measured here rather than argued from the helper's source. `T2` against `T1` is what activation
changed, and it is asserted per scenario: equal once the journal's own statements are removed where
the block is immediately followed by the test's own flush, and DIFFERENT IN A NAMED, EXACT WAY where
it is not. Both directions are assertions; neither can be satisfied by measuring nothing.

WHAT THE ROUND-3 REVIEW FOUND WRONG HERE, and what this module claims now. The reviewer ran `_trace`
directly and measured three things that made the mutation section false in both directions: two
UNCHANGED armed runs compared UNEQUAL; removing the rollback produced an equal non-journal trace; and
moving the flush produced an equal non-journal trace. So the module distinguished runs that were the
same and failed to distinguish runs that differed. The three causes and their fixes are recorded at
their sites (`_scalar_text`, `_rollback_instead_of_commit`, `_pending_write_after_the_block`), and the
claims are now these, each of them measured:

1. TWO IDENTICAL ARMED RUNS RECORD THE SAME TRACE, verbatim, for every scenario shape - the control
   the whole mutation section rests on, which did not exist before. Three per-run-unique shapes are
   normalised to get there: the fixture identity's `uuid4`, the journal's timestamps, and its effect
   digests. Money, statement text, statement order, multiplicity, transaction events and the
   scenario's own outcome are NOT normalised, because that is where the three mutations live.
2. EACH OF THE THREE NAMED MUTATIONS PRODUCES A DIFFERENCE IN A NAMED ASPECT, in BOTH journal states,
   on a trace from which the journal's own statements have been removed - so the journal's noise
   cannot be what supplies the difference. "убран rollback" moves the transaction-event sequence and
   the outcome; "перенесён flush" moves the statement order; "изменена сумма" moves the bound money
   AND NOTHING ELSE (that last one is stated as an exact claim, not as an inequality).
3. ONE MUTATION CANNOT BE SEEN ON ONE SHAPE, and that is recorded as its own two-directional
   assertion rather than as a claim quietly dropped: moving the test's own `flush()` on shape B is
   invisible while the journal is ARMED, because the completion flush has already drained the unit of
   work. The mutation is carried by shape C instead, where the test's own flush has work of its own.

WHAT REMAINS UNPROVEN, stated plainly because R4's scope is wider than this module's. This compares
five scenarios built to be the shapes the migration produces. It does not re-run all 110 migrated
blocks under both journal states; the evidence that those blocks' STATEMENTS are unchanged is slice
B's AST comparison (R3), and the evidence that their OUTCOMES are unchanged is that both canonical
gates return their baseline counts. Neither of those is a per-block behavioural trace, and this module
does not claim to be one. Nor does it check the CONTENT of the journal's digests: they are compared
only as an equality pattern, because a digest taken over rows with fresh ids cannot be equal across
two runs (see `_DIGEST_TEXT`). What it does close is the specific gap the external review named:
there is a check that observes transaction outcomes and exceptions, that is measurably sensitive to
the three mutations the spec names in both journal states, and that fails when the flush moves.

TIER. SQLite, the default tier; `|v| < 2^26` (design v2 §4). The journal is stood down and re-armed
PER ENGINE inside a `try/finally`; nothing here leaves the shared engine unguarded.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.core.ledger.journal import JOURNAL_STATEMENT_OPTION
from app.db.journal_tables import DEBT_JOURNAL_TABLE_NAMES
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from tests.debt_setup import debt_fixture_setup
from tests.p015_b4_support import World, drop_world, seed_world


# =================================================================================================
# The recorder
# =================================================================================================

#: Core transaction events this records. `before_cursor_execute` sees NONE of them, which is the
#: round-2 reviewer's objection and the reason R4 was rewritten: a DBAPI commit issues no statement,
#: an implicit BEGIN issues no statement, and an exception is not a statement at all.
_TRANSACTION_EVENTS = (
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "rollback_savepoint",
    "release_savepoint",
)

_SAVEPOINT_NAME = re.compile(r"sa_savepoint_\d+")
_WHITESPACE = re.compile(r"\s+")


class _Trace:
    """Everything one run told the database, in order, plus how it ended.

    Attached to the ENGINE for the duration of one scenario and detached immediately: the seeding and
    the teardown around a scenario are not part of what is being compared, and a recorder left
    attached would fold them in.
    """

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._listeners: list[tuple] = []

    # -- recording ---------------------------------------------------------------------------
    def attach(self, engine) -> None:
        sync_engine = engine.sync_engine

        def _transaction(name):
            def _handler(conn, *args) -> None:
                # `savepoint`/`release_savepoint`/`rollback_savepoint` carry the name; `begin`,
                # `commit` and `rollback` carry nothing. Recorded the same way so a savepoint that
                # appeared or vanished is visible as a trace difference rather than as a count.
                payload = tuple(
                    _SAVEPOINT_NAME.sub("sa_savepoint_N", str(arg))
                    for arg in args
                    if isinstance(arg, str)
                )
                self.events.append((name, *payload))

            return _handler

        for name in _TRANSACTION_EVENTS:
            handler = _transaction(name)
            event.listen(sync_engine, name, handler)
            self._listeners.append((sync_engine, name, handler))

        def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            # `journal-sql` IS the journal's own statement, said by the journal rather than guessed
            # from a table name (T1528). Its verification read is a SELECT against `debts`, so the
            # table-name filter below cannot recognise it, and counting it as the writer's work would
            # make "arming adds the journal's own statements and nothing else" false for a statement
            # that is the journal's own. It stays visible in `verbatim`.
            own = bool(context.execution_options.get(JOURNAL_STATEMENT_OPTION))
            kind = "journal-sql" if own else "sql"
            self.events.append((kind, _normalised(statement), _parameters(parameters)))

        event.listen(sync_engine, "after_cursor_execute", _after_cursor_execute)
        self._listeners.append((sync_engine, "after_cursor_execute", _after_cursor_execute))

        def _handle_error(context) -> None:
            self.events.append(("error", type(context.original_exception).__name__))

        event.listen(sync_engine, "handle_error", _handle_error)
        self._listeners.append((sync_engine, "handle_error", _handle_error))

    def detach(self) -> None:
        for target, name, handler in self._listeners:
            event.remove(target, name, handler)
        self._listeners.clear()

    def outcome(self, value) -> None:
        self.events.append(("outcome", value))

    # -- reading -----------------------------------------------------------------------------
    @property
    def without_the_journals_own_statements(self) -> list[tuple]:
        """The trace with every statement OF THE JOURNAL'S OWN removed, ids renumbered.

        Two kinds of those, and the second was added by T1528: a statement against a journal table,
        recognised by its tables, and the journal's verification read of `debts`, recognised by the
        execution option the journal puts on it (`JOURNAL_STATEMENT_OPTION`). Recognising the second
        by its SQL text would make this filter a copy of the journal's current statement.

        THE RENUMBERING HAPPENS AFTER THE FILTER, and that order is load-bearing: the journal's own
        rows carry ids of their own, so numbering before the filter would give the two runs different
        ordinals for the same debt and every comparison below would fail for a reason that is not a
        behaviour change.
        """

        kept = [
            row
            for row in self.events
            if row[0] != "journal-sql"
            and (row[0] != "sql" or not (_words_in(row[1]) & DEBT_JOURNAL_TABLE_NAMES))
        ]
        return _renumbered(kept)

    @property
    def verbatim(self) -> list[tuple]:
        return _renumbered(self.events)


_UUID_TEXT = re.compile(r"\A[0-9a-fA-F]{32}\Z|\A[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")

#: A UUID ANYWHERE inside a longer string. `debt_fixture_setup` builds the operation's identity as
#: `"<node id>:<label>:<uuid4>"` (`tests/debt_setup.py:144`), so the identity of two runs of the same
#: scenario differs by construction. Round 3 measured the consequence: two UNCHANGED armed runs
#: compared UNEQUAL verbatim, which made every "the mutated trace differs" assertion in this module
#: pass for a reason that had nothing to do with the mutation. The UUID is replaced by an ordinal
#: marker rather than dropped, so a run that put a DIFFERENT NUMBER of distinct ids into the string,
#: or ids belonging to other rows, still shows up as a difference.
_UUID_INSIDE_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: `debt_operations.opened_at` / `completed_at` as the SQLite driver receives them. The dialect has
#: already turned the `datetime` into text by the time `after_cursor_execute` fires, so the
#: `isinstance(value, datetime)` branch of `_scalar` never sees them on this tier - the second reason
#: two unchanged armed runs compared unequal.
_TIMESTAMP_TEXT = re.compile(
    r"\A\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\Z"
)

#: A hex SHA-256. `debt_operation_equivalents.effect_digest` and `debt_operations.effect_digest` are
#: computed over rows whose ids are fresh per run, so their VALUES cannot be equal across two runs -
#: the third reason. They are renumbered like ids, which keeps exactly one property: WHICH digests in
#: a trace are equal to which. That is weaker than comparing the digests themselves and it is said
#: plainly - the content of a digest is C14's and C15's subject, not this module's.
_DIGEST_TEXT = re.compile(r"\A[0-9a-f]{64}\Z")


def _normalised(statement: str) -> str:
    return _SAVEPOINT_NAME.sub("sa_savepoint_N", _WHITESPACE.sub(" ", statement).strip()).lower()


def _words_in(statement: str) -> set[str]:
    """Every identifier-shaped word of a normalised statement.

    Used only to ask "does this statement name a journal table?". A word set rather than a parse: the
    three journal table names are unique in this schema, so a word match cannot mistake a `debts`
    statement for a journal one, and a parser here would be machinery nobody could check.
    """

    return set(re.findall(r"[a-z_][a-z0-9_]*", statement))


def _parameters(parameters) -> tuple:
    """Bound parameters as a comparable shape, ids left as markers for `_renumbered`."""

    if parameters is None:
        return ()
    if isinstance(parameters, dict):
        return tuple(sorted((key, _scalar(value)) for key, value in parameters.items()))
    if isinstance(parameters, (list, tuple)):
        return tuple(_parameters(row) if isinstance(row, (dict, list, tuple)) else _scalar(row)
                     for row in parameters)
    return (_scalar(parameters),)


def _scalar(value):
    """One bound value, with everything that cannot be equal across two runs made symbolic.

    A timestamp differs between runs by construction. An id differs too, but NOT arbitrarily: the
    same logical row gets the same ordinal in both runs as long as the runs do the same things in the
    same order, which is exactly the property under test - so ids become `<id k>` and a run that
    wrote a different number of distinct ids, or wrote them in a different order, shows up as a
    difference rather than being papered over.

    WHAT IS DELIBERATELY *NOT* NORMALISED, because the three mutations this module must notice live
    there: money (kept to the digit, which is what makes «изменена сумма» visible), statement text,
    statement order, multiplicity, transaction events and the scenario's own outcome.
    """

    if isinstance(value, uuid.UUID):
        return ("id", value.hex)
    if isinstance(value, datetime):
        return ("ts",)
    if isinstance(value, Decimal):
        return ("money", f"{value:f}")
    if isinstance(value, float):
        return ("money", f"{Decimal(str(value)):f}")
    if isinstance(value, str):
        return _scalar_text(value)
    return value


def _scalar_text(value: str):
    """A bound string, with the three per-run-unique shapes the journal writes made symbolic.

    Checked most specific first: a digest is 64 hex characters and must not be mistaken for an id.
    """

    if _UUID_TEXT.match(value):
        return ("id", uuid.UUID(value).hex)
    if _DIGEST_TEXT.match(value):
        return ("digest", value)
    if _TIMESTAMP_TEXT.match(value):
        return ("ts",)
    found = _UUID_INSIDE_TEXT.findall(value)
    if found:
        # The template keeps everything the string says that is NOT an id - for the fixture identity
        # that is the pytest node id and the block's label, both of which a mutation could change.
        return (
            "text",
            _UUID_INSIDE_TEXT.sub("{id}", value),
            tuple(("id", uuid.UUID(item).hex) for item in found),
        )
    return value


def _renumbered(events: list[tuple]) -> list[tuple]:
    """Replace every `("id", hex)` / `("digest", hex)` marker with an ordinal of first appearance."""

    seen: dict[tuple[str, str], int] = {}

    def _walk(node):
        if isinstance(node, tuple):
            if len(node) == 2 and node[0] in ("id", "digest") and isinstance(node[1], str):
                key = (node[0], node[1])
                return (node[0], seen.setdefault(key, len(seen)))
            return tuple(_walk(item) for item in node)
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return [_walk(row) for row in events]


# =================================================================================================
# The scenarios: one body each, written twice - wrapped and unwrapped
# =================================================================================================
#
# The two forms of each scenario differ by EXACTLY the wrapper, which is what the migration did to
# ~130 call sites. Anything else that differed between them would make `T1 == T0` a statement about
# this module rather than about the helper.


async def _plain_commit(session, world: World, *, wrapped: bool, marker: list) -> None:
    """Shape A: the block is immediately followed by the test's own commit."""

    debt = world.debt("7.00")
    if wrapped:
        async with debt_fixture_setup(session, label="r4-plain"):
            session.add(debt)
    else:
        session.add(debt)
    await session.commit()
    marker.append("committed")


async def _other_sql_before_the_flush(session, world: World, *, wrapped: bool, marker: list) -> None:
    """Shape B: a read runs between the block and the test's own flush.

    This is the shape slice B counted 39 times ("not immediately followed by the file's own flush").
    With the journal armed, the debt INSERT moves to BEFORE the read; with it stood down it stays
    after. That is a real behaviour change and this module asserts it rather than filtering it away.
    """

    debt = world.debt("7.00")
    if wrapped:
        async with debt_fixture_setup(session, label="r4-other-sql"):
            session.add(debt)
    else:
        session.add(debt)
    await session.execute(select(Participant.pid).where(Participant.id == world.debtor.id))
    await session.flush()
    await session.commit()
    marker.append("committed")


async def _rollback_instead_of_commit(session, world: World, *, wrapped: bool, marker: list) -> None:
    """Shape A with a rollback, AND A READ BACK AFTER IT: the carrier of "убран rollback".

    WHY THE READ BACK IS PART OF THE SCENARIO AND NOT DECORATION. Round 3 measured the version
    without it: deleting the `await session.rollback()` line changed NOTHING in the trace, armed or
    stood down. The reason is that the `async with factory() as session` in `_trace` closes the
    session immediately afterwards, and closing a session with an open transaction issues a DBAPI
    rollback of its own - so the Core `rollback` event fires either way, at the same point, with
    nothing between the two positions to tell them apart. A rollback whose removal is followed by
    nothing is unobservable by construction, and a mutation check over that shape cannot fail.

    With the read back the rollback has a consequence: it ends the transaction, so the SELECT opens a
    new one and cannot see the debt. Remove the rollback and the SELECT runs inside the SAME
    transaction as the INSERT and does see it. The mutation therefore changes the transaction-event
    sequence (a `rollback`/`begin` pair disappears) AND the scenario's own outcome - two independent
    differences, both recorded.
    """

    debt = world.debt("7.00")
    if wrapped:
        async with debt_fixture_setup(session, label="r4-rollback"):
            session.add(debt)
    else:
        session.add(debt)
    await session.flush()
    await session.rollback()
    found = (await session.execute(select(Debt.id).where(Debt.id == debt.id))).first()
    marker.append("the debt survived the rollback" if found else "the debt is gone")


def _pending_participant() -> Participant:
    """The non-debt row shape C leaves pending for the test's own `flush()` to send.

    ITS KEYS ARE CONSTANTS, not derived from `world.tag`, and that is load-bearing: `seed_world`
    builds a fresh random tag per run, so a tag-derived `pid` would make two unmutated runs of shape C
    record different bound parameters and `test_r4_two_identical_armed_runs_record_the_same_trace`
    would go red for a reason that is not a behaviour change. A constant is safe because shape C ends
    in `rollback()` - the row is never committed, so the unique `pid` is free again at the next run.
    """

    return Participant(
        pid="B4_R4_PENDING",
        display_name="R4 pending write",
        public_key="pk_b4_r4_pending",
        type="person",
        status="active",
        profile={},
    )


async def _pending_write_after_the_block(session, world: World, *, wrapped: bool, marker: list) -> None:
    """Shape C: the test's own `flush()` still has work OF ITS OWN to send. Carrier of "перенесён flush".

    WHY THIS SHAPE EXISTS. Round 3 measured "перенесён flush" on shape B and found it invisible WITH
    THE JOURNAL ARMED: by the time the test's own `flush()` runs, `debt_operation`'s completion flush
    has already drained the unit of work, so the moved `flush()` sends no statement and moving a
    statement-less call moves nothing. That is a true and important fact about activation - it is
    asserted as such in `test_r4_the_completion_flush_makes_the_tests_own_flush_a_no_op_for_shape_b` -
    but it means shape B cannot carry the mutation on the tier the suite actually runs on.

    Here the pending work is a row the journal does not police (a Participant), added AFTER the block
    closes. The completion flush cannot have sent it, so the test's own `flush()` has something to
    do, and where that `flush()` stands relative to the read is observable in both journal states.

    Nothing is committed: the transaction is rolled back at the end, so the extra Participant never
    becomes a row `drop_world` would have to know about.
    """

    debt = world.debt("7.00")
    if wrapped:
        async with debt_fixture_setup(session, label="r4-pending"):
            session.add(debt)
    else:
        session.add(debt)
    session.add(_pending_participant())
    await session.execute(select(Participant.pid).where(Participant.id == world.debtor.id))
    await session.flush()
    await session.rollback()
    marker.append("rolled back")


async def _duplicate_edge_raises(session, world: World, *, wrapped: bool, marker: list) -> None:
    """The NEGATIVE path, with an explicit marker saying WHERE the refusal arrived.

    `uq_debts_debtor_creditor_equivalent` already holds this edge, so the second row cannot be
    written. Stood down, the `IntegrityError` arrives at the test's own `flush()`; armed, it arrives
    at the block's exit, because the completion flush runs first. That is the case slice B named as
    mattering - "the next statement is a pytest.raises or try that expects the flush to raise" - and
    the marker is what makes the difference observable instead of inferred.
    """

    debt = world.debt("9.00")
    try:
        if wrapped:
            async with debt_fixture_setup(session, label="r4-duplicate"):
                session.add(debt)
        else:
            session.add(debt)
    except IntegrityError:
        marker.append("block exit raised IntegrityError")
    else:
        try:
            await session.flush()
        except IntegrityError:
            marker.append("own flush raised IntegrityError")
        else:
            marker.append("nothing raised")
    await session.rollback()


_SCENARIOS = {
    "a block followed immediately by the test's own commit": _plain_commit,
    "a block with other SQL before the test's own flush": _other_sql_before_the_flush,
    "a block whose transaction is rolled back and then read back": _rollback_instead_of_commit,
    "a block with a pending non-debt write after it": _pending_write_after_the_block,
    "a block whose write violates a unique constraint": _duplicate_edge_raises,
}

#: Scenarios that need the edge to exist before they run.
_NEEDS_A_STARTING_EDGE = {_duplicate_edge_raises}

#: Scenarios whose `debts` INSERT is refused by the database, so the statement never reaches
#: `after_cursor_execute` and the trace carries no bound money. Named so the non-vacuity control can
#: require the refusal instead, rather than being weakened to a disjunction for every scenario.
_SCENARIOS_WHOSE_DEBT_INSERT_IS_REFUSED = {"a block whose write violates a unique constraint"}


# =================================================================================================
# Running one trace
# =================================================================================================


async def _trace(scenario, *, wrapped: bool, armed: bool, amount_shift: Decimal = Decimal(0),
                 drop_the_rollback: bool = False, move_the_flush: bool = False,
                 move_the_flush_on_shape_b: bool = False) -> _Trace:
    """One run of `scenario`, recorded. Everything is cleaned up before the next run.

    `amount_shift`, `drop_the_rollback` and `move_the_flush` are the three mutations `spec.md:1437`
    names, applied to the SCENARIO and not to the journal - they exist so this module can prove its
    own comparison is sensitive to them, which is the only thing that makes the equality assertions
    above evidence rather than decoration.
    """

    from app.core.ledger import journal
    from tests.conftest import TestingSessionLocal as factory, engine

    world = await seed_world(factory)
    trace = _Trace()
    marker: list[str] = []
    try:
        if scenario in _NEEDS_A_STARTING_EDGE:
            # Built BEFORE the block: `fixture_block_violations` allows only constructors and
            # session calls inside one, and `world.debt(...)` is indistinguishable in the AST from a
            # helper that drives a writer. Same object, same single `add`, same flush.
            starting_edge = world.debt("10.00")
            async with factory() as setup:
                async with debt_fixture_setup(setup, label="r4-starting-edge"):
                    setup.add(starting_edge)
                await setup.commit()

        if not armed:
            journal.uninstall_write_guard(engine)
        try:
            trace.attach(engine)
            try:
                async with factory() as session:
                    await _run_with_mutations(
                        scenario,
                        session,
                        world,
                        wrapped=wrapped,
                        marker=marker,
                        amount_shift=amount_shift,
                        drop_the_rollback=drop_the_rollback,
                        move_the_flush=move_the_flush,
                        move_the_flush_on_shape_b=move_the_flush_on_shape_b,
                    )
            finally:
                trace.detach()
        finally:
            if not armed:
                journal.install_write_guard(engine)
        trace.outcome(tuple(marker))
        return trace
    finally:
        await drop_world(factory, world)


async def _run_with_mutations(
    scenario, session, world, *, wrapped, marker, amount_shift, drop_the_rollback, move_the_flush,
    move_the_flush_on_shape_b,
) -> None:
    """`scenario`, with at most one of the three spec mutations in force.

    The mutations are applied by substituting a DIFFERENT scenario body rather than by flags inside
    the bodies above, so the unmutated scenarios stay readable and a mutation cannot leak into a
    comparison it was not asked for.
    """

    if amount_shift:
        debt = world.debt(f"{Decimal('7.00') + amount_shift:f}")
        if wrapped:
            async with debt_fixture_setup(session, label="r4-plain"):
                session.add(debt)
        else:
            session.add(debt)
        await session.commit()
        marker.append("committed")
        return

    if drop_the_rollback:
        debt = world.debt("7.00")
        if wrapped:
            async with debt_fixture_setup(session, label="r4-rollback"):
                session.add(debt)
        else:
            session.add(debt)
        await session.flush()
        # the `await session.rollback()` of `_rollback_instead_of_commit` is GONE
        found = (await session.execute(select(Debt.id).where(Debt.id == debt.id))).first()
        marker.append("the debt survived the rollback" if found else "the debt is gone")
        return

    if move_the_flush:
        # Shape C with the test's own `flush()` moved BEFORE the read instead of after it. Shape B
        # used to be the carrier here and cannot be: see `_pending_write_after_the_block`.
        debt = world.debt("7.00")
        if wrapped:
            async with debt_fixture_setup(session, label="r4-pending"):
                session.add(debt)
        else:
            session.add(debt)
        session.add(_pending_participant())
        await session.flush()
        await session.execute(select(Participant.pid).where(Participant.id == world.debtor.id))
        await session.rollback()
        marker.append("rolled back")
        return

    if move_the_flush_on_shape_b:
        debt = world.debt("7.00")
        if wrapped:
            async with debt_fixture_setup(session, label="r4-other-sql"):
                session.add(debt)
        else:
            session.add(debt)
        await session.flush()  # moved BEFORE the read instead of after it
        await session.execute(select(Participant.pid).where(Participant.id == world.debtor.id))
        await session.commit()
        marker.append("committed")
        return

    await scenario(session, world, wrapped=wrapped, marker=marker)


# =================================================================================================
# R4, zeroth: the instrument compares equal to itself
# =================================================================================================


@pytest.mark.parametrize("label", list(_SCENARIOS), ids=list(_SCENARIOS))
@pytest.mark.asyncio
async def test_r4_two_identical_armed_runs_record_the_same_trace(db_session, label) -> None:
    """THE CONTROL EVERY OTHER CASE IN THIS MODULE DEPENDS ON, and the one round 3 found missing.

    Two runs of the SAME scenario, both wrapped, both armed, nothing mutated. They must compare equal
    verbatim. Before the normalisation in `_scalar_text` they did not: the round-3 reviewer ran this
    comparison directly and got `False`, because `debt_fixture_setup` puts a fresh `uuid4` into the
    operation's identity (`tests/debt_setup.py:144`), the journal writes `opened_at`/`completed_at`,
    and its effect digests are taken over rows whose ids are fresh per run.

    WHY THAT WAS NOT A COSMETIC PROBLEM. Every mutation case below asserts `mutated != baseline`.
    While two identical runs already compared unequal, those assertions passed without the mutation
    having anything to do with it - three checks that could not fail, reported as sensitivity. This
    test is what makes their passing mean something, so it runs for EVERY scenario shape, not one.

    MUTATION: delete the `_UUID_INSIDE_TEXT`, `_TIMESTAMP_TEXT` or `_DIGEST_TEXT` branch of
    `_scalar_text` and the corresponding case here goes red again.
    """

    scenario = _SCENARIOS[label]
    first = await _trace(scenario, wrapped=True, armed=True)
    second = await _trace(scenario, wrapped=True, armed=True)

    # NON-VACUITY, FIRST: the normalisation did not collapse the trace into something that cannot
    # disagree. The journal really wrote, the recorder really saw transaction events, money survived
    # normalisation to the digit, and the identity's non-random part is still in the trace.
    assert any(
        row[0] == "journal-sql"
        or (row[0] == "sql" and (_words_in(row[1]) & DEBT_JOURNAL_TABLE_NAMES))
        for row in first.events
    ), f"stand: the armed run issued no journal statement, so it was not armed: {first.events}"
    assert any(row[0] in _TRANSACTION_EVENTS for row in first.events), first.events
    if label in _SCENARIOS_WHOSE_DEBT_INSERT_IS_REFUSED:
        # The INSERT never completes, so `after_cursor_execute` never fires for it and there is no
        # money in this trace to normalise. What must be there instead is the refusal itself.
        assert ("error", "IntegrityError") in first.events, (
            f"stand: the refused scenario recorded no error, so its negative path was not reached: "
            f"{first.events}"
        )
    else:
        assert _money_in(first), (
            f"stand: normalisation left no money in the trace, so «изменена сумма» could not be seen: "
            f"{first.without_the_journals_own_statements}"
        )
    assert any("r4-" in repr(row) for row in first.verbatim), (
        f"stand: the block's label survived nothing of the normalisation, so the identity is now "
        f"indistinguishable from any other string: {first.verbatim}"
    )

    assert first.verbatim == second.verbatim, (
        "two unmutated armed runs of the same scenario recorded different traces, so every "
        "`mutated != baseline` assertion in this module can pass without the mutation.\n"
        + "\n".join(
            f"first:  {a}\nsecond: {b}"
            for a, b in zip(first.verbatim, second.verbatim)
            if a != b
        )
        + f"\nlengths: {len(first.verbatim)} vs {len(second.verbatim)}"
    )


# =================================================================================================
# R4, first half: wrapping a block changes nothing while the journal is stood down
# =================================================================================================


@pytest.mark.parametrize("label", list(_SCENARIOS), ids=list(_SCENARIOS))
@pytest.mark.asyncio
async def test_r4_the_wrapper_alone_changes_nothing_observable(db_session, label) -> None:
    """R4, the no-op claim, MEASURED. `T1 == T0`, verbatim, with nothing filtered out.

    This is what slice B asserted by comparing ASTs. Here it is asserted by comparing what the two
    forms actually told the database - statements, parameters, multiplicity, transaction events and
    outcome - which is a strictly stronger statement about the same thing and the only one that can
    notice a helper that started doing something.

    MUTATION: make `debt_fixture_setup` read or write anything on the no-op path (`tests/debt_setup.py`
    returns `yield None` before touching the session; a single `await session.flush()` there turns
    every case below red).
    """

    scenario = _SCENARIOS[label]
    unwrapped = await _trace(scenario, wrapped=False, armed=False)
    wrapped = await _trace(scenario, wrapped=True, armed=False)

    # NON-VACUITY, FIRST: the trace really saw the things `before_cursor_execute` cannot see.
    assert any(row[0] in _TRANSACTION_EVENTS for row in unwrapped.events), (
        f"stand: the recorder observed no transaction event at all: {unwrapped.events}"
    )
    assert unwrapped.events[-1][0] == "outcome" and unwrapped.events[-1][1], (
        f"stand: the scenario recorded no outcome, so nothing says it reached its end: "
        f"{unwrapped.events[-1:]}"
    )

    assert wrapped.verbatim == unwrapped.verbatim, (
        "wrapping a setup block in `debt_fixture_setup` changed what the database was told while "
        "the journal was stood down. The helper's no-op path is what made the slice-B migration "
        "behaviour-neutral, and this is the measurement of it.\n"
        f"wrapped:   {wrapped.verbatim}\nunwrapped: {unwrapped.verbatim}"
    )


# =================================================================================================
# R4, second half: what ARMING the journal changes, per scenario shape
# =================================================================================================


@pytest.mark.parametrize(
    "label",
    [
        "a block followed immediately by the test's own commit",
        "a block whose transaction is rolled back and then read back",
    ],
)
@pytest.mark.asyncio
async def test_r4_arming_the_journal_adds_its_own_statements_and_nothing_else(
    db_session, label
) -> None:
    """R4. Where the block is immediately followed by the test's own flush or commit, the only
    difference arming makes is the journal's own rows.

    MUTATION: make the journal write a debt of its own, or issue the completion flush on a different
    transaction - the filtered traces then differ in a statement against `debts`, not only in
    statements against the journal tables.
    """

    scenario = _SCENARIOS[label]
    stood_down = await _trace(scenario, wrapped=True, armed=False)
    armed = await _trace(scenario, wrapped=True, armed=True)

    # NON-VACUITY, FIRST: the journal really was armed for the second run, so the comparison is
    # between two different journal states and not between two identical ones.
    journal_statements = [
        row for row in armed.events
        if row[0] == "sql" and (_words_in(row[1]) & DEBT_JOURNAL_TABLE_NAMES)
    ]
    assert journal_statements, (
        f"stand: the armed run issued no statement against any journal table, so it was not armed: "
        f"{armed.events}"
    )
    assert not [
        row for row in stood_down.events
        if row[0] == "sql" and (_words_in(row[1]) & DEBT_JOURNAL_TABLE_NAMES)
    ], f"stand: the stood-down run wrote journal rows anyway: {stood_down.events}"

    assert (
        armed.without_the_journals_own_statements
        == stood_down.without_the_journals_own_statements
    ), (
        "arming the journal changed more than the journal's own rows for a block whose own flush "
        "follows immediately.\n"
        f"armed:      {armed.without_the_journals_own_statements}\n"
        f"stood down: {stood_down.without_the_journals_own_statements}"
    )


@pytest.mark.asyncio
async def test_r4_arming_the_journal_moves_the_debt_sql_earlier_when_no_flush_follows(
    db_session,
) -> None:
    """R4, the difference that is REAL and is therefore asserted rather than filtered.

    Slice B counted 39 blocks that are not immediately followed by the file's own flush. For those,
    `debt_operation`'s completion flush at block exit sends the debt BEFORE whatever the test does
    next. This is the concrete reason AST equality cannot stand in for behavioural equivalence, and
    it is stated here as an executable expectation: the statement order differs in exactly this way.

    MUTATION, AND THE CLAIM IT CARRIED WAS WRONG UNTIL IT WAS MEASURED (round 3, 2026-09-13). Stop
    flushing at completion (`_complete`'s `await session.flush()`) and this test goes red - that half
    held. The other half - "while the two above stay green" - is FALSE: measured on `c17fa26`, that
    mutation reddens 15 of this module's 23 cases, `test_r4_arming_the_journal_adds_its_own_statements
    _and_nothing_else` included, because without the completion flush the block writes nothing at exit
    and the armed trace stops being the stood-down trace plus journal rows anywhere. The mutation is
    still the right one - it is what would silently re-break the 39 blocks slice B counted - but it is
    not SPECIFIC to this test, and saying it was made this docstring the third false mutation claim
    this programme has shipped. No mutation that reddens only this case has been found; the
    completion flush is the carrier of the whole armed family, and that is recorded rather than
    dressed up as per-test sensitivity.
    """

    scenario = _SCENARIOS["a block with other SQL before the test's own flush"]
    stood_down = await _trace(scenario, wrapped=True, armed=False)
    armed = await _trace(scenario, wrapped=True, armed=True)

    def _order(trace: _Trace) -> list[str]:
        """The two statements this scenario issues, in the order the database received them."""

        names = {"debts": "debt insert", "participants": "participant select"}
        order: list[str] = []
        for row in trace.without_the_journals_own_statements:
            if row[0] != "sql":
                continue
            for table, name in names.items():
                if table in _words_in(row[1]):
                    order.append(name)
        return order

    # NON-VACUITY: both runs really issued both statements.
    assert sorted(_order(stood_down)) == ["debt insert", "participant select"], _order(stood_down)
    assert sorted(_order(armed)) == ["debt insert", "participant select"], _order(armed)

    assert _order(stood_down) == ["participant select", "debt insert"], (
        f"stand: with the journal stood down the debt is flushed by the test's own `flush()`, after "
        f"the read; observed {_order(stood_down)}"
    )
    assert _order(armed) == ["debt insert", "participant select"], (
        f"arming the journal did NOT move the debt SQL to the block's exit ({_order(armed)}). If "
        f"that is a deliberate change, the 39 blocks slice B counted have to be re-measured and this "
        f"expectation rewritten - it is the one place this programme records that activation is not "
        f"statement-for-statement neutral."
    )
    assert armed.without_the_journals_own_statements != (
        stood_down.without_the_journals_own_statements
    ), "the two traces are equal, so this scenario is not the shape it is named after"


@pytest.mark.asyncio
async def test_r4_arming_the_journal_moves_where_a_refused_write_raises(db_session) -> None:
    """R4's "явные маркеры достижения негативных путей", on the case that actually moved.

    The negative path is reached in both runs - the marker says so, by name - but NOT in the same
    place: stood down the `IntegrityError` arrives at the test's own `flush()`, armed it arrives at
    the block's exit. Six migrated blocks had a `pytest.raises` or a `try` immediately after the
    block, which is why slice B named this case; this is the executable statement of it.

    MUTATION: remove the completion flush, and the armed run's marker becomes the stood-down one.
    """

    scenario = _SCENARIOS["a block whose write violates a unique constraint"]
    stood_down = await _trace(scenario, wrapped=True, armed=False)
    armed = await _trace(scenario, wrapped=True, armed=True)

    # NON-VACUITY, FIRST: the database really refused, in both runs. Without this the markers could
    # agree because nothing raised at all.
    assert ("error", "IntegrityError") in stood_down.events, stood_down.events
    assert ("error", "IntegrityError") in armed.events, armed.events

    assert stood_down.events[-1] == ("outcome", ("own flush raised IntegrityError",)), (
        f"stand: with the journal stood down the refusal did not arrive at the test's own flush: "
        f"{stood_down.events[-1]}"
    )
    assert armed.events[-1] == ("outcome", ("block exit raised IntegrityError",)), (
        f"arming the journal did not move the refusal to the block's exit: {armed.events[-1]}. The "
        f"six blocks that expect their own flush to raise depend on which of these two is true."
    )


# =================================================================================================
# R4's three named mutations: the comparison above has to be sensitive to each of them
# =================================================================================================
#
# ROUND 3 MEASURED THIS SECTION AND FOUND IT FALSE IN BOTH DIRECTIONS, so what it claims now is
# narrower and each half is measured rather than argued. What was wrong:
#
# * two UNCHANGED armed runs compared UNEQUAL (`verbatim` differed), because the fixture identity
#   carries a `uuid4` and the journal's timestamps and effect digests are per-run. A "the mutated
#   trace differs" assertion over a comparison that already differs cannot fail. Fixed by
#   `_scalar_text`, and held in place by `test_r4_two_identical_armed_runs_record_the_same_trace`.
# * "убран rollback" produced an IDENTICAL trace on the old shape-A scenario, armed and stood down,
#   because the scenario ended and `Session.close()` issued the rollback anyway. Fixed by giving the
#   rollback a consequence to have - see `_rollback_instead_of_commit`.
# * "перенесён flush" produced an identical trace ARMED on shape B, because the completion flush had
#   already drained the unit of work and the moved `flush()` sent nothing. The mutation now runs on
#   shape C, where the test's own flush has work of its own; shape B's insensitivity is itself
#   recorded below, as the measured cost of activation rather than as a check that cannot fail.
#
# Every case below compares the trace WITHOUT the journal's own statements, so the journal's noise
# cannot be what supplies the difference, and every case runs in BOTH journal states.


_MUTATIONS = {
    "убран rollback": {
        "scenario": "a block whose transaction is rolled back and then read back",
        "kwargs": {"drop_the_rollback": True},
        "must_change": ("transaction events", "outcome"),
    },
    "перенесён flush": {
        "scenario": "a block with a pending non-debt write after it",
        "kwargs": {"move_the_flush": True},
        "must_change": ("statement order",),
    },
    "изменена сумма": {
        "scenario": "a block followed immediately by the test's own commit",
        "kwargs": {"amount_shift": Decimal("1.00")},
        "must_change": ("bound money",),
    },
}


def _transaction_events(trace: _Trace) -> list[tuple]:
    return [row for row in trace.events if row[0] in _TRANSACTION_EVENTS]


def _statement_order(trace: _Trace) -> list[str]:
    return [row[1] for row in trace.without_the_journals_own_statements if row[0] == "sql"]


def _money_in(trace: _Trace) -> list[str]:
    """Every money value the filtered trace bound, in order."""

    found: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, tuple):
            if len(node) == 2 and node[0] == "money":
                found.append(node[1])
                return
            for item in node:
                _walk(item)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(tuple(trace.without_the_journals_own_statements))
    return found


def _with_money_blanked(trace: _Trace) -> list:
    """The filtered trace with every money VALUE replaced by a placeholder, shape kept."""

    def _walk(node):
        if isinstance(node, tuple):
            if len(node) == 2 and node[0] == "money":
                return ("money",)
            return tuple(_walk(item) for item in node)
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return [_walk(row) for row in trace.without_the_journals_own_statements]


def _what_changed(baseline: _Trace, mutated: _Trace) -> set[str]:
    """Which of the aspects a mutation could move actually moved. Measured, then asserted."""

    changed = set()
    if _transaction_events(baseline) != _transaction_events(mutated):
        changed.add("transaction events")
    if baseline.events[-1] != mutated.events[-1]:
        changed.add("outcome")
    if _statement_order(baseline) != _statement_order(mutated):
        changed.add("statement order")
    if _money_in(baseline) != _money_in(mutated):
        changed.add("bound money")
    return changed


@pytest.mark.parametrize("armed", [False, True], ids=["stood-down", "armed"])
@pytest.mark.parametrize("mutation", list(_MUTATIONS), ids=["rollback", "flush", "amount"])
@pytest.mark.asyncio
async def test_r4_the_trace_comparison_notices_each_named_mutation(
    db_session, mutation, armed
) -> None:
    """R4: "мутации «убран rollback», «перенесён flush», «изменена сумма» обязаны делать гейт красным".

    A comparison that cannot fail is worth nothing, and the only way to know this one can is to run
    it against a body that really differs by exactly one of those three things. Each case compares
    the unmutated trace with the mutated one and requires them to disagree IN THE NAMED WAY - not
    merely to disagree, which round 3 showed is satisfiable by per-run noise alone.

    THIS IS WHY THE RECORDER RECORDS WHAT IT RECORDS: "убран rollback" is invisible to
    `before_cursor_execute` (a DBAPI rollback issues no statement) and is caught here by the
    transaction-event sequence and by the scenario's own outcome; "перенесён flush" is invisible to
    any check that compares SETS of statements and is caught by their order; "изменена сумма" is
    invisible to any check that drops parameters and is caught by the bound money.

    WHAT EACH CASE PROVES, exactly: that THIS instrument, in THIS journal state, distinguishes the
    unmutated scenario from the named mutation of it. It does not prove that the 110 migrated blocks
    contain that mutation's shape, and it says nothing about the journal's own correctness.
    """

    case = _MUTATIONS[mutation]
    scenario = _SCENARIOS[case["scenario"]]
    baseline = await _trace(scenario, wrapped=True, armed=armed)
    mutated = await _trace(scenario, wrapped=True, armed=armed, **case["kwargs"])

    # NON-VACUITY, FIRST: both runs really ran, reached their end and recorded an outcome; and the
    # recorder really saw the transaction events `before_cursor_execute` cannot see.
    for name, trace in (("baseline", baseline), ("mutated", mutated)):
        assert trace.events, f"stand: the {name} run recorded nothing at all"
        assert trace.events[-1][0] == "outcome" and trace.events[-1][1], (
            f"stand: the {name} run recorded no outcome, so nothing says it reached its end: "
            f"{trace.events[-1:]}"
        )
        assert _transaction_events(trace), (
            f"stand: the {name} run recorded no transaction event: {trace.events}"
        )

    changed = _what_changed(baseline, mutated)
    assert set(case["must_change"]) <= changed, (
        f"the mutation `{mutation}` did not change {set(case['must_change']) - changed} with the "
        f"journal {'armed' if armed else 'stood down'}, so this instrument cannot see that class of "
        f"change and the equality assertions above are vacuous for it.\n"
        f"baseline: {baseline.without_the_journals_own_statements}\n"
        f"mutated:  {mutated.without_the_journals_own_statements}"
    )
    assert (
        mutated.without_the_journals_own_statements
        != baseline.without_the_journals_own_statements
    ), (
        f"the mutation `{mutation}` left the filtered trace identical, so whatever the check above "
        f"saw was not a difference in what the database was told."
    )


@pytest.mark.parametrize("armed", [False, True], ids=["stood-down", "armed"])
@pytest.mark.asyncio
async def test_r4_changing_the_amount_changes_the_money_and_nothing_else(db_session, armed) -> None:
    """"изменена сумма", stated exactly: the ONLY thing that moved is the bound money.

    The looser "the traces differ" is the form round 3 found unfalsifiable. This is the precise one:
    the filtered trace with money values blanked is IDENTICAL, and the money values themselves
    differ. A recorder that stopped binding parameters fails the second half; one that started
    folding the amount into something else (statement text, an ordinal, a digest) fails the first.
    """

    scenario = _SCENARIOS["a block followed immediately by the test's own commit"]
    baseline = await _trace(scenario, wrapped=True, armed=armed)
    mutated = await _trace(scenario, wrapped=True, armed=armed, amount_shift=Decimal("1.00"))

    # NON-VACUITY, FIRST: there IS money in the filtered trace to compare.
    assert _money_in(baseline), (
        f"stand: the filtered trace bound no money at all, so nothing here can see an amount "
        f"change: {baseline.without_the_journals_own_statements}"
    )

    assert _with_money_blanked(baseline) == _with_money_blanked(mutated), (
        "shifting the amount changed something other than the money.\n"
        f"baseline: {_with_money_blanked(baseline)}\nmutated:  {_with_money_blanked(mutated)}"
    )
    assert _money_in(baseline) != _money_in(mutated), (
        f"shifting the amount by 1.00 did not change any bound money value: "
        f"{_money_in(baseline)} == {_money_in(mutated)}"
    )


@pytest.mark.asyncio
async def test_r4_the_completion_flush_makes_the_tests_own_flush_a_no_op_for_shape_b(
    db_session,
) -> None:
    """THE LIMIT OF THE FLUSH MUTATION, measured, because round 3 found it claimed and false.

    On shape B - a block, other SQL, then the test's own `flush()` with nothing else pending - moving
    that `flush()` before the read is observable while the journal is STOOD DOWN and is NOT observable
    while it is ARMED. The reason is the one this module already asserts elsewhere: the completion
    flush at the block's exit has already sent the debt, so the test's own `flush()` has nothing left
    to send, and moving a call that sends nothing moves nothing.

    Recorded as an assertion in BOTH directions rather than as a comment, because both directions can
    break and each break means something different:

    * the stood-down half going equal would mean the recorder stopped seeing statement order;
    * the armed half going unequal would mean the completion flush stopped draining the session -
      exactly the change that would silently re-break the 39 blocks slice B counted.

    The mutation itself is carried by shape C (`_pending_write_after_the_block`), where the test's own
    flush has work of its own; see `test_r4_the_trace_comparison_notices_each_named_mutation`.
    """

    scenario = _SCENARIOS["a block with other SQL before the test's own flush"]

    stood_down_base = await _trace(scenario, wrapped=True, armed=False)
    stood_down_moved = await _trace(
        scenario, wrapped=True, armed=False, move_the_flush_on_shape_b=True
    )
    armed_base = await _trace(scenario, wrapped=True, armed=True)
    armed_moved = await _trace(
        scenario, wrapped=True, armed=True, move_the_flush_on_shape_b=True
    )

    # NON-VACUITY, FIRST: all four runs issued both of the scenario's own statements, so a comparison
    # that comes out equal did so with something to compare.
    for name, trace in (
        ("stood down, unmutated", stood_down_base),
        ("stood down, flush moved", stood_down_moved),
        ("armed, unmutated", armed_base),
        ("armed, flush moved", armed_moved),
    ):
        tables = {table for row in _statement_order(trace) for table in _words_in(row)}
        assert {"debts", "participants"} <= tables, (
            f"stand: the run `{name}` did not issue both of the scenario's statements: "
            f"{_statement_order(trace)}"
        )

    assert _statement_order(stood_down_base) != _statement_order(stood_down_moved), (
        "with the journal stood down, moving the test's own `flush()` before the read did not change "
        "the order the database received the statements in - which it must, because that `flush()` "
        "is what sends the debt.\n"
        f"unmutated: {_statement_order(stood_down_base)}\n"
        f"moved:     {_statement_order(stood_down_moved)}"
    )
    assert (
        armed_base.without_the_journals_own_statements
        == armed_moved.without_the_journals_own_statements
    ), (
        "with the journal ARMED, moving the test's own `flush()` on shape B changed the trace. That "
        "would mean the completion flush no longer drains the unit of work at the block's exit, so "
        "the 39 blocks slice B counted have to be re-measured and this expectation rewritten - it is "
        "the recorded cost of activation, not a passing detail.\n"
        f"unmutated: {armed_base.without_the_journals_own_statements}\n"
        f"moved:     {armed_moved.without_the_journals_own_statements}"
    )
