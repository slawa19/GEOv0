"""Programme 015, `B4` step 2: the debt journal's transaction contract, as counterexamples.

WHAT THIS MODULE IS. The spec's acceptance of the step-4 design (2026-09-11, after two external
review rounds) made every remaining requirement "обязательной приёмкой шага 4: каждое пишется
красным контрпримером до кода". This module holds the transaction-contract half of that acceptance:
design v2 §9 counterexamples C1, C7, C9, C10, C11, and binding conditions 1 and 2. The write guard
half is `tests/unit/test_p015_b4_write_guard.py`; the PostgreSQL tier is
`tests/integration/test_p015_b4_transaction_contract_postgres.py`.

There is no journal yet, and there must not be one when these are written. `tests/p015_b4_support.py`
explains how a counterexample stays red for the property it names rather than for a missing import.

TWO KINDS OF RED, and the difference matters when reading a failure:

* DEFECT-SHAPED - the scenario runs today to its end and the database keeps what the journal would
  have refused. The failure quotes real stored money. C1, C9, C10, condition 1.
* API-SHAPED - the property has no carrier at all: there is no envelope table to look in, no
  registry to ask. These fail on their non-vacuity assertion, which says so in one sentence. C7,
  C11's machinery half, condition 2's retention check.

Both are red on today's tree; only the first also proves a live bypass. Each test names, in its
docstring, the MUTATION that must make it red again once step 4 exists - a green counterexample
whose mutation was never run is not evidence (`AGENTS.md` §9, anti-vacuum).

TIER. SQLite, the default tier. Since T1525 this tier takes real snapshots, so a transaction that
reads and then writes can be refused with SQLITE_BUSY_SNAPSHOT; every session under test therefore
begins with its write, and the world is seeded and committed before it opens. Money stays inside
`|v| < 2^26`, the domain where scale-8 values round-trip exactly through the driver's float binding
(design v2 §4). Every verdict is read back on a NEW session.

THE `db_session` FIXTURE IS REQUESTED FOR WHAT IT DOES BEFORE THE TEST, not for its session: on
SQLite it initialises the schema and truncates every table. Its session is never used, so no fixture
transaction wraps the work under test and no commit boundary is hidden.

MARKER, HISTORICAL. This module carried `b4_counterexample` and was deselected from the canonical
gate while the debt journal did not exist. Step 4 slice C built it and REMOVED THE MARKER, not the
assertions: every test below still asserts exactly what it asserted while it was red, and each one
names in its docstring the mutation that must turn it red again.
"""

from __future__ import annotations

import asyncio
import gc
import uuid
import weakref
from contextlib import AsyncExitStack
from decimal import Decimal

import asyncpg
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.debt import Debt
from tests.debt_setup import debt_fixture_setup
from tests.p015_b4_support import (
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
)


class _BusinessFailure(RuntimeError):
    """A failure from the BLOCK of an operation - a rejected payment, not a journal malfunction."""


def _identity(name: str) -> str:
    return f"p015-b4/{name}/{uuid.uuid4().hex[:12]}"


def _intent(**fields) -> dict:
    """The smallest intent an operation can carry. Shape, not semantics - §7 owns the real ones."""
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


async def _raw_driver_connection(session):
    """A handle on the DBAPI connection, taken while the transaction is still open.

    Taken EARLY and read LATE on purpose: asking the session for its connection after a refusal
    would autobegin a fresh transaction, and `in_transaction` would then answer about that new one.
    """
    connection = await session.connection()
    return (await connection.get_raw_connection()).driver_connection


def _driver_transaction_state(driver) -> str:
    """`open`, `none` or `closed`, asked of the DRIVER connection in its own words.

    `closed` is reported as its own answer rather than folded into `none`. A connection that has
    been handed back to the pool and closed cannot hold a transaction open, but it also cannot show
    that the refusal itself ended the transaction - the pool's reset-on-return would have done it
    either way, and accepting that would be exactly the "compensation further downstream" this
    repository forbids (`AGENTS.md` §9). A test that needs the distinction must therefore keep the
    connection checked out itself.

    TWO DRIVERS, ONE QUESTION (programme 017, `T1702`, 2026-09-23). This read only
    `sqlite3.Connection.in_transaction`, so on a PostgreSQL tier it raised `AttributeError` before
    the scenario could say anything. asyncpg answers the same question with
    `Connection.is_in_transaction()`, which reports the transaction status the SERVER sent with its
    last ReadyForQuery - the database's own state, as `in_transaction` is sqlite3's - and
    `is_closed()` for the closed case. The three answers and what each one means are unchanged.
    """
    # The sqlite3 arm (`in_transaction`) left with SQLite (017 stage 3, S7); any other driver is a
    # stand this module does not know how to read, and is refused rather than guessed.
    if not isinstance(driver, asyncpg.Connection):
        raise TypeError(f"no transaction-state reading for driver {type(driver).__name__}")
    if driver.is_closed():
        return "closed"
    return "open" if driver.is_in_transaction() else "none"



async def _envelope_states_in_transaction(connection, identity: str) -> list[str]:
    """The envelope states for one identity, read ON the connection that is inside the transaction.

    A read on a NEW session cannot see an uncommitted envelope at all, so for a scenario whose
    verdict is "the commit was REFUSED" the fresh-session read answers the empty list whether the
    scenario ran or not. This is the only read in this module that deliberately goes through the
    connection under test, and it is used for NON-VACUITY only - never for a verdict, which is
    always taken from `stored_debts` on a new session.
    """

    rows = (
        await connection.execute(
            text(f"SELECT state FROM {OPERATIONS_TABLE} WHERE identity = :identity"),  # noqa: S608
            {"identity": identity},
        )
    ).all()
    return [row[0] for row in rows]


async def _debt_amounts_in_transaction(connection, world: World) -> list[Decimal]:
    """This world's debt amounts as the open transaction sees them. Non-vacuity only; see above."""

    rows = (
        await connection.execute(
            text("SELECT amount FROM debts WHERE equivalent_id = :equivalent_id"),
            {"equivalent_id": _uuid_as_stored(connection, world.equivalent.id)},
        )
    ).all()
    return sorted(Decimal(str(row[0])) for row in rows)


def _uuid_as_stored(connection, value: uuid.UUID):
    """`value` spelled the way PostgreSQL stores a `Uuid(as_uuid=True)` column: a native `uuid`.

    Until 017 stage 3 this also had a SQLite arm (32 hex WITHOUT dashes), the trap this programme
    was bitten by twice: a `text()` comparison with the wrong spelling matches nothing, and a
    non-vacuity assertion that matches nothing is always satisfiable by doing nothing. Same rule as
    `tests/debt_setup.py::_uuid_literals`.
    """

    return value


# ==============================================================================================
# C1 - a Debt write with no operation open
# ==============================================================================================


