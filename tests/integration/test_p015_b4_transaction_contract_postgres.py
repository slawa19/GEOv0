"""Programme 015, `B4` step 2: the transaction contract and the write guard on PostgreSQL.

WHAT THIS MODULE IS. The PostgreSQL half of the step-2 acceptance bound to `B4` on 2026-09-11. The
SQLite tier is `tests/unit/test_p015_b4_transaction_contract.py` and
`tests/unit/test_p015_b4_write_guard.py`; this module holds only what SQLite cannot answer:

* C2's DML-inside-a-CTE forms. SQLite has no data-modifying CTEs, so the door the guard must see
  through does not exist there at all.
* C10's two halves as BACKEND state. `sqlite3.Connection.in_transaction` is the driver's opinion;
  `pg_stat_activity.state`, read from a different connection, is the server's. Design v2 §1.2 states
  the obligation in exactly those terms ("after refused commit, asyncpg adapter holds no open
  transaction (pg_stat_activity.state='idle')").
* C7's rollback-and-reopen, which the round-2 reviewer required on PostgreSQL as well
  (`review-round2-codex.md` §7: "C7 нужен также на PostgreSQL").
* C9 with two genuinely independent transactions. On SQLite a second writer cannot exist while the
  first holds the write lock, so the SQLite version of that counterexample has both sessions on one
  `Connection`; this is the version with two backends.
* Binding condition 2's two root shapes whose completion never reaches the Core `commit` event. The
  two-phase root SQLite cannot host at all - `begin_twophase()` on the pysqlite/aiosqlite dialect
  raises `NotImplementedError`, while on PostgreSQL `TwoPhaseTransaction` really is a
  `RootTransaction` subclass that commits through a different code path
  (`review-round2-codex.md` §2). The AUTOCOMMIT root SQLite cannot host EITHER, for a reason worth
  stating: an `isolation_level="AUTOCOMMIT"` SQLite engine would have to skip
  `install_sqlite_transaction_control`, which `tests/unit/
  test_p015_t1525_every_sqlite_engine_has_transaction_control.py` forbids - and a controlled engine
  is not in AUTOCOMMIT, because the control's `begin` listener sends a real `BEGIN`.

THE STAND. Its own engine with `isolation_level="SERIALIZABLE"` and a real pool, never the
`db_session` fixture: that fixture wraps every test in an outer transaction, which hides exactly the
commit and release boundaries these counterexamples are about. Verdicts are read on a NEW session,
and backend state on a connection that is not the one under test.

READ `tests/p015_b4_support.py` for the import rule and the two kinds of red.

MARKER. This module carries `b4_counterexample` alongside `postgres` and is deselected from the
canonical gate, the PostgreSQL tier included. It is red on purpose until step 4 exists, and STEP 4
REMOVES THE MARKER, NOT THE ASSERTIONS - the full contract is in the comment above the marker list
in `pytest.ini`, and `tests/unit/test_p015_b4_counterexample_marker_is_not_a_hiding_place.py` holds
it in place.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import insert, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.debt import Debt
from tests.p015_b4_support import (
    JOURNAL_MODULE,
    OPERATIONS_TABLE,
    World,
    drop_world,
    journal_api,
    missing_journal_tables,
    operation,
    refusal_of,
    seed_world,
    stored_debts,
    stored_entries,
    stored_operations,
    stored_rows,
)

pytestmark = [pytest.mark.postgres, pytest.mark.b4_counterexample]


def _identity(name: str) -> str:
    return f"p015-b4/{name}/{uuid.uuid4().hex[:12]}"


async def _open(api, session, world: World, name: str, **kw):
    return operation(
        api,
        session,
        kind=kw.pop("kind", "TEST_FIXTURE"),
        identity=kw.pop("identity", _identity(name)),
        intent=kw.pop("intent", {"source": "p015-b4-counterexample", "name": name}),
        scope_equivalent_ids=kw.pop("scope_equivalent_ids", frozenset({world.equivalent.id})),
        **kw,
    )


@pytest_asyncio.fixture
async def serializable_factory():
    """A sessionmaker over this module's own SERIALIZABLE engine with a real pool.

    `pool_size=3` and no overflow: the counterexamples below need at most three live backends at
    once (the one under test, a second independent writer, and the watcher that reads
    `pg_stat_activity`). `NullPool` is deliberately NOT used - a released connection would be closed,
    and "the transaction ended" could not be told from "the connection died", which is the very
    distinction C10 turns on.
    """
    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    await _ensure_schema_initialized()
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=3,
        max_overflow=0,
        pool_timeout=10,
        isolation_level="SERIALIZABLE",
    )
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def watcher():
    """A connection that is never the one under test, for reading `pg_stat_activity`."""
    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=10)

    async def _backend_state(pid: int) -> str | None:
        async with engine.connect() as connection:
            return (
                await connection.execute(
                    text("SELECT state FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
                )
            ).scalar_one_or_none()

    try:
        yield _backend_state
    finally:
        await engine.dispose()


# ==============================================================================================
# C2 on PostgreSQL - DML the statement does not look like
# ==============================================================================================


@pytest.mark.parametrize("form", ["dml cte in a select", "insert from select with a dml cte"])
@pytest.mark.asyncio
async def test_c2_p_a_debt_write_hidden_inside_a_cte_is_refused(serializable_factory, form):
    """C2, PostgreSQL, DEFECT-SHAPED. A statement that is not DML can still write `debts`.

    A guard that asks "is this statement an INSERT/UPDATE/DELETE?" answers False for the first form
    below: it is a `Select`, `is_dml` is False, and it writes a row. Design v2 §6 therefore collects
    `{el.table.name for el in visitors.iterate(clause) if isinstance(el, UpdateBase)}` - every
    `UpdateBase` anywhere in the tree - and this is the counterexample that makes the difference
    between the two readings visible.

    MEASURED ON THIS TREE, debug-only, before this test existed: a `before_execute` listener
    receiving the first form saw `('Select', False, {'debts'})`, so the recursive scan does see it
    and the cheap `is_dml` check does not. Both rows were durable.

    RED TODAY BECAUSE: both forms commit their rows with nothing refusing them.
    MUTATION once step 4 exists: replace the recursive scan with `clause.is_dml and
    clause.table.name in guarded`; the first form must go through again.
    """
    api = journal_api()
    world = await seed_world(serializable_factory, extra_participants=1)
    try:
        before = await stored_debts(serializable_factory, world)
        async with serializable_factory() as session:
            if form == "dml cte in a select":
                written = world.debt_values("41.00")
                cte = insert(Debt).values(**written).returning(Debt.id).cte("hidden_insert")
                statement = select(cte.c.id).add_cte(cte)
            else:
                seed = world.debt_values("43.00")
                cte = (
                    insert(Debt)
                    .values(**seed)
                    .returning(Debt.amount)
                    .cte("hidden_insert")
                )
                columns = Debt.__table__.c
                statement = insert(Debt).from_select(
                    ["id", "debtor_id", "creditor_id", "equivalent_id", "amount", "version"],
                    select(
                        literal(uuid.uuid4(), columns.id.type),
                        literal(world.debtor.id, columns.debtor_id.type),
                        literal(world.extra_participants[0].id, columns.creditor_id.type),
                        literal(world.equivalent.id, columns.equivalent_id.type),
                        cte.c.amount,
                        literal(0, columns.version.type),
                    ).add_cte(cte),
                )
            refusal = await refusal_of(api, session.execute(statement))
            if refusal is None:
                await session.commit()

        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY: the hidden DML really wrote. A statement that failed to run would otherwise
        # read as a refusal.
        assert after != before or refusal is not None, (
            f"stand: `{form}` neither changed `debts` nor was refused (before={before})"
        )

        # VERDICT.
        assert refusal is not None, (
            f"`{form}` wrote `debts` from inside a CTE and nothing refused it: {before} -> {after}. "
            f"The guard must look at every `UpdateBase` in the statement tree, not at the "
            f"statement's own kind ({JOURNAL_MODULE}, design v2 §6)."
        )
        assert after == before, f"`{form}` was refused but its rows are durable: {after}"
    finally:
        await drop_world(serializable_factory, world)


# ==============================================================================================
# C10 on PostgreSQL - what the SERVER says after a refusal
# ==============================================================================================


@pytest.mark.asyncio
async def test_c10_p_a_refused_root_commit_leaves_the_backend_idle(serializable_factory, watcher):
    """C10, root half, PostgreSQL, DEFECT-SHAPED. Read from the server, not from the adapter.

    WHY THE SERVER AND NOT THE ADAPTER (`review-round2-codex.md` §2). A successful
    `dialect.do_rollback` on asyncpg awaits `asyncpg.Transaction.rollback()` and then, in a
    `finally`, sets `_transaction = None` and `_started = False`. Those flags are the ADAPTER's
    bookkeeping: on a failure or a cancellation they can be cleared while the server-side
    transaction is still open, and the connection then goes back into the pool carrying it. Only
    `pg_stat_activity.state`, read on another connection, distinguishes the two.

    `idle` is the requirement. `idle in transaction` would mean the refused work is still holding
    locks and an xmin horizon, and the next checkout of that pooled connection would inherit it.

    RED TODAY BECAUSE: the commit is not refused at all and the incomplete operation's debt is
    stored. The backend-state assertion is written second so it cannot pass on a scenario in which
    nothing was refused.
    MUTATION once step 4 exists: raise from the Core `commit` event without rolling back first, or
    swallow a failure of that rollback instead of invalidating the connection.
    """
    api = journal_api()
    world = await seed_world(serializable_factory)
    try:
        refusal: BaseException | None = None
        backend_pid: int | None = None
        async with serializable_factory() as session:
            try:
                async with await _open(api, session, world, "incomplete"):
                    session.add(world.debt("15.00"))
                    await session.flush()
                    backend_pid = int(await session.scalar(text("SELECT pg_backend_pid()")))
                    # The bypass: commit the root while this operation is still OPEN.
                    await session.commit()
            except api.refusals as exc:  # noqa: B902
                refusal = exc
            state = await watcher(backend_pid) if backend_pid else None

        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY: the backend under test was really identified and really observed.
        assert backend_pid is not None and state is not None, (
            f"stand: the backend that ran the refused commit was not observed (pid={backend_pid}, "
            f"state={state!r})"
        )

        # VERDICT.
        assert refusal is not None, (
            f"the root was committed while a debt operation was still OPEN and nothing refused it: "
            f"the database holds {after}."
        )
        assert state == "idle", (
            f"after the refused commit the backend is {state!r}, not 'idle': the refusal left a "
            f"server-side transaction open, and the pooled connection carries it to its next user"
        )
        assert after == {}, f"the refused commit still stored the debt: {after}"
    finally:
        await drop_world(serializable_factory, world)


@pytest.mark.asyncio
async def test_c10_p_a_refused_release_leaves_the_backend_in_transaction(
    serializable_factory, watcher
):
    """C10, release half, PostgreSQL, DEFECT-SHAPED. The OPPOSITE state, and deliberately so.

    The round-2 reviewer split C10 here: "Отказ release через `ROLLBACK TO` намеренно оставляет root
    открытым и poisoned. Поэтому требование C10 'DBAPI tx not open' для всех отказов неверно". A
    release refusal must undo the savepoint and nothing else - a sibling operation's committed work
    in the same root must survive - so the backend must still be `idle in transaction`, and the root
    must refuse to commit afterwards.

    RED TODAY BECAUSE: the release is not refused, the root commits, and both debts are stored.
    MUTATION once step 4 exists: make a release refusal roll the ROOT back; this test then sees an
    idle backend, and a real staged-payment tick would lose every payment that had already
    committed when one of its siblings failed to complete.
    """
    api = journal_api()
    world = await seed_world(serializable_factory, extra_participants=1)
    try:
        release_refusal: BaseException | None = None
        commit_refusal: BaseException | None = None
        backend_pid: int | None = None
        async with serializable_factory() as session:
            async with await _open(api, session, world, "root"):
                session.add(world.debt("4.00"))
                await session.flush()
            backend_pid = int(await session.scalar(text("SELECT pg_backend_pid()")))

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
            state = await watcher(backend_pid)
            commit_refusal = await refusal_of(api, session.commit())

        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY.
        assert state is not None, "stand: the backend under test was not observed"

        # VERDICT.
        assert release_refusal is not None, (
            f"a savepoint was RELEASEd while the operation bound to it was still OPEN and nothing "
            f"refused it: the database holds {after}."
        )
        assert state == "idle in transaction", (
            f"after the refused release the backend is {state!r}: a release refusal must roll back "
            f"only to the savepoint and leave the root open and poisoned"
        )
        assert commit_refusal is not None, (
            f"the root committed after a refused release: {after}"
        )
        assert after == {}, f"the refused release left durable rows: {after}"
    finally:
        await drop_world(serializable_factory, world)


# ==============================================================================================
# C7 on PostgreSQL - the durable rollback/reopen obligation the reviewer would not let go
# ==============================================================================================


@pytest.mark.asyncio
async def test_c7_p_a_rolled_back_operation_leaves_nothing_and_the_identity_reopens_clean(
    serializable_factory,
):
    """C7, PostgreSQL, API-SHAPED. The same property as on SQLite, on the tier that owns durability.

    The round-1 review made this a durable obligation and the round-2 review noticed it had become
    SQLite-only. It belongs here because `UNIQUE(kind, identity)` (design v2 §5) is what makes a
    reopen after a rollback either work or fail, and a unique index is a database behaviour: the
    SQLite tier can show the rows are gone, only a real backend shows that the constraint agrees.

    RED TODAY BECAUSE: `debt_operations` does not exist.
    MUTATION once step 4 exists: keep the registry entry across a real root rollback, so the reopen
    hits the UNIQUE of the first, rolled back, envelope.
    """
    api = journal_api()
    world = await seed_world(serializable_factory)
    identity = _identity("reopen")
    try:
        async with serializable_factory() as session:
            async with await _open(api, session, world, "reopen", identity=identity):
                session.add(world.debt("19.00"))
                await session.flush()
            await session.rollback()

        reopen_error: BaseException | None = None
        async with serializable_factory() as session:
            try:
                async with await _open(api, session, world, "reopen", identity=identity):
                    session.add(world.debt("23.00"))
                    await session.flush()
                await session.commit()
            except Exception as exc:  # recorded, asserted below
                reopen_error = exc

        after = await stored_debts(serializable_factory, world)
        envelopes = await stored_operations(serializable_factory, identity)
        entries = await stored_entries(serializable_factory, identity)

        # NON-VACUITY: the rollback really discarded the first attempt and the second really wrote.
        assert after == {("debtor", "creditor", "eq"): Decimal("23.00000000")}, (
            f"stand: the two attempts did not leave exactly the second one's debt: {after}"
        )
        assert reopen_error is None, f"reopening the identity after a rollback failed: {reopen_error!r}"

        # VERDICT.
        assert envelopes is not None, missing_journal_tables(envelopes, OPERATIONS_TABLE)
        assert [row["state"] for row in envelopes] == ["COMPLETED"], envelopes
        assert entries and len(entries) == 1, (
            f"the journal entries do not describe exactly the surviving attempt: {entries}"
        )
    finally:
        await drop_world(serializable_factory, world)


# ==============================================================================================
# C9 on PostgreSQL - two genuinely independent transactions
# ==============================================================================================


@pytest.mark.asyncio
async def test_c9_p_an_operation_does_not_cover_an_independent_transactions_write(
    serializable_factory,
):
    """C9, PostgreSQL, DEFECT-SHAPED. The variant SQLite cannot host.

    Two backends, two real transactions. Session A holds an open operation; session B writes a debt
    of its own on a different edge and commits it. Nothing about A's operation may license that.
    On SQLite this shape ends in "database is locked" before the property is reached - since T1525
    a transaction that has written holds the write lock - so the SQLite version of C9 shares one
    connection and this one exists to cover what that cannot.

    RED TODAY BECAUSE: session B's debt commits with nothing refusing it.
    DEGENERATE FORM: with no journal there is no operation on A either, so today's red says the
    weaker "an uncovered write was not refused".
    MUTATION once step 4 exists: key the registry by process or by session identity map rather than
    by the Core root transaction of the connection actually doing the writing.
    """
    api = journal_api()
    world = await seed_world(serializable_factory, extra_participants=1)
    try:
        async with serializable_factory() as session_a, serializable_factory() as session_b:
            async with await _open(api, session_a, world, "session-a"):
                session_a.add(world.debt("7.00"))
                await session_a.flush()

                session_b.add(
                    world.debt("31.00", creditor_id=world.extra_participants[0].id)
                )
                refusal = await refusal_of(api, session_b.flush())
                if refusal is None:
                    await session_b.commit()
            await session_a.commit()

        after = await stored_debts(serializable_factory, world)

        # NON-VACUITY: session A's own covered write really committed from its own backend.
        assert after.get(("debtor", "creditor", "eq")) == Decimal("7.00000000"), (
            f"stand: session A's write did not commit, so the two transactions did not both run: "
            f"{after}"
        )

        # VERDICT.
        assert refusal is not None, (
            f"session B committed a Debt from an independent transaction while the only open "
            f"operation belonged to session A, and nothing refused it: {after}."
        )
        assert after.get(("debtor", "extra0", "eq")) is None, (
            f"session B's uncovered debt is durable: {after}"
        )
    finally:
        await drop_world(serializable_factory, world)


# ==============================================================================================
# Binding condition 2 - roots whose completion never reaches the Core `commit` event
# ==============================================================================================


@pytest.mark.asyncio
async def test_condition2_p_an_autocommit_root_is_refused_before_the_operation_opens(
    serializable_factory,
):
    """Binding condition 2, DEFECT-SHAPED for the trap, API-SHAPED for the verdict.

    WHY THIS EXISTS. The design detects AUTOCOMMIT by reading `Connection._execution_options`. The
    round-2 reviewer showed that this is blind to `create_engine(isolation_level="AUTOCOMMIT")`: the
    option is consumed on connect (`sqlalchemy/engine/base.py:1041`) and the connection's execution
    options stay empty. An operation opened on such a root can never be refused at commit, because
    there is no commit - every statement is durable as it runs, the journal's own envelope included,
    and no rollback can take any of it back.

    WHY IT IS A POSTGRESQL TEST even though condition 2 names no tier. On SQLite a
    `create_async_engine(url, isolation_level="AUTOCOMMIT")` cannot exist in this repository:
    `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py` requires every SQLite
    engine construction to be paired with `install_sqlite_transaction_control`, and a controlled
    engine is not in AUTOCOMMIT - the control's `begin` listener sends a real `BEGIN`. Weakening that
    guard to host a counterexample would be trading a live money protection for a test.

    THE TWO MEASUREMENTS BELOW ARE GREEN TODAY and are the point of the test: they prove the trap is
    real on this tree, so the verdict cannot be satisfied by the blind check.
    MUTATION once step 4 exists: detect AUTOCOMMIT from `Connection._execution_options` alone
    (design v2 §1.4 as written); the engine-level form must then slip through.
    """
    from tests.conftest import TEST_DATABASE_URL

    api = journal_api()
    world = await seed_world(serializable_factory)
    autocommit_engine = create_async_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    autocommit_factory = async_sessionmaker(
        bind=autocommit_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    try:
        async with autocommit_engine.connect() as connection:
            execution_options = dict(connection.sync_connection._execution_options)
        dialect_level = getattr(
            autocommit_engine.sync_engine.dialect, "_on_connect_isolation_level", None
        )

        refusal: BaseException | None = None
        async with autocommit_factory() as session:
            try:
                async with await _open(api, session, world, "autocommit"):
                    session.add(world.debt("3.00"))
                    await session.flush()
                    await session.rollback()
            except api.refusals as exc:  # noqa: B902
                refusal = exc

        durable = await stored_debts(serializable_factory, world)

        # NON-VACUITY 1: the naive check really is blind here.
        assert execution_options.get("isolation_level") is None, (
            f"stand: `_execution_options` did see the isolation level ({execution_options}), so "
            f"this engine is not the blind case the reviewer described"
        )
        assert dialect_level == "AUTOCOMMIT", (
            f"stand: the engine is not in AUTOCOMMIT ({dialect_level!r}), so nothing here is about "
            f"an AUTOCOMMIT root"
        )
        # NON-VACUITY 2: AUTOCOMMIT is really in effect - a write that was rolled back stayed.
        assert durable.get(("debtor", "creditor", "eq")) == Decimal("3.00000000"), (
            f"stand: the write was not durable after a rollback, so this connection is not in "
            f"AUTOCOMMIT after all: {durable}"
        )

        # VERDICT.
        assert refusal is not None, (
            f"a debt operation opened on an AUTOCOMMIT root and its debt became durable the moment "
            f"it was flushed ({durable}). There is no commit to refuse at and no rollback to undo "
            f"the journal's own envelope, so such a root must be refused before the operation opens."
        )
    finally:
        await autocommit_engine.dispose()
        await drop_world(serializable_factory, world)


@pytest.mark.asyncio
async def test_condition2_p_a_two_phase_root_is_refused_before_the_operation_opens(
    serializable_factory,
):
    """Binding condition 2, PostgreSQL, DEFECT-SHAPED for the trap, API-SHAPED for the verdict.

    WHAT THE REVIEWER FOUND (`review-round2-codex.md` §2). `TwoPhaseTransaction` is a subclass of
    `RootTransaction`, so a registry keyed by `RootTransaction` accepts it - but it completes through
    `commit_twophase`, not through `commit` (`sqlalchemy/engine/base.py:2863`, `:2900`, `:1200`;
    `sqlalchemy/dialects/postgresql/base.py:3217`). The Core `commit` event the whole refusal
    mechanism hangs on never fires, so an incomplete operation in such a root is never refused. The
    reviewer's own conclusion is that supporting two-phase is not required: refusing such a root
    before the operation opens is enough, and that is what this asserts.

    SQLite cannot carry this counterexample: `begin_twophase()` there raises `NotImplementedError`
    (measured on this tree). This is why condition 2's third clause is a PostgreSQL test even though
    the condition itself names no tier.

    NOTHING IS PREPARED, so no prepared transaction can be left behind: `do_begin_twophase` only
    begins; `PREPARE TRANSACTION` is `do_prepare_twophase`, which is never reached. The final
    assertion checks `pg_prepared_xacts` anyway - a test that leaks a prepared transaction would
    block VACUUM in the test database for everyone.

    RED TODAY BECAUSE: no operation can be opened, so nothing refuses the root. The non-vacuity
    check that the root really IS two-phase comes first.
    MUTATION once step 4 exists: accept any `RootTransaction` at open time; the refusal at commit
    then never fires for this root.
    """
    from tests.conftest import TEST_DATABASE_URL

    api = journal_api()
    world = await seed_world(serializable_factory)
    engine = create_async_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=10)
    try:
        refusal: BaseException | None = None
        root_kinds: tuple[str, ...] = ()
        async with engine.connect() as connection:
            root_kinds = tuple(
                await connection.run_sync(
                    lambda sync_connection: tuple(
                        base.__name__
                        for base in type(sync_connection.begin_twophase()).__mro__
                    )
                )
            )
            two_phase = async_sessionmaker(
                bind=connection, class_=AsyncSession, expire_on_commit=False, autoflush=False
            )
            async with two_phase() as session:
                try:
                    async with await _open(api, session, world, "two-phase"):
                        session.add(world.debt("47.00"))
                        await session.flush()
                except api.refusals as exc:  # noqa: B902
                    refusal = exc
            # Nothing was prepared, so dropping the connection ends the server-side transaction.
            await connection.invalidate()

        after = await stored_debts(serializable_factory, world)
        prepared = await stored_rows(serializable_factory, "SELECT gid FROM pg_prepared_xacts")

        # NON-VACUITY: the root really was a two-phase root AND really was a `RootTransaction`,
        # which is the whole reason the registry accepts it.
        assert root_kinds[:2] == ("TwoPhaseTransaction", "RootTransaction"), (
            f"stand: `begin_twophase()` did not produce a two-phase root that is also a "
            f"RootTransaction: {root_kinds}"
        )

        # VERDICT.
        assert refusal is not None, (
            f"a debt operation opened in a TWO-PHASE root and nothing refused it (durable rows "
            f"afterwards: {after}). Such a root completes through `commit_twophase`, so the Core "
            f"`commit` "
            f"event that carries every refusal never fires and an incomplete operation can never "
            f"be refused. The root must be refused before the operation opens."
        )
        assert after == {}, f"the two-phase root left durable rows: {after}"
        assert prepared == [], f"the stand left a prepared transaction behind: {prepared}"
    finally:
        await engine.dispose()
        await drop_world(serializable_factory, world)
