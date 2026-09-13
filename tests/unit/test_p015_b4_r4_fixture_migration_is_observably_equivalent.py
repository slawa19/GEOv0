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

WHAT REMAINS UNPROVEN, stated plainly because R4's scope is wider than this module's. This compares
four scenarios built to be the shapes the migration produces. It does not re-run all 110 migrated
blocks under both journal states; the evidence that those blocks' STATEMENTS are unchanged is slice
B's AST comparison (R3), and the evidence that their OUTCOMES are unchanged is that both canonical
gates return their baseline counts. Neither of those is a per-block behavioural trace, and this module
does not claim to be one. What it does close is the specific gap the external review named: there is
now a check that observes transaction outcomes and exceptions, that is sensitive to the three
mutations the spec names, and that fails when the flush moves.

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

from app.db.journal_tables import DEBT_JOURNAL_TABLE_NAMES
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
            self.events.append(("sql", _normalised(statement), _parameters(parameters)))

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
        """The trace with every statement against a journal table removed, ids renumbered.

        THE RENUMBERING HAPPENS AFTER THE FILTER, and that order is load-bearing: the journal's own
        rows carry ids of their own, so numbering before the filter would give the two runs different
        ordinals for the same debt and every comparison below would fail for a reason that is not a
        behaviour change.
        """

        kept = [
            row
            for row in self.events
            if row[0] != "sql" or not (_words_in(row[1]) & DEBT_JOURNAL_TABLE_NAMES)
        ]
        return _renumbered(kept)

    @property
    def verbatim(self) -> list[tuple]:
        return _renumbered(self.events)


_UUID_TEXT = re.compile(r"\A[0-9a-fA-F]{32}\Z|\A[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


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
    """

    if isinstance(value, uuid.UUID):
        return ("id", value.hex)
    if isinstance(value, str) and _UUID_TEXT.match(value):
        return ("id", uuid.UUID(value).hex)
    if isinstance(value, datetime):
        return ("ts",)
    if isinstance(value, Decimal):
        return ("money", f"{value:f}")
    if isinstance(value, float):
        return ("money", f"{Decimal(str(value)):f}")
    return value


def _renumbered(events: list[tuple]) -> list[tuple]:
    """Replace every `("id", hex)` marker with `("id", ordinal of first appearance)`."""

    seen: dict[str, int] = {}

    def _walk(node):
        if isinstance(node, tuple):
            if len(node) == 2 and node[0] == "id" and isinstance(node[1], str):
                return ("id", seen.setdefault(node[1], len(seen)))
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
    """Shape A with a rollback: the mutation "убран rollback" is this scenario's own line."""

    debt = world.debt("7.00")
    if wrapped:
        async with debt_fixture_setup(session, label="r4-rollback"):
            session.add(debt)
    else:
        session.add(debt)
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
    "a block whose transaction is rolled back": _rollback_instead_of_commit,
    "a block whose write violates a unique constraint": _duplicate_edge_raises,
}

#: Scenarios that need the edge to exist before they run.
_NEEDS_A_STARTING_EDGE = {_duplicate_edge_raises}


# =================================================================================================
# Running one trace
# =================================================================================================


async def _trace(scenario, *, wrapped: bool, armed: bool, amount_shift: Decimal = Decimal(0),
                 drop_the_rollback: bool = False, move_the_flush: bool = False) -> _Trace:
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
    scenario, session, world, *, wrapped, marker, amount_shift, drop_the_rollback, move_the_flush
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
        marker.append("rolled back")
        return

    if move_the_flush:
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
        "a block whose transaction is rolled back",
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

    MUTATION: stop flushing at completion (`_complete`'s `await session.flush()`), and this test goes
    red while the two above stay green - which is also what would silently re-break the 39 blocks.
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


_MUTATIONS = {
    "убран rollback": {"drop_the_rollback": True},
    "перенесён flush": {"move_the_flush": True},
    "изменена сумма": {
        "amount_shift": Decimal("1.00")
    },
}

_MUTATION_BASELINE = {
    "убран rollback": "a block whose transaction is rolled back",
    "перенесён flush": (
        "a block with other SQL before the test's own flush"
    ),
    "изменена сумма": (
        "a block followed immediately by the test's own commit"
    ),
}


@pytest.mark.parametrize("mutation", list(_MUTATIONS), ids=["rollback", "flush", "amount"])
@pytest.mark.asyncio
async def test_r4_the_trace_comparison_notices_each_named_mutation(db_session, mutation) -> None:
    """R4: "мутации «убран rollback», «перенесён flush», «изменена сумма» обязаны делать гейт красным".

    A comparison that cannot fail is worth nothing, and the only way to know this one can is to run
    it against a body that really differs by exactly one of those three things. Each case below
    compares the unmutated trace with the mutated one and requires them to DISAGREE - so if the
    recorder ever stopped observing transaction events, statement order or parameters, the
    corresponding case here goes red and the equality assertions above stop being believed.

    THIS IS WHY THE RECORDER RECORDS WHAT IT RECORDS: "убран rollback" is invisible to
    `before_cursor_execute` (a DBAPI rollback issues no statement), "перенесён flush" is invisible to
    any check that compares SETS of statements, and "изменена сумма" is invisible to any check that
    drops parameters.
    """

    scenario = _SCENARIOS[_MUTATION_BASELINE[mutation]]
    baseline = await _trace(scenario, wrapped=True, armed=True)
    mutated = await _trace(scenario, wrapped=True, armed=True, **_MUTATIONS[mutation])

    # NON-VACUITY, FIRST: both runs really ran and really recorded something.
    assert baseline.events and mutated.events, (baseline.events, mutated.events)

    assert mutated.verbatim != baseline.verbatim, (
        f"the mutation `{mutation}` produced an identical trace, so this comparison cannot see it "
        f"and every equality assertion in this module is vacuous for that class of change.\n"
        f"baseline: {baseline.verbatim}\nmutated:  {mutated.verbatim}"
    )