@pytest.mark.parametrize("effect", ["insert", "update", "delete"])
@pytest.mark.asyncio
async def test_c1_a_debt_written_with_no_operation_is_refused(db_session, effect) -> None:
    """C1, DEFECT-SHAPED. An ORM Debt I/U/D outside any debt operation must be refused.

    RED TODAY BECAUSE: nothing looks at flushes, so the write reaches the database and the commit
    stores it. The failure below quotes the stored amount.
    MUTATION once step 4 exists: remove the `before_flush` hook, or let it return without refusing
    when `conn.get_transaction()` has no OPEN operation of this session.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    try:
        if effect in ("update", "delete"):
            starting_edge = world.debt("10.00")
            async with factory() as setup:
                # Built before the block: `fixture_block_violations` allows only constructors and session
                # calls inside one, and `world.debt(...)` is indistinguishable in the AST from a helper that
                # drives a writer. Same object, same single `add`, same flush.
                async with debt_fixture_setup(setup, label="starting-edge"):
                    setup.add(starting_edge)
                await setup.commit()

        async with factory() as session:
            if effect == "insert":
                session.add(world.debt("13.00"))
            else:
                debt = (
                    await session.execute(
                        select(Debt).where(Debt.equivalent_id == world.equivalent.id)
                    )
                ).scalar_one()
                if effect == "update":
                    debt.amount = exact_money("17.00")
                else:
                    await session.delete(debt)
            refusal = await refusal_of(api, session.flush())
            if refusal is None:
                await session.commit()

        after = await stored_debts(factory, world)
        expected_amount = {"insert": Decimal("13.00000000"), "update": Decimal("17.00000000")}

        # NON-VACUITY, AND IT IS NOW A CONTROL. It used to read "the write really reached the
        # database", which was the DEFECT and therefore could not survive the fix: once the journal
        # refuses the bare write, nothing reaches the table and the old sentence became
        # unsatisfiable. What it was FOR survives unchanged - excluding "the write failed for some
        # unrelated reason (a rejected foreign key, an autoflush that never ran)" - and is proven
        # the other way round: THE SAME EFFECT, INSIDE A DECLARED OPERATION, lands exactly as
        # written. That is also the anti-vacuum `AGENTS.md` §9 asks of any rule that refuses
        # something: a rule with no passing side would stop payments happening at all.
        control = await seed_world(factory)
        try:
            if effect in ("update", "delete"):
                control_edge = control.debt("10.00")
                async with factory() as setup:
                    async with debt_fixture_setup(setup, label="control-start"):
                        setup.add(control_edge)
                    await setup.commit()
            async with factory() as session:
                async with await _open(api, session, control, f"control-{effect}"):
                    if effect == "insert":
                        session.add(control.debt("13.00"))
                    else:
                        row = (
                            await session.execute(
                                select(Debt).where(Debt.equivalent_id == control.equivalent.id)
                            )
                        ).scalar_one()
                        if effect == "update":
                            row.amount = exact_money("17.00")
                        else:
                            await session.delete(row)
                    await session.flush()
                await session.commit()
            controlled = await stored_debts(factory, control)
        finally:
            await drop_world(factory, control)

        if effect == "delete":
            assert controlled == {}, (
                f"stand: the DELETE does not reach the database even inside a declared operation "
                f"({controlled}), so the refusal below cannot be attributed to the journal"
            )
        else:
            assert controlled == {("debtor", "creditor", "eq"): expected_amount[effect]}, (
                f"stand: the {effect.upper()} does not reach the database as written even inside a "
                f"declared operation ({controlled}), so the refusal below cannot be attributed to "
                f"the journal"
            )

        # VERDICT.
        assert refusal is not None, (
            f"a Debt {effect.upper()} was flushed and committed with no debt operation open and "
            f"nothing refused it; the database now holds {after or 'no debt where one stood'}. "
            f"Every change to `debts` must be covered by an operation of {JOURNAL_MODULE}."
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c1_a_swallowed_refusal_does_not_let_the_commit_through(db_session) -> None:
    """C1, DEFECT-SHAPED. A caller that swallows the refusal must still not get a commit.

    This is the half the round-2 reviewer singled out (`review-round2-codex.md` §3, last paragraph):
    a refusal raised before any SQL does not have to roll the database back on the spot, but the
    root must be poisoned, so that WRITES THAT ALREADY SUCCEEDED in this transaction do not become
    durable when the caller catches the refusal and commits anyway.

    RED TODAY BECAUSE: there is no refusal to swallow and no poison, so the commit stores both the
    earlier write and the refused one.
    MUTATION once step 4 exists: drop the root poison on a hook refusal (refuse the flush but leave
    the registry clean), or clear the poison anywhere other than a real database rollback.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    try:
        earlier = exact_money("21.00")
        async with factory() as session:
            # An earlier write that legitimately succeeds. It is a Debt of the same transaction; the
            # operation that covers it is opened by `_open` when there is one to open.
            async with await _open(api, session, world, "earlier"):
                session.add(world.debt(str(earlier)))
                await session.flush()

            # NON-VACUITY, READ INSIDE THE TRANSACTION, and it has to be read here. It used to be
            # `assert after` - "the earlier write really was committed" - which was the defect and
            # is unsatisfiable once the poison holds: the whole point below is that NOTHING becomes
            # durable, the earlier write included. What the assertion was for - "this transaction
            # really wrote something, so the emptiness afterwards is the poison and not an empty
            # unit of work" - is exactly what this reads, on the session that did the writing,
            # while its transaction is still open.
            written_inside = (
                await session.execute(
                    select(Debt.amount).where(Debt.equivalent_id == world.equivalent.id)
                )
            ).scalars().all()

            # Now an uninstrumented Debt write, and a caller that swallows whatever comes back.
            session.add(
                world.debt("34.00", creditor_id=world.extra_participants[0].id)
            )
            refusal = await refusal_of(api, session.flush())
            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        assert [Decimal(str(value)) for value in written_inside] == [earlier], (
            f"stand: the earlier, covered write never reached the database inside the transaction "
            f"({written_inside}), so this proves nothing about a swallowed refusal"
        )

        # VERDICT.
        assert (refusal, commit_refusal) != (None, None), (
            f"an uninstrumented Debt was flushed, the refusal was swallowed and the commit went "
            f"through: the database holds {after}. A refusal must poison the root until a real "
            f"database rollback, so neither this write nor the earlier one can become durable."
        )
        assert after == {}, f"the poisoned root committed anyway: {after}"
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# Condition 1 - a rollback event is not a report that the rollback succeeded
# ==============================================================================================


