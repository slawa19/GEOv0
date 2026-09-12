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

MARKER. This module carries `b4_counterexample` and is deselected from the canonical gate. It is
red on purpose until step 4 exists, and STEP 4 REMOVES THE MARKER, NOT THE ASSERTIONS - the full
contract is in the comment above the marker list in `pytest.ini`, and
`tests/unit/test_p015_b4_counterexample_marker_is_not_a_hiding_place.py` holds it in place.
"""

from __future__ import annotations

import gc
import uuid
import weakref
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.debt import Debt
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

pytestmark = pytest.mark.b4_counterexample


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
    """`open`, `none` or `closed`, from `sqlite3.Connection.in_transaction`.

    `closed` is reported as its own answer rather than folded into `none`. A connection that has
    been handed back to the pool and closed cannot hold a transaction open, but it also cannot show
    that the refusal itself ended the transaction - the pool's reset-on-return would have done it
    either way, and accepting that would be exactly the "compensation further downstream" this
    repository forbids (`AGENTS.md` §9). A test that needs the distinction must therefore keep the
    connection checked out itself.
    """
    try:
        return "open" if driver.in_transaction else "none"
    except ValueError:
        return "closed"


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
            async with factory() as setup:
                setup.add(world.debt("10.00"))
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

        # NON-VACUITY: the write really reached the database. Without this an unrelated failure
        # (a rejected foreign key, an autoflush that never ran) would read as "the journal worked".
        if effect == "delete":
            assert after == {}, f"stand: the DELETE did not reach the database: {after}"
        else:
            assert after == {("debtor", "creditor", "eq"): expected_amount[effect]}, (
                f"stand: the {effect.upper()} did not reach the database as written: {after}"
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

            # Now an uninstrumented Debt write, and a caller that swallows whatever comes back.
            session.add(
                world.debt("34.00", creditor_id=world.extra_participants[0].id)
            )
            refusal = await refusal_of(api, session.flush())
            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY: the earlier write really happened and really was committed, so "nothing is
        # durable" below cannot come from a transaction that wrote nothing.
        assert after, (
            "stand: nothing at all was written, so this proves nothing about a swallowed refusal"
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
# see. On SQLite such an engine cannot exist in this repository: `tests/unit/
# test_p015_t1525_every_sqlite_engine_has_transaction_control.py` requires every SQLite engine
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
                await session_a.commit()

        after = await stored_debts(factory, world)

        # NON-VACUITY: session A's own, covered write really committed, so the shared transaction
        # really lived and B's flush really ran inside it.
        assert after.get(("debtor", "creditor", "eq")) == Decimal("7.00000000"), (
            f"stand: session A's own write did not commit, so the shared transaction never ran: "
            f"{after}"
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
async def test_c9_an_orphaned_operation_does_not_let_a_second_session_commit(db_session) -> None:
    """C9, DEFECT-SHAPED. The `rollback_only` shape of design v2 §1.1(b), on real debts.

    THE SHAPE. A session joined to an EXTERNAL `Connection` can end its own root without any
    database rollback (`sqlalchemy/orm/session.py:1361`, `:1377`): `session.close()` ends the
    session while the database transaction lives on. Anything that lived in `session.info` dies with
    it, and the external owner's `commit()` then persists whatever was written. That is why the
    design keys the registry by the Core root transaction rather than by the session.

    RED TODAY BECAUSE: the external commit stores both writes, the abandoned one included.
    MUTATION once step 4 exists: hold the operation record in `session.info` (v1's design) instead
    of in a registry keyed by the Core root, and the abandoned operation disappears with the closed
    session while its debt commits.
    """
    from tests.conftest import TestingSessionLocal, engine

    api = journal_api()
    world = await seed_world(TestingSessionLocal, extra_participants=1)
    try:
        async with engine.connect() as connection:
            external = await connection.begin()
            abandoned_factory = async_sessionmaker(
                bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            session_a = abandoned_factory()
            async with await _open(api, session_a, world, "orphan"):
                session_a.add(world.debt("6.00"))
                await session_a.flush()
                # The session is closed while its operation is still open and while the database
                # transaction it wrote in is still alive.
                await session_a.close()

            async with abandoned_factory() as session_b:
                session_b.add(
                    world.debt("37.00", creditor_id=world.extra_participants[0].id)
                )
                flush_refusal = await refusal_of(api, session_b.flush())

            commit_refusal = await refusal_of(api, external.commit())

        after = await stored_debts(TestingSessionLocal, world)

        # NON-VACUITY: both writes really reached the shared database transaction.
        assert after or (flush_refusal, commit_refusal) != (None, None), (
            "stand: nothing was written and nothing was refused, so the scenario never ran"
        )

        # VERDICT.
        assert (flush_refusal, commit_refusal) != (None, None), (
            f"a session was closed with an operation still open, a second session wrote a Debt in "
            f"the same external transaction, and the external commit stored everything: {after}. "
            f"An orphaned operation must make any commit or release on that root refuse."
        )
        assert after == {}, f"the orphaned root committed anyway: {after}"
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


@pytest.mark.asyncio
async def test_c11_a_failure_in_the_operations_own_io_poisons_the_root(db_session) -> None:
    """C11, machinery half, API-SHAPED. A journal I/O failure is not a business failure.

    A failure or cancellation while the operation writes its OWN rows - the envelope INSERT in
    `__aenter__`, the entries and completion UPDATE at exit - leaves the journal unable to say what
    happened. Design v2 §1.5 makes that a ROOT poison: no commit and no release may go through
    until a real database rollback. Only a failure in the caller's block is discardable.

    THE INJECTION names no private symbol: a `before_execute` listener refuses the first statement
    that writes the envelope table, which is the operation's own I/O by definition.

    RED TODAY BECAUSE: no envelope INSERT is ever attempted, so the injection never fires - checked
    first, below, so this cannot pass by injecting nothing.
    MUTATION once step 4 exists: treat an exception from the journal's own I/O like a body exception
    (discard the operation record and let the root commit).
    """
    from tests.conftest import TestingSessionLocal as factory, engine

    api = journal_api()
    world = await seed_world(factory)
    injected: list[str] = []
    message = "p015-b4: the operation's own envelope INSERT failed"

    def _fail_the_envelope_write(_conn, clauseelement, _multiparams, _params, _options) -> None:
        rendered = str(getattr(clauseelement, "table", "")) or str(clauseelement)
        if OPERATIONS_TABLE in rendered and "insert" in type(clauseelement).__name__.lower():
            injected.append(rendered)
            raise RuntimeError(message)

    event.listen(engine.sync_engine, "before_execute", _fail_the_envelope_write)
    try:
        machinery_failure: BaseException | None = None
        commit_refusal: BaseException | None = None
        async with factory() as session:
            try:
                async with await _open(api, session, world, "io-failure"):
                    session.add(world.debt("9.00"))
                    await session.flush()
            except Exception as exc:  # recorded, asserted below
                machinery_failure = exc
            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)

        # NON-VACUITY: the operation really tried to write its envelope, and the injection really
        # fired. Without this the verdict would be about a scenario that never happened.
        assert injected, (
            f"no INSERT into `{OPERATIONS_TABLE}` was ever attempted, so no failure could be "
            f"injected into the operation's own I/O. {JOURNAL_MODULE} does not exist yet; this is "
            f"the step-2 red state, not a passing test."
        )
        assert machinery_failure is not None and message in str(machinery_failure), (
            f"stand: the injected failure did not reach the caller: {machinery_failure!r}"
        )

        # VERDICT.
        assert commit_refusal is not None, (
            f"the operation's own I/O failed and the root committed anyway: {after}. A machinery "
            f"failure must poison the root until a real database rollback."
        )
        assert after == {}, f"a root poisoned by a journal I/O failure still stored a debt: {after}"
    finally:
        event.remove(engine.sync_engine, "before_execute", _fail_the_envelope_write)
        await drop_world(factory, world)


@pytest.mark.asyncio
async def test_c11_a_business_failure_in_one_block_leaves_its_sibling_committable(
    db_session,
) -> None:
    """C11, body half, API-SHAPED. Over-refusal is a defect too, and this is its counterexample.

    The staged-payment shape (`app/core/simulator/real_payments_executor.py:384`): each payment runs
    in its own action savepoint on the tick's session. A payment REJECTED for business reasons must
    discard only its own operation; its siblings, and the tick, must still commit. A root poisoned
    by a neighbour's rejection would fail the whole tick - the mirror-image defect of C1, and just
    as expensive.

    RED TODAY BECAUSE: the surviving sibling's envelope cannot be counted - there is no envelope
    table. The money half of this scenario is already correct on today's tree (T1525), and this test
    says so; what it cannot yet see is that exactly one operation was recorded.
    MUTATION once step 4 exists: poison the root on a body exception (rather than marking only that
    operation POISONED and discarding it with its own savepoint); the sibling's commit must then be
    refused.
    """
    from tests.conftest import TestingSessionLocal as factory

    api = journal_api()
    world = await seed_world(factory, extra_participants=1)
    kept_identity = _identity("sibling-kept")
    rejected_identity = _identity("sibling-rejected")
    try:
        business_failure: BaseException | None = None
        commit_refusal: BaseException | None = None

        async with factory() as session:
            # Sibling 1: a payment that goes through, in its own action savepoint.
            async with session.begin_nested():
                async with await _open(api, session, world, "kept", identity=kept_identity):
                    session.add(world.debt("12.00"))
                    await session.flush()

            # Sibling 2: a payment rejected on business grounds, in its own action savepoint.
            try:
                async with session.begin_nested():
                    async with await _open(
                        api, session, world, "rejected", identity=rejected_identity
                    ):
                        session.add(
                            world.debt("28.00", creditor_id=world.extra_participants[0].id)
                        )
                        await session.flush()
                        raise _BusinessFailure("p015-b4: this payment is rejected, not broken")
            except _BusinessFailure as exc:
                business_failure = exc

            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(factory, world)
        kept = await stored_operations(factory, kept_identity)
        rejected = await stored_operations(factory, rejected_identity)

        # NON-VACUITY: the rejection really happened and the sibling really committed its money.
        assert business_failure is not None, "stand: the business failure never reached the caller"
        assert commit_refusal is None, (
            f"the tick's commit was refused because a SIBLING payment was rejected: "
            f"{commit_refusal!r}. A business failure must discard only its own operation."
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
            f"the rejected payment left an operation envelope behind: {rejected}"
        )
    finally:
        await drop_world(factory, world)