@pytest.mark.asyncio
async def test_condition1_a_failed_savepoint_rollback_keeps_the_refusal(db_session) -> None:
    """Binding condition 1, DEFECT-SHAPED. The reviewer's own probe, rebuilt on real debts.

    THE BYPASS (`review-round2-codex.md` §2, `FAILED_ROLLBACK_DURABLE_WITHOUT_COMPLETED [(42,)]`).
    SQLAlchemy dispatches `rollback_savepoint` BEFORE `do_rollback_to_savepoint`
    (`sqlalchemy/engine/base.py:1150`) and deactivates the savepoint in a `finally` (`:2819`). The
    design clears the operation registry from that event (design v2 §1.3), which is therefore not a
    report that anything was rolled back: if the rollback itself fails - another listener raising,
    a cancellation, a driver error - the savepoint's rows are still in the transaction, the registry
    is empty, and the root commit stores them.

    Reproduced here on today's tree with `install_sqlite_transaction_control` in effect: the
    listener raised, `sqlite3.Connection.in_transaction` stayed True, `session.commit()` returned
    normally, and the savepoint's debt is on disk.

    REQUIREMENT: a failed or cancelled rollback must keep the refusal or invalidate the connection.
    Either way this debt must not be durable.
    MUTATION once step 4 exists: clear the registry from the `rollback_savepoint` / `rollback`
    events without confirming that the SQL rollback completed.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    armed = {"on": False}
    rollback_failure_message = "p015-b4: the savepoint rollback failed before any SQL was sent"

    def _fail_the_savepoint_rollback(_conn, _name, _context) -> None:
        if armed["on"]:
            raise RuntimeError(rollback_failure_message)

    event.listen(engine.sync_engine, "rollback_savepoint", _fail_the_savepoint_rollback)
    try:
        in_savepoint = exact_money("42.00")
        rollback_error: BaseException | None = None
        seen_inside: Decimal | None = None

        async with factory() as session:
            # The root begins with its own write, so the savepoint below is never the transaction
            # (T1525) and this test measures the rollback, not SQLite's legacy BEGIN.
            async with await _open(api, session, world, "root-write"):
                session.add(world.debt("8.00"))
                await session.flush()

            nested = await session.begin_nested()
            async with await _open(api, session, world, "savepoint-bound"):
                session.add(
                    world.debt(str(in_savepoint), creditor_id=world.extra_participants[0].id)
                )
                await session.flush()
            # NON-VACUITY, read through this transaction's own connection: the savepoint's INSERT
            # really reached the database. Uncommitted and deliberately so - it is the row whose
            # fate the rollback decides.
            seen_inside = await session.scalar(
                select(Debt.amount).where(
                    Debt.creditor_id == world.extra_participants[0].id
                )
            )

            armed["on"] = True
            try:
                await nested.rollback()
            except RuntimeError as exc:
                rollback_error = exc
            finally:
                armed["on"] = False

            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY.
        assert rollback_error is not None and rollback_failure_message in str(rollback_error), (
            f"stand: the savepoint rollback did not fail, so nothing here is about a failed "
            f"rollback: {rollback_error!r}"
        )
        assert seen_inside == in_savepoint, (
            f"stand: the savepoint's debt never reached the database ({seen_inside}), so its "
            f"survival would prove nothing"
        )

        # VERDICT.
        assert after.get(("debtor", "extra0", "eq")) is None, (
            f"the savepoint rollback FAILED and the root commit stored the savepoint's debt "
            f"anyway: {after}. The rollback event fires before the SQL rollback, so clearing the "
            f"registry on it treats a failure as a success. A failed rollback must keep the "
            f"refusal (commit refusal seen: {commit_refusal!r})."
        )
    finally:
        event.remove(engine.sync_engine, "rollback_savepoint", _fail_the_savepoint_rollback)
        await drop_world(factory, world)


# ==============================================================================================
# Condition 2 - lifecycle: no retention of finished roots, AUTOCOMMIT, two-phase
# ==============================================================================================


@pytest.mark.asyncio
async def test_condition2_a_finished_root_is_not_retained_by_the_registry(db_session) -> None:
    """Binding condition 2, API-SHAPED. A committed root must become collectable.

    THE DEFECT THE REVIEWER MEASURED (`review-round2-codex.md` §2): the registry is keyed by the
    Core `RootTransaction` in a `WeakKeyDictionary`, but the VALUE holds `op.root`, a strong
    reference back to the key. A successful commit does not clear the registry, so after commit and
    a full collection the reviewer still saw `root_alive True entries 1` - unbounded retention of
    finished transactions, and with them their connections' transaction objects.

    RED TODAY BECAUSE: no operation can be registered in any root, so the non-vacuity assertion
    below - "this root really carried an operation" - has nothing to stand on. It is checked FIRST
    precisely so the weak reference test cannot pass by measuring a root that no registry ever saw.
    MUTATION once step 4 exists: store `op.root` as a strong attribute on the registry value (which
    is what design v2 §1.3 describes), or keep entries for roots that have committed.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("retention")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "retention", identity=identity):
                session.add(world.debt("5.00"))
                await session.flush()
            core_connection = (await session.connection()).sync_connection
            root_ref = weakref.ref(core_connection.get_transaction())
            await session.commit()
        del core_connection, session

        envelopes = await stored_operations(factory, identity)

        # NON-VACUITY: this root really carried a completed operation.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert [row["state"] for row in envelopes] == ["COMPLETED"], (
            f"stand: the operation did not complete in the root under test: {envelopes}"
        )

        # VERDICT.
        gc.collect()
        assert root_ref() is None, (
            "the committed root transaction is still alive after a full collection: the registry "
            "keeps a strong reference back to its own key, so every finished transaction is "
            "retained for the life of the process"
        )
    finally:
        await drop_world(factory, world)


# Condition 2's other two clauses - an AUTOCOMMIT root and a two-phase root - are NOT here, and the
# reason is a property of this tier rather than a convenience.
#
# AUTOCOMMIT. The counterexample needs an engine built as `create_async_engine(url,
# isolation_level="AUTOCOMMIT")`, which is the form the reviewer showed `_execution_options` cannot
# see. On SQLite such an engine could not exist in this repository: the T1525 engine guard
# (deleted with SQLite, 017 stage 3 S7) required every SQLite engine
# construction to be paired with `install_sqlite_transaction_control`, and an engine that carries the
# control is not in AUTOCOMMIT any more - the control's `begin` listener sends a real `BEGIN`. The
# two demands are genuinely incompatible, and the guard is the one that protects money, so the
# counterexample moves to PostgreSQL rather than the guard acquiring an exemption.
#
# TWO-PHASE. `Connection.begin_twophase()` on the pysqlite/aiosqlite dialect raises
# `NotImplementedError` (measured on this tree), so SQLite cannot host that shape at all.
#
# Both live in `tests/integration/test_p015_b4_transaction_contract_postgres.py`.


# ==============================================================================================
# C7 - rollback and reopen
# ==============================================================================================


@pytest.mark.asyncio
async def test_c7_a_rolled_back_operation_leaves_nothing_and_the_identity_reopens_clean(
    db_session,
) -> None:
    """C7, API-SHAPED. A rolled back operation must leave no journal row and must be reopenable.

    RED TODAY BECAUSE: there are no journal tables, so "no rows in the three journal tables after
    the rollback" cannot be distinguished from "there is nowhere for a row to be". `stored_*` return
    None for a missing table and `[]` for an empty one, and the assertions below tell them apart.
    MUTATION once step 4 exists: keep the registry entry across a real root rollback (design v2
    §1.3, `rollback(conn)` → drop whole state), so the reopen hits the UNIQUE(kind, identity) of the
    first, rolled back, envelope.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory)
    identity = _identity("reopen")
    try:
        async with factory() as session:
            async with await _open(api, session, world, "reopen", identity=identity):
                session.add(world.debt("19.00"))
                await session.flush()
            await session.rollback()

        # The same identity, opened again in a new transaction, must succeed.
        reopen_error: BaseException | None = None
        async with factory() as session:
            try:
                async with await _open(api, session, world, "reopen", identity=identity):
                    session.add(world.debt("23.00"))
                    await session.flush()
                await session.commit()
            except Exception as exc:  # recorded, asserted below
                reopen_error = exc

        after = await stored_debts(factory, world)
        envelopes = await stored_operations(factory, identity)
        entries = await stored_entries(factory, identity)

        # NON-VACUITY: the rollback really discarded the first attempt's money, and the second
        # attempt really wrote.
        assert after == {("debtor", "creditor", "eq"): Decimal("23.00000000")}, (
            f"stand: the two attempts did not leave exactly the second one's debt: {after}"
        )
        assert reopen_error is None, f"reopening the identity after a rollback failed: {reopen_error!r}"

        # VERDICT.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert [row["state"] for row in envelopes] == ["COMPLETED"], (
            f"the rolled back attempt left an envelope behind, or the reopened one did not "
            f"complete: {envelopes}"
        )
        assert entries and len(entries) == 1, (
            f"the journal entries do not describe exactly the surviving attempt: {entries}"
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c7_a_savepoint_bound_operation_rolled_back_leaves_the_root_intact(
    db_session,
) -> None:
    """C7, second half, API-SHAPED. And the anti-vacuum control for binding condition 1.

    A savepoint whose rollback SUCCEEDS must take its operation with it and nothing else: the
    root's own completed operation keeps its entries, and the rolled-back one leaves no envelope and
    no entries at all. Condition 1's counterexample is the same shape with the rollback failing, so
    without this control a journal could satisfy condition 1 by never dropping anything, and the
    staged-payment path - where a rejected payment's savepoint is rolled back while its siblings
    commit - would lose every sibling.

    RED TODAY BECAUSE: there is no envelope table, so "the rolled-back operation left nothing"
    cannot be told from "nothing was ever recorded". The money half is asserted first and is already
    correct on this tree since T1525.
    MUTATION once step 4 exists: on `rollback_savepoint`, drop the WHOLE registry state for the root
    rather than only the operations whose chain contains that savepoint name; the root's own
    operation then loses its record and its commit is refused.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    root_identity = _identity("root-op")
    savepoint_identity = _identity("savepoint-op")
    try:
        commit_refusal: BaseException | None = None
        async with factory() as session:
            async with await _open(api, session, world, "root-op", identity=root_identity):
                session.add(world.debt("10.00"))
                await session.flush()

            nested = await session.begin_nested()
            async with await _open(
                api, session, world, "savepoint-op", identity=savepoint_identity
            ):
                session.add(world.debt("50.00", creditor_id=world.extra_participants[0].id))
                await session.flush()
            await nested.rollback()

            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)
        root_envelopes = await stored_operations(factory, root_identity)
        savepoint_envelopes = await stored_operations(factory, savepoint_identity)
        root_entries = await stored_entries(factory, root_identity)

        # NON-VACUITY: the savepoint really rolled back and the root really committed.
        assert commit_refusal is None, (
            f"the root commit was refused although its own operation completed and only a "
            f"descendant savepoint was rolled back: {commit_refusal!r}"
        )
        assert after == {("debtor", "creditor", "eq"): Decimal("10.00000000")}, (
            f"stand: the root did not commit exactly its own debt: {after}"
        )

        # VERDICT.
        assert root_envelopes is not None, missing_journal_tables(root_envelopes, OPERATIONS_TABLE)
        assert [row["state"] for row in root_envelopes] == ["COMPLETED"], root_envelopes
        assert savepoint_envelopes == [], (
            f"the operation bound to the rolled back savepoint left an envelope: "
            f"{savepoint_envelopes}"
        )
        assert root_entries and len(root_entries) == 1, (
            f"the root operation's entries do not exclude the rolled back savepoint's: "
            f"{root_entries}"
        )
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C9 - writes that no operation covers
# ==============================================================================================


@pytest.mark.asyncio
async def test_c9_a_debt_written_after_the_operations_transaction_ended_is_refused(
    db_session,
) -> None:
    """C9, DEFECT-SHAPED. A completed operation does not license the next transaction's writes.

    RED TODAY BECAUSE: nothing refuses; the second transaction's debt is stored.
    DEGENERATE FORM TODAY, stated so it is not mistaken for the full property: with no journal there
    is no operation in the first transaction either, so today's red says "an uncovered write was not
    refused". Once step 4 exists the first transaction really completes an operation and the test
    says what its name says - that the completed operation did not carry over.
    MUTATION once step 4 exists: keep the registry state on the root's `commit` event instead of
    letting the new root start empty, so a write in the next transaction finds a stale OPEN record.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    try:
        async with factory() as session:
            async with await _open(api, session, world, "first"):
                session.add(world.debt("11.00"))
                await session.flush()
            await session.commit()

            # Same session, NEW database transaction, no operation.
            session.add(world.debt("29.00", creditor_id=world.extra_participants[0].id))
            refusal = await refusal_of(api, session.flush())
            if refusal is None:
                await session.commit()

        after = await stored_debts(factory, world)

        # NON-VACUITY: the first, covered write really committed, so the second transaction really
        # ran on a live connection.
        assert after.get(("debtor", "creditor", "eq")) == Decimal("11.00000000"), (
            f"stand: the first transaction did not commit its debt: {after}"
        )

        # VERDICT.
        assert refusal is not None, (
            f"a Debt was written in a NEW database transaction after the operation's transaction "
            f"had committed, and nothing refused it: {after}. An operation covers one database "
            f"transaction and does not survive its commit."
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c9_an_operation_on_one_session_does_not_cover_a_write_on_another(
    db_session,
) -> None:
    """C9, DEFECT-SHAPED. Session B's debt is not covered by session A's operation.

    THE TWO SESSIONS SHARE ONE `Connection`, and that is not a convenience. On SQLite two separate
    connections cannot both be writing: since T1525 a transaction that has written holds the write
    lock, and session B would fail with "database is locked" before reaching anything this test is
    about - a red for the stand's own limits, not for the property. Sharing the connection also
    makes the scenario the harder one for the journal: both sessions are inside the SAME database
    transaction, so a registry keyed only by the root transaction, with no session identity on the
    record, would accept B's write. That shape is real - the clearing service runs a second,
    `Connection`-bound work session (`app/core/clearing/service.py`, design v2 §1.2). The variant
    with two genuinely independent transactions belongs on the PostgreSQL tier.

    RED TODAY BECAUSE: session B's write is stored with nothing refusing it.
    DEGENERATE FORM TODAY: there is no operation on A either; the sentence the failure prints is
    the weaker "an uncovered write was not refused".
    MUTATION once step 4 exists: find the operation by root transaction alone (design v2 §6
    requires ALSO that the record's `session_ref` is this session), and B's write starts passing.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    try:
        async with engine.connect() as connection:
            shared = async_sessionmaker(
                bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            async with shared() as session_a, shared() as session_b:
                async with await _open(api, session_a, world, "session-a"):
                    session_a.add(world.debt("7.00"))
                    await session_a.flush()

                    # A different Session on the same Connection, with no operation of its own.
                    session_b.add(
                        world.debt("31.00", creditor_id=world.extra_participants[0].id)
                    )
                    refusal = await refusal_of(api, session_b.flush())

                    # NON-VACUITY, READ INSIDE THE SHARED TRANSACTION. It used to be read after the
                    # commit - "session A's own write really committed" - which cannot hold once
                    # the journal works: B's refusal poisons the root A and B share, so A's commit
                    # is refused too and nothing at all is durable. That is the design's own rule
                    # (a hook refusal poisons the ROOT until a real rollback), not a surprise. What
                    # the assertion was for - "the shared transaction really lived and B's flush
                    # really ran inside it" - is read here, where it is still true.
                    lived = (
                        await session_a.execute(
                            select(Debt.amount).where(
                                Debt.equivalent_id == world.equivalent.id
                            )
                        )
                    ).scalars().all()
                await refusal_of(api, session_a.commit())

        after = await stored_debts(factory, world)

        assert [Decimal(str(value)) for value in lived] == [Decimal("7.00000000")], (
            f"stand: session A's own write never reached the shared transaction ({lived}), so "
            f"session B never flushed inside a live one"
        )

        # VERDICT.
        assert refusal is not None, (
            f"session B flushed a Debt into the transaction whose only open operation belongs to "
            f"session A, and nothing refused it: {after}. An operation covers its own session, not "
            f"every session that happens to share the connection."
        )
        assert after.get(("debtor", "extra0", "eq")) is None, (
            f"session B's uncovered debt is durable: {after}"
        )
    finally:
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c9_an_operation_orphaned_by_session_close_makes_the_external_commit_refuse(
    db_session,
) -> None:
    """C9, DEFECT-SHAPED. The `rollback_only` shape of design v2 §1.1(b), on real debts.

    THE SHAPE. A session joined to an EXTERNAL `Connection` can end its own root without any
    database rollback (`sqlalchemy/orm/session.py:1361`, `:1377`): `session.close()` ends the
    session while the database transaction lives on. Anything that lived in `session.info` dies with
    it, and the external owner's `commit()` then persists whatever was written. That is why the
    design keys the registry by the Core root transaction rather than by the session, and it is
    prototype 3 of design v2 §1.3: "rollback_only close then outer.commit REFUSED []".

    THERE IS NO SECOND SESSION HERE, AND ITS REMOVAL IS THE POINT. The earlier shape of this test
    had a session B flush an undeclared Debt into the same external transaction and then asserted
    "something refused". Measured 2026-09-13: the orphaned operation was not refused at all -
    exiting its block after the `close()` re-acquires a connection and COMPLETES the envelope
    (`AFTER_CLOSE [('COMPLETED',)]`, `effect_count=1`) - and both refusals that test caught came
    from session B's own undeclared write (`no_operation`, then `root_poisoned` on the commit). The
    property C9 names was therefore carried entirely by a write the design never mentions. It is
    asserted here with nothing else in the transaction, so only the orphan can refuse it.

    WHICH MEANS THE BLOCK MUST NOT EXIT. `async with` completes the operation, and a COMPLETED
    operation is one the commit is ALLOWED to carry - correctly, as the control below measures. The
    state the design describes is an operation that is still OPEN when its session dies, so the
    context is entered through an `AsyncExitStack` and abandoned: that is what "A closes with an
    OPEN op" is, and no public call produces it while the `with` block is still running.

    RED BEFORE STEP 4 BECAUSE: the external commit stores the abandoned operation's debt.
    MUTATION, MEASURED 2026-09-13: drop the `is_settled` arm of `_blocking_problem`, so a root commit
    stops asking whether the operations registered in it finished. This test then goes red quoting
    `{('debtor', 'creditor', 'eq'): Decimal('6.00000000')}` as durable, and the control below stays
    green. The design-level mutation is the same thing one layer up - hold the record in
    `session.info` (v1's design) instead of in a registry keyed by the Core root, and the record dies
    with the closed session so there is nothing left for that arm to find.
    """
    from tests.conftest import TestingSessionLocal, engine

    api = journal_api()
    world = await seed_world(TestingSessionLocal)
    identity = _identity("orphan")
    try:
        record = None
        envelope_inside = None
        debts_inside = None
        commit_refusal: BaseException | None = None
        async with engine.connect() as connection:
            external = await connection.begin()
            abandoned_factory = async_sessionmaker(
                bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            session_a = abandoned_factory()
            stack = AsyncExitStack()
            try:
                record = await stack.enter_async_context(
                    await _open(api, session_a, world, "orphan", identity=identity)
                )
                session_a.add(world.debt("6.00"))
                await session_a.flush()
                # The session is closed while its operation is still open and while the database
                # transaction it wrote in is still alive. The block is never left.
                await session_a.close()

                # NON-VACUITY, READ ON THE CONNECTION THAT IS STILL INSIDE THE TRANSACTION. Read
                # after the commit it would say nothing: the refusal rolls the transaction back, so
                # "no envelope, no debt" is the expected answer either way and a scenario that never
                # ran would be indistinguishable from one that was refused.
                envelope_inside = await _envelope_states_in_transaction(connection, identity)
                debts_inside = await _debt_amounts_in_transaction(connection, world)

                commit_refusal = await refusal_of(api, external.commit())
            finally:
                # Tidy-up AFTER the verdict, and it asserts nothing: abandoning the context IS the
                # scenario, so unwinding it raises whatever a completion on a rolled-back, poisoned
                # root raises. Swallowed here so the test reports its own verdict rather than the
                # exception of its own cleanup.
                try:
                    await stack.aclose()
                except BaseException:  # noqa: BLE001 - see above
                    pass

        after = await stored_debts(TestingSessionLocal, world)

        # NON-VACUITY: the operation really opened and really wrote, inside the external
        # transaction, and it was still OPEN when the session died.
        assert record is not None and api.available, api.missing(
            "an operation has to exist before it can be orphaned"
        )
        assert envelope_inside == ["OPEN"], (
            f"stand: the envelope this operation opened was {envelope_inside} inside the external "
            f"transaction, not ['OPEN'], so what the commit below meets is not an orphan"
        )
        assert debts_inside == [Decimal("6.00000000")], (
            f"stand: the abandoned operation's debt never reached the external transaction "
            f"({debts_inside}), so the commit below had nothing of it to persist"
        )

        # VERDICT.
        assert commit_refusal is not None, (
            f"a session was closed while its operation was still OPEN, and the external "
            f"transaction committed that operation's debt anyway: {after}. The record lives with "
            f"the Core root transaction, not with the session, so a commit that would cover an "
            f"unfinished operation must refuse."
        )
        assert after == {}, f"the orphaned root committed anyway: {after}"
    finally:
        await drop_world(TestingSessionLocal, world)


@pytest.mark.asyncio
async def test_c9_control_a_completed_operation_survives_the_same_session_close(db_session) -> None:
    """C9, anti-vacuum control. GREEN, and it is what makes the refusal above mean something.

    SAME SCENARIO, ONE DIFFERENCE: the operation is COMPLETED before the session is closed. The
    external commit must then be ALLOWED and the money durable. Without this the refusal above
    would be satisfied by a journal that refused every commit on a connection whose session had
    closed - a rule that would stop the rollback_only shape working at all - and the counterexample
    could not tell "the orphan was caught" from "a close poisons everything".

    MUTATION, MEASURED 2026-09-13: refuse the commit whenever an operation's session has ended,
    whatever state the operation is in (`_blocking_problem` gaining an arm on
    `op.session.get_transaction() is None`). This control goes red and the test above stays green,
    which is the pair showing the two halves are distinguishable by the stand and not only by prose.
    """
    from tests.conftest import TestingSessionLocal, engine

    api = journal_api()
    world = await seed_world(TestingSessionLocal)
    identity = _identity("orphan-control")
    try:
        async with engine.connect() as connection:
            external = await connection.begin()
            factory = async_sessionmaker(
                bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            session_a = factory()
            async with await _open(api, session_a, world, "orphan-control", identity=identity):
                session_a.add(world.debt("6.00"))
                await session_a.flush()
            envelope_inside = await _envelope_states_in_transaction(connection, identity)
            # The same `close()` as the test above, with the operation already finished.
            await session_a.close()
            commit_refusal = await refusal_of(api, external.commit())

        after = await stored_debts(TestingSessionLocal, world)

        # NON-VACUITY: the operation really finished before the close.
        assert envelope_inside == ["COMPLETED"], (
            f"stand: the operation was {envelope_inside} when the session closed, so this is not "
            f"the completed half of the pair"
        )

        # VERDICT.
        assert commit_refusal is None, (
            f"a COMPLETED operation's external commit was refused because its session had closed: "
            f"{commit_refusal!r}. The rollback_only shape is legitimate; what C9 forbids is an "
            f"UNFINISHED operation surviving its session, not a finished one."
        )
        assert after == {("debtor", "creditor", "eq"): Decimal("6.00000000")}, (
            f"the completed operation's money is not durable: {after}"
        )
    finally:
        await drop_world(TestingSessionLocal, world)


# ==============================================================================================
# C10 - what refusal leaves behind, split into root commit and release (binding condition 5)
# ==============================================================================================


@pytest.mark.asyncio
async def test_c10_a_refused_root_commit_leaves_no_open_database_transaction(db_session) -> None:
    """C10, root half, DEFECT-SHAPED. After refusing a root commit, nothing may stay open.

    WHY THE STATE MATTERS (design v2 §1.2). SQLAlchemy dispatches the `commit` event before
    `do_commit` (`sqlalchemy/engine/base.py:1123`). An event that just raises leaves
    `RootTransaction` deactivated but current (`:2720-2746`), and the later `rollback()` skips the
    database rollback because `is_active` is False (`:2698-2712`) - the reviewer's probe then had
    the refused row committed by the NEXT commit. So a refusal inside the commit event must end the
    refused scope at database level first and raise second.

    RED TODAY BECAUSE: the commit is not refused at all, and the incomplete operation's debt is
    stored.
    THE CONNECTION IS HELD BY THE TEST, not by the session: the shared test engine uses `NullPool`,
    so a session that finishes its transaction hands the connection back and it is closed at once -
    and a closed connection would answer "nothing open" no matter what the refusal did. See
    `_driver_transaction_state`.
    MUTATION once step 4 exists: raise from the Core `commit` event without calling
    `dialect.do_rollback` first; this test must then see a transaction still open, or the debt
    durable on the next commit.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory)
    try:
        refusal: BaseException | None = None
        state_after = "unobserved"
        async with engine.connect() as connection:
            driver = (await connection.get_raw_connection()).driver_connection
            held = async_sessionmaker(
                bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            async with held() as session:
                try:
                    async with await _open(api, session, world, "incomplete"):
                        session.add(world.debt("15.00"))
                        await session.flush()
                        # The bypass: commit the root while this operation is still OPEN.
                        await session.commit()
                except api.refusals as exc:  # noqa: B902
                    refusal = exc
                state_after = _driver_transaction_state(driver)

        after = await stored_debts(factory, world)

        # NON-VACUITY: the connection was still the test's own when it was read, so the answer is
        # about the refusal and not about a connection the pool had already reset.
        assert state_after in {"open", "none"}, (
            f"stand: the connection was {state_after} when the transaction state was read, so it "
            f"says nothing about what the refusal did"
        )

        # VERDICT.
        assert refusal is not None, (
            f"the root was committed while a debt operation was still OPEN and nothing refused it: "
            f"the database holds {after}."
        )
        assert state_after == "none", (
            "the commit was refused but the driver still has a transaction open: a refusal inside "
            "the Core commit event must roll the database back before it raises, or the next "
            "commit on this connection persists the refused rows"
        )
        assert after == {}, f"the refused commit still stored the debt: {after}"
    finally:
        await drop_world(factory, world)


# The journal detaches the savepoint it rolled back (`_release_refused_nested`), so the tidy-up
# rollback below finds it already gone and SQLAlchemy says so. Ignored rather than avoided: the
# rollback is what puts the SESSION's own nesting back, and the warning is the Core half reporting
# that the journal had already put the CONNECTION's back.
@pytest.mark.filterwarnings("ignore:nested transaction already deassociated")
@pytest.mark.asyncio
async def test_c10_a_refused_release_leaves_the_root_open_and_poisoned(db_session) -> None:
    """C10, release half, DEFECT-SHAPED. A refused RELEASE is not a refused commit.

    The round-2 reviewer corrected C10 here (`review-round2-codex.md` §2): "Отказ release через
    `ROLLBACK TO` намеренно оставляет root открытым и poisoned. Поэтому требование C10 'DBAPI tx not
    open' для всех отказов неверно". So this half asserts the OPPOSITE state from the root half: the
    savepoint's effects are gone, the root is still open, and it is poisoned - a later root commit
    must refuse too.

    RED TODAY BECAUSE: the release is not refused, the root commits, and the debt is stored.
    MUTATION once step 4 exists: make the release refusal call `do_rollback` (the root recipe)
    instead of `do_rollback_to_savepoint` plus root poison - the root would then be closed here, and
    a sibling operation's work would be destroyed by a neighbour's incomplete one.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    try:
        release_refusal: BaseException | None = None
        commit_refusal: BaseException | None = None
        in_transaction_after = "unobserved"

        async with factory() as session:
            # The root begins with its own write, so the savepoint is a real savepoint (T1525).
            async with await _open(api, session, world, "root"):
                session.add(world.debt("4.00"))
                await session.flush()

            driver = await _raw_driver_connection(session)
            nested = await session.begin_nested()
            try:
                async with await _open(api, session, world, "savepoint"):
                    session.add(
                        world.debt("26.00", creditor_id=world.extra_participants[0].id)
                    )
                    await session.flush()
                    # The bypass: release the savepoint while its operation is still OPEN.
                    await nested.commit()
            except api.refusals as exc:  # noqa: B902
                release_refusal = exc
            in_transaction_after = _driver_transaction_state(driver)
            # THE SAVEPOINT IS ROLLED BACK FIRST, so that what refuses the commit below is the
            # JOURNAL and not SQLAlchemy. A `NestedTransaction` whose RELEASE raised is left
            # deactivated-but-present, and the next `Session.commit()` on it raises
            # `PendingRollbackError` ("Can't reconnect until invalid savepoint transaction is
            # rolled back") before any Core commit event runs - a refusal by the ORM's own
            # bookkeeping, which says nothing about the root poison this test is named after.
            # Rolling the savepoint back clears that bookkeeping and, by design, KEEPS the root
            # poison (`app/core/ledger/journal.py`, `_on_rollback_savepoint`: the ops bound to the
            # savepoint are dropped, the poison is not). So the commit that follows reaches the
            # journal's `commit` event, and `commit_refusal` is the journal's.
            await nested.rollback()
            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY: the savepoint really wrote inside a real root transaction, and the
        # connection was still the session's own when its state was read.
        assert in_transaction_after != "closed", (
            "stand: the connection was already closed when its state was read"
        )

        # VERDICT.
        assert release_refusal is not None, (
            f"a savepoint was RELEASEd while the operation bound to it was still OPEN and nothing "
            f"refused it: the database holds {after}."
        )
        assert in_transaction_after == "open", (
            "the refused release closed the root transaction: a release refusal must roll back only "
            "to the savepoint and leave the root open and poisoned"
        )
        assert commit_refusal is not None, (
            "the root committed after a refused release: the release refusal must poison the root "
            "until a real database rollback"
        )
        assert after == {}, f"the refused release left durable rows: {after}"
    finally:
        await drop_world(factory, world)


# ==============================================================================================
# C11 - failure classes: the journal's own I/O versus the caller's business failure
# ==============================================================================================


#: The three machinery failures design v2 §9 `C11` names, as `(label, when, raises)`. `when` is the
#: statement against `debt_operations` the injection fires on: `insert` is the envelope write in
#: `__aenter__`, `update` is the completion write at exit. Until 2026-09-13 only the first existed,
#: while the test's own docstring claimed "the envelope INSERT in `__aenter__`, the entries and
#: completion UPDATE at exit" - two thirds of the claim had no case.
_MACHINERY_FAILURES = [
    ("the envelope INSERT at open", "insert", RuntimeError),
    ("the completion UPDATE at exit", "update", RuntimeError),
    ("a cancellation during the completion UPDATE", "update", asyncio.CancelledError),
]


@pytest.mark.parametrize(
    "label,when,raises", _MACHINERY_FAILURES, ids=[row[0] for row in _MACHINERY_FAILURES]
)
@pytest.mark.asyncio
async def test_c11_a_failure_in_the_operations_own_io_poisons_the_root(
    db_session, label, when, raises
) -> None:
    """C11, machinery half, API-SHAPED. A journal I/O failure is not a business failure.

    A failure or cancellation while the operation writes its OWN rows - the envelope INSERT in
    `__aenter__`, the entries and completion UPDATE at exit - leaves the journal unable to say what
    happened. Design v2 §1.5 makes that a ROOT poison: no commit and no release may go through
    until a real database rollback. Only a failure in the caller's block is discardable.

    ALL THREE OF THE DESIGN'S CASES, which is what changed on 2026-09-13. Design v2 §9 names
    "injected exception in envelope INSERT in `__aenter__`, in completion I/O, CancelledError during
    completion"; only the first was written. The completion cases are not a restatement of the open
    case: at open nothing of the caller's work has been journalled yet, while at completion the
    entries are already written and the envelope is still `OPEN` - so a mechanism that poisoned on
    the way in and merely logged on the way out would have passed the single case and left every
    half-recorded operation committable.

    THE INJECTION names no private symbol: a `before_execute` listener refuses the statement that
    writes the envelope table, which is the operation's own I/O by definition.

    RED BEFORE STEP 4 BECAUSE: no envelope statement is ever attempted, so the injection never fires
    - checked first, below, so this cannot pass by injecting nothing.
    MUTATION, and it is the one that was MEASURED rather than the one that reads well. Make a failed
    completion mark the envelope COMPLETED and clear the root poison (`_complete`'s
    `except BaseException`, keeping the `raise`): the two completion cases then go red quoting the
    stored debt, and the open case stays green because `_complete` never runs there. Measured
    2026-09-13.

    AND THE OBVIOUS MUTATION IS NOT ONE, which is worth writing down because it reads like it is:
    dropping the `_poison(state, "operation completion failed")` line alone changes nothing here. The
    record is left `POISONED`, `_blocking_problem` refuses any root commit that carries an operation
    which `is_settled` is false for, and the refusal simply arrives under
    `operation_not_completed` instead of `root_poisoned`. The poison is what survives a SAVEPOINT
    rollback; it is not what refuses this commit.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory)
    injected: list[str] = []
    message = f"p015-b4: the operation's own {when} against {OPERATIONS_TABLE} failed"

    def _fail_the_envelope_statement(_conn, clauseelement, _multiparams, _params, _options) -> None:
        table = getattr(getattr(clauseelement, "table", None), "name", None)
        if table != OPERATIONS_TABLE or not getattr(clauseelement, f"is_{when}", False):
            return
        injected.append(f"{when} {table}")
        raise raises(message)

    event.listen(engine.sync_engine, "before_execute", _fail_the_envelope_statement)
    try:
        machinery_failure: BaseException | None = None
        commit_refusal: BaseException | None = None
        async with factory() as session:
            try:
                async with await _open(api, session, world, "io-failure"):
                    session.add(world.debt("9.00"))
                    await session.flush()
            except BaseException as exc:  # noqa: BLE001 - CancelledError is one of the cases
                machinery_failure = exc
            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY: the operation really reached the statement the injection is attached to, and
        # the injection really fired. Without this the verdict would be about a scenario that never
        # happened - and for the completion cases it is also what proves the envelope INSERT
        # SUCCEEDED, so the failure really is at completion and not a relabelled open failure.
        assert injected == [f"{when} {OPERATIONS_TABLE}"], (
            f"the operation never reached its own {when} against `{OPERATIONS_TABLE}` (fired: "
            f"{injected}), so no failure could be injected into its I/O. {JOURNAL_MODULE} may not "
            f"exist yet; this is the step-2 red state, not a passing test."
        )
        assert isinstance(machinery_failure, raises) and message in str(machinery_failure), (
            f"stand: the injected {raises.__name__} did not reach the caller: {machinery_failure!r}"
        )

        # VERDICT.
        assert commit_refusal is not None, (
            f"the operation's own I/O failed at {label} and the root committed anyway: {after}. A "
            f"machinery failure must poison the root until a real database rollback."
        )
        assert after == {}, f"a root poisoned by a journal I/O failure still stored a debt: {after}"
    finally:
        event.remove(engine.sync_engine, "before_execute", _fail_the_envelope_statement)
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c11_the_completion_poison_survives_a_savepoint_rollback_that_removes_the_record(
    db_session,
) -> None:
    """C11, machinery half: THE SCENARIO IN WHICH THE COMPLETION POISON IS THE ONLY THING LEFT.

    WHY THIS EXISTS. The test above says in its own docstring that dropping
    `_poison(state, "operation completion failed")` reddens nothing, and names the reason: the record
    is left `POISONED`, `_blocking_problem` refuses the commit under `operation_not_completed`
    (`app/core/ledger/journal.py:890-894`, at `c17fa26`), and the poison is never consulted. The round-3 reviewer
    called that what it is - an admitted hole in the evidence - and named the shape that closes it: a
    completion failure inside a savepoint THAT IS THEN ROLLED BACK SUCCESSFULLY.
    `_confirm_savepoint_rollback` (`:1038-1043`) removes from `state.ops` every operation whose chain
    contains that savepoint and KEEPS `state.poison`. After it runs, the half-recorded operation is no
    longer in the list `_blocking_problem` walks - so the poison, and nothing else, is what stands
    between the root and a commit.

    THE STAND, and why each part of it is there:

    * the ROOT opens and COMPLETES an operation of its own first, so `_blocking_problem` has no
      unsettled operation to catch for an unrelated reason, and so the savepoint below is a real
      savepoint rather than the transaction itself (T1525);
    * the injection is armed only AFTER that, so it hits the completion `UPDATE` of the
      savepoint-bound operation and not the root's;
    * the savepoint rollback must really run as SQL - watched on `after_cursor_execute` - because a
      rollback that only fired the event leaves `pending_savepoint_rollbacks` set, and THAT would
      refuse the commit under `unconfirmed_rollback` instead, which is binding condition 1's property
      and not this one.

    NON-VACUITY, FIRST, IN THREE PARTS, because this test is about WHICH mechanism refuses: the
    injection fired at the completion UPDATE; the `ROLLBACK TO SAVEPOINT` really executed; and the
    refusal's reason is `root_poisoned` and NOT `operation_not_completed` or `unconfirmed_rollback`.
    The third is the decisive one - it is the observable proof that the record `_blocking_problem`
    would have caught is gone, which is the whole point of the scenario.

    MUTATION, MEASURED 2026-09-13: delete the `_poison(...)` call from `_complete`'s
    `except BaseException` (keeping the `raise`). The root then COMMITS - both its own debt and
    nothing of the rolled-back one - and this test goes red on the verdict, while
    `test_c11_a_failure_in_the_operations_own_io_poisons_the_root` stays green. That is the mutation
    the module could not previously offer for the poison.
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    root_identity = _identity("poison-root-op")
    savepoint_identity = _identity("poison-savepoint-op")
    armed: list[bool] = []
    injected: list[str] = []
    rolled_back_savepoints: list[str] = []
    message = f"p015-b4: the savepoint operation's completion UPDATE against {OPERATIONS_TABLE} failed"

    def _fail_the_completion_update(_conn, clauseelement, _multiparams, _params, _options) -> None:
        table = getattr(getattr(clauseelement, "table", None), "name", None)
        if not armed or table != OPERATIONS_TABLE or not getattr(clauseelement, "is_update", False):
            return
        injected.append(f"update {table}")
        raise RuntimeError(message)

    def _watch_the_savepoint_rollback(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if statement.strip().upper().startswith("ROLLBACK TO SAVEPOINT"):
            rolled_back_savepoints.append(statement.strip())

    event.listen(engine.sync_engine, "before_execute", _fail_the_completion_update)
    event.listen(engine.sync_engine, "after_cursor_execute", _watch_the_savepoint_rollback)
    try:
        machinery_failure: BaseException | None = None
        commit_refusal: BaseException | None = None
        async with factory() as session:
            async with await _open(api, session, world, "poison-root-op", identity=root_identity):
                session.add(world.debt("10.00"))
                await session.flush()

            nested = await session.begin_nested()
            armed.append(True)
            try:
                async with await _open(
                    api, session, world, "poison-savepoint-op", identity=savepoint_identity
                ):
                    session.add(
                        world.debt("50.00", creditor_id=world.extra_participants[0].id)
                    )
                    await session.flush()
            except BaseException as exc:  # noqa: BLE001 - the injected machinery failure
                machinery_failure = exc
            armed.clear()
            await nested.rollback()

            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)
        root_envelopes = await stored_operations(factory, root_identity)
        savepoint_envelopes = await stored_operations(factory, savepoint_identity)

        # NON-VACUITY, FIRST. (1) The injection fired, exactly once, on the completion UPDATE - so
        # the envelope INSERT succeeded and this is a completion failure, not a relabelled open one.
        assert injected == [f"update {OPERATIONS_TABLE}"], (
            f"the savepoint operation never reached its completion UPDATE against "
            f"`{OPERATIONS_TABLE}` (fired: {injected}), so no completion failure was injected. "
            f"{JOURNAL_MODULE} may not exist yet; this is the step-2 red state, not a pass."
        )
        assert isinstance(machinery_failure, BaseException) and message in str(machinery_failure), (
            f"stand: the injected completion failure did not reach the caller: {machinery_failure!r}"
        )

        # (2) The savepoint rollback really ran as SQL. Without this the refusal below could be
        # `unconfirmed_rollback` - binding condition 1's property, not the poison's.
        assert rolled_back_savepoints, (
            "stand: no `ROLLBACK TO SAVEPOINT` statement was executed, so `state.ops` still holds "
            "the half-recorded operation and this scenario is not the one it is named after"
        )

        # VERDICT.
        assert commit_refusal is not None, (
            f"the completion UPDATE failed inside a savepoint, that savepoint was rolled back, and "
            f"the root committed anyway: {after}. After the rollback dropped the operation record, "
            f"the poison is the only thing that can refuse this commit - and it did not."
        )

        # (3) THE DECISIVE CONTROL: it is the POISON that refused, not the operation record. If the
        # record were still in `state.ops` the reason would be `operation_not_completed` and this
        # test would be a second copy of the one above.
        assert getattr(commit_refusal, "reason", None) == "root_poisoned", (
            f"the commit was refused as `{getattr(commit_refusal, 'reason', None)}`, not as "
            f"`root_poisoned`: {commit_refusal!r}. Then the savepoint rollback did NOT remove the "
            f"operation record, the poison is still untested, and this scenario has to be rebuilt "
            f"rather than believed."
        )
        assert after == {}, (
            f"a root poisoned by a completion failure inside a rolled-back savepoint still stored "
            f"money: {after}"
        )
        assert savepoint_envelopes == [], (
            f"the rolled-back savepoint left its envelope behind: {savepoint_envelopes}"
        )
        assert root_envelopes == [], (
            f"the poisoned root's own envelope became durable: {root_envelopes}"
        )
    finally:
        event.remove(engine.sync_engine, "after_cursor_execute", _watch_the_savepoint_rollback)
        event.remove(engine.sync_engine, "before_execute", _fail_the_completion_update)
        await drop_world(factory, world)


#: The two ways design v2 §9 `C11` says a caller's BLOCK can end badly: "business exception/
#: cancellation in body of staged payment inside action savepoint". The cancellation case did not
#: exist until 2026-09-13, and it is not the same case: `CancelledError` is a `BaseException`, so an
#: implementation whose discard path was written as `except Exception` would pass the first and poison
#: the whole tick on the second.
_BODY_FAILURES = [
    ("a business rejection", _BusinessFailure),
    ("a cancellation", asyncio.CancelledError),
]


@pytest.mark.parametrize(
    "label,raises", _BODY_FAILURES, ids=[row[0] for row in _BODY_FAILURES]
)
@pytest.mark.asyncio
async def test_c11_a_body_failure_in_one_block_leaves_its_sibling_committable(
    db_session, label, raises
) -> None:
    """C11, body half, API-SHAPED. Over-refusal is a defect too, and this is its counterexample.

    The staged-payment shape (`app/core/simulator/real_payments_executor.py:384`): each payment runs
    in its own action savepoint on the tick's session. A payment REJECTED for business reasons, or
    CANCELLED, must discard only its own operation; its siblings, and the tick, must still commit. A
    root poisoned by a neighbour's rejection would fail the whole tick - the mirror-image defect of
    C1, and just as expensive.

    RED BEFORE STEP 4 BECAUSE: the surviving sibling's envelope cannot be counted - there is no
    envelope table. The money half of this scenario is already correct on today's tree (T1525), and
    this test says so; what it could not see is that exactly one operation was recorded.
    MUTATION: poison the root on a body exception, rather than marking only that operation POISONED
    and letting its own savepoint discard it. Both cases then go red quoting the refusal the sibling's
    commit got. Measured 2026-09-13.

    THE TWO CASES ARE NOT SEPARABLE BY A MUTATION HERE, and saying so is the honest version. It is
    tempting to write "narrowing `debt_operation`'s body handler from `except BaseException` to
    `except Exception` reddens the cancellation case on its own" - it does not. Both siblings run
    inside their own action savepoint, and the savepoint's rollback drops the record from the
    registry whether or not the handler marked it POISONED first, so the sibling's commit is allowed
    either way and this test stays green. What the cancellation case is here for is the design's own
    list (§9: "business exception/cancellation in body of staged payment inside action savepoint"):
    it records that a `BaseException` reaches the discard path at all. An operation NOT bound to a
    savepoint is where the handler's width becomes observable, and that is `C10`'s subject.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    kept_identity = _identity("sibling-kept")
    rejected_identity = _identity("sibling-rejected")
    try:
        body_failure: BaseException | None = None
        commit_refusal: BaseException | None = None

        async with factory() as session:
            # Sibling 1: a payment that goes through, in its own action savepoint.
            async with session.begin_nested():
                async with await _open(api, session, world, "kept", identity=kept_identity):
                    session.add(world.debt("12.00"))
                    await session.flush()

            # Sibling 2: a payment that ends badly in its own action savepoint.
            try:
                async with session.begin_nested():
                    async with await _open(
                        api, session, world, "rejected", identity=rejected_identity
                    ):
                        session.add(
                            world.debt("28.00", creditor_id=world.extra_participants[0].id)
                        )
                        await session.flush()
                        raise raises("p015-b4: this payment ended in the body, not in the journal")
            except raises as exc:  # noqa: B902 - the parametrised body failure is the subject
                body_failure = exc

            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)
        kept = await stored_operations(factory, kept_identity)
        rejected = await stored_operations(factory, rejected_identity)

        # NON-VACUITY: the failure really happened and the sibling really committed its money.
        assert isinstance(body_failure, raises), (
            f"stand: the {label} never reached the caller: {body_failure!r}"
        )
        assert commit_refusal is None, (
            f"the tick's commit was refused because a SIBLING payment ended with {label}: "
            f"{commit_refusal!r}. A failure in the caller's block must discard only its own "
            f"operation."
        )
        assert after == {("debtor", "creditor", "eq"): Decimal("12.00000000")}, (
            f"stand: the surviving sibling's money is not exactly what committed: {after}"
        )

        # VERDICT.
        assert kept is not None, missing_journal_tables(kept, OPERATIONS_TABLE)
        assert [row["state"] for row in kept] == ["COMPLETED"], (
            f"the sibling that committed has no completed envelope: {kept}"
        )
        assert rejected == [], (
            f"the payment that ended with {label} left an operation envelope behind: {rejected}"
        )
    finally:
        await drop_world(factory, world)
