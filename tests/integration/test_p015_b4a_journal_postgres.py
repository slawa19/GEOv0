"""Programme 015, step 4 slice A: the journal's mechanism on PostgreSQL.

WHAT IS HERE AND WHY NOT IN THE DEFAULT TIER. Only what SQLite cannot answer, with the reason
stated per test rather than "for coverage":

* AUTOCOMMIT. The trap is `create_async_engine(url, isolation_level="AUTOCOMMIT")`, which leaves
  `Connection._execution_options` EMPTY - the form the round-2 reviewer showed a check on those
  options cannot see. SQLite cannot host it at all: every SQLite engine in this repository must
  carry `install_sqlite_transaction_control`
  (`tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py`), and an engine
  carrying the control is not in AUTOCOMMIT, because the control's `begin` listener sends a real
  `BEGIN`. The two demands are genuinely incompatible and the guard that protects money is the one
  that stays.
* A TWO-PHASE ROOT. `Connection.begin_twophase()` on the pysqlite/aiosqlite dialect raises
  `NotImplementedError`; on PostgreSQL a `TwoPhaseTransaction` really is a `RootTransaction`
  subclass whose second phase happens after this process stopped watching.
* DML HIDDEN IN A CTE. SQLite has no data-modifying CTEs, so the door the write guard must see
  through does not exist there.
* `NaN` REACHING THE MONEY COLUMN. On SQLite a bound `NaN` becomes `NULL` before any predicate sees
  it (T1526), so the CHECK constraints on the journal's own money columns can only be measured
  here - and the measurement is the whole reason those columns carry `MoneyNumeric` as well.
* EXACT MONEY AT FULL COLUMN WIDTH. `999999999999.99999999` is outside the domain SQLite
  round-trips (design v2 §4 rule 1: PostgreSQL is the money-acceptance tier).

THE STAND. Its own SERIALIZABLE engine with a real pool, its own sync `Session` subclass, and the
journal armed on both - never `db_session`, whose outer transaction hides the commit boundaries
these tests are about, and never the shared application engine, because slice A changes no
behaviour anywhere else. Every verdict is read on a NEW session, and the stand purges what it
created.

Each test names, in its docstring, the mutation that must turn it red again.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import insert, literal, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.ledger import journal
from app.db.journal_tables import debt_journal_entries
from app.db.models.debt import Debt
from tests.p015_b4a_stand import Stand, arm_stand, identity

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def stand():
    """This module's own SERIALIZABLE engine with a real pool, and the journal armed on it.

    THE ENGINE IS BUILT HERE, NEXT TO ITS REFUSAL, and not in `tests/p015_b4a_stand.py`. Every
    SQLite-capable engine construction in this repository must be paired with
    `install_sqlite_transaction_control` (T1525,
    `tests/unit/test_p015_t1525_every_sqlite_engine_has_transaction_control.py`); a construction
    that can only ever be PostgreSQL is exempt only where a refusal in the same module says so.
    The `pytest.skip` below is that refusal, and it belongs in a postgres-marked module rather than
    in a shared helper where it would be one indirection away from the thing it protects.

    `NullPool` is deliberately not used: a released connection would be CLOSED, and "the
    transaction ended" could not be told from "the connection died" - a distinction the refusal
    tests here turn on.
    """

    from tests.conftest import TEST_DATABASE_URL, _ensure_schema_initialized

    if "postgresql" not in TEST_DATABASE_URL:
        pytest.skip(f"this module needs a PostgreSQL TEST_DATABASE_URL, got {TEST_DATABASE_URL!r}")
    await _ensure_schema_initialized()
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_size=4,
        max_overflow=0,
        pool_timeout=15,
        isolation_level="SERIALIZABLE",
    )
    built = await arm_stand(engine, extra_participants=1)
    try:
        yield built
    finally:
        await built.close(purge=True)


@pytest.mark.asyncio
async def test_an_operation_records_full_width_money_exactly(stand: Stand) -> None:
    """Design v2 §4 rule 1: PostgreSQL is the money-acceptance tier, with exact integer atoms.

    `999999999999.99999999` is the largest value `NUMERIC(20, 8)` holds. It must survive the debt,
    the entry and the read-back byte for byte - and the delta of the update below is computed from
    it, so a journal that rounded anywhere would show up here rather than in a tolerance.

    MUTATION that must redden this: quantize the amounts to fewer places in `_effects_of_flush`,
    or store `float(value)` in the entry rows.
    """

    ident = identity("full-width")
    largest = Decimal("999999999999.99999999")
    reduced = Decimal("999999999998.99999999")
    async with stand.factory() as session:
        async with stand.operation("full-width", session=session, identity=ident):
            debt = stand.debt("0", raw_amount=largest)
            session.add(debt)
            await session.flush()
            debt.amount = reduced
            await session.flush()
        await session.commit()

    stored = await stand.stored_debts()
    entries = await stand.entries(ident)

    # NON-VACUITY: the money really is at full column width, so this is not a test about 10.00.
    assert stored == {("debtor", "creditor", "eq"): reduced}, stored

    assert [
        (row["effect"], row["amount_before"], row["amount_after"], row["delta"]) for row in entries
    ] == [
        ("I", None, largest, largest),
        ("U", largest, reduced, Decimal("-1.00000000")),
    ], entries


@pytest.mark.asyncio
async def test_an_operation_refuses_to_open_on_an_autocommit_root(stand: Stand) -> None:
    """Binding condition 2. AUTOCOMMIT must be detected in the form that hides from the options.

    `create_async_engine(url, isolation_level="AUTOCOMMIT")` stores the level on the DIALECT and
    leaves `Connection._execution_options` empty - asserted below, because a test that only showed
    the refusal could not tell a correct check from one that happened to work. In AUTOCOMMIT the
    envelope, the entries and the debts are three separate durable facts and a refusal can undo
    none of them, so this is a refusal at the door and not a warning.

    MUTATION that must redden this: in `_is_autocommit`, read only
    `conn._execution_options.get("isolation_level")` and drop the dialect fallback.
    """

    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(
        TEST_DATABASE_URL, pool_size=1, max_overflow=0, isolation_level="AUTOCOMMIT"
    )
    # The stand's session class, not a second one: class-level Session events share one dispatch
    # across the whole `Session` hierarchy, so two subclasses carrying the same handler cannot both
    # be uninstalled (see `journal.install_flush_hook`). Only the engine half is new here anyway.
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        sync_session_class=stand.session_class,
        autoflush=False,
    )
    journal.install_write_guard(engine)
    try:
        async with factory() as session:
            connection = (await session.connection()).sync_connection
            options = dict(connection._execution_options)
            dialect_level = getattr(connection.engine.dialect, "_on_connect_isolation_level", None)
            with pytest.raises(journal.DebtJournalError) as refusal:
                async with journal.debt_operation(
                    session, kind="SEED", identity=identity("autocommit"), intent={}
                ):
                    pass
            await session.rollback()
    finally:
        journal.uninstall_write_guard(engine)
        await engine.dispose()

    # NON-VACUITY: this is the shape that hides from the execution options, which is the whole
    # reason the check cannot be written against them.
    assert options.get("isolation_level") is None, (
        f"stand: this engine's AUTOCOMMIT IS visible in the execution options ({options}), so the "
        f"refusal below does not prove the dialect fallback is needed"
    )
    assert dialect_level == "AUTOCOMMIT", (
        f"stand: the dialect does not report AUTOCOMMIT either ({dialect_level!r}); this stand is "
        f"not in AUTOCOMMIT at all"
    )
    assert refusal.value.reason == journal.Reason.AUTOCOMMIT_ROOT, refusal.value


@pytest.mark.asyncio
async def test_an_operation_refuses_to_open_on_a_two_phase_root(stand: Stand) -> None:
    """Binding condition 2. A two-phase root commits somewhere this process is not watching.

    The journal's promise is "the record and the money commit together". A prepared transaction's
    second phase can happen minutes later, from another process, so that promise is not one this
    module can keep here - and saying so at the door is the only honest option.

    MUTATION that must redden this: drop the `TwoPhaseTransaction` branch in
    `_refuse_unusable_transaction`.
    """

    from sqlalchemy.engine.base import TwoPhaseTransaction

    factory = async_sessionmaker(
        bind=stand.engine,
        class_=AsyncSession,
        sync_session_class=stand.session_class,
        autoflush=False,
        twophase=True,
    )
    async with factory() as session:
        root = (await session.connection()).sync_connection.get_transaction()
        # NON-VACUITY: the root really is two-phase, and it really is a `RootTransaction`, so the
        # refusal below is about that shape and not about this stand being unusable.
        assert isinstance(root, TwoPhaseTransaction), (
            f"stand: the root is {type(root).__name__}, not a two-phase transaction, so the "
            f"refusal below would be about something else"
        )
        with pytest.raises(journal.DebtJournalError) as refusal:
            async with journal.debt_operation(
                session, kind="SEED", identity=identity("two-phase"), intent={}
            ):
                pass
        await session.rollback()

    assert refusal.value.reason == journal.Reason.TWO_PHASE_ROOT, refusal.value


@pytest.mark.asyncio
async def test_the_write_guard_sees_dml_hidden_in_a_cte(stand: Stand) -> None:
    """C2 on PostgreSQL. A SELECT that writes is still a write.

    `select(...).add_cte(insert(Debt)...)` answers False to `is_dml` and reaches the database all
    the same, which is why the guard walks the whole statement instead of looking at
    `clause.table`.

    MUTATION that must redden this: replace `_dml_tables`'s `visitors.iterate` walk with
    `{clause.table.name} if isinstance(clause, UpdateBase) else set()`.
    """

    before = await stand.stored_debts()
    refusals = {}
    async with stand.factory() as session:
        writing_cte = (
            insert(Debt)
            .values(**stand.debt_values("55.00", creditor_id=stand.extra_ids[0]))
            .returning(Debt.id)
            .cte("writer")
        )
        try:
            await session.execute(select(literal(1)).add_cte(writing_cte))
        except journal.DebtJournalError as exc:
            refusals["select_with_writing_cte"] = exc.reason

        updating_cte = (
            update(Debt).values(amount=Decimal("66.00000000")).returning(Debt.id).cte("updater")
        )
        try:
            await session.execute(select(updating_cte.c.id))
        except journal.DebtJournalError as exc:
            refusals["select_from_writing_cte"] = exc.reason
        await session.rollback()

    after = await stand.stored_debts()

    # NON-VACUITY: a SELECT with a READ-ONLY cte passes through untouched, so the refusals above
    # are about the DML inside the cte and not about CTEs.
    async with stand.factory() as session:
        reading_cte = select(Debt.id).cte("reader")
        rows = (await session.execute(select(reading_cte.c.id))).all()
        await session.rollback()

    assert refusals == {
        "select_with_writing_cte": journal.Reason.UNVERIFIED_DEBT_WRITE,
        "select_from_writing_cte": journal.Reason.UNVERIFIED_DEBT_WRITE,
    }, refusals
    assert rows == [], f"stand: the read-only CTE control found rows it should not have: {rows}"
    assert after == before == {}, (after, before)


@pytest.mark.asyncio
async def test_nan_is_refused_by_the_hook_and_would_be_refused_by_the_column_too(
    stand: Stand,
) -> None:
    """T1526 on the tier where `NaN` actually reaches a `NUMERIC` column.

    Three separate facts, because they are three different mechanisms and the programme exists
    because they were once taken for one:

    1. The hook refuses `NaN` by FINITENESS, before any statement is built.
    2. `MoneyNumeric` refuses the bind, so the value never leaves the process even through a Core
       insert into the journal's own tables.
    3. The journal's CHECK constraint refuses it at the database - through the MAGNITUDE clause,
       because `'NaN' > 0` is TRUE on PostgreSQL and only an upper bound is FALSE for it. That is
       asserted here directly against the database, so the claim "the CHECKs exclude NaN" is
       measured rather than reasoned.

    MUTATION that must redden this: drop the magnitude clause from
    `chk_debt_journal_entries_delta`, leaving positivity - and fact 3 stops holding while facts 1
    and 2 still do, which is exactly the silent hole T1526 closed on `debts.amount`.
    """

    async with stand.factory() as session:
        with pytest.raises(journal.DebtJournalError) as by_the_hook:
            async with stand.operation("nan", session=session):
                session.add(stand.debt("0", raw_amount=Decimal("NaN")))
                await session.flush()
        await session.rollback()
    assert by_the_hook.value.reason == journal.Reason.MONEY_FINITENESS, by_the_hook.value

    async with stand.engine.connect() as connection:
        # Fact 3, measured on the database: which clause of the constraint is the one that refuses.
        positive = await connection.scalar(text("SELECT 'NaN'::numeric(20,8) > 0"))
        bounded = await connection.scalar(
            text("SELECT abs('NaN'::numeric(20,8)) <= 999999999999.99999999")
        )
        stored_definition = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'chk_debt_journal_entries_delta'"
            )
        )

    assert positive is True, (
        "stand: PostgreSQL no longer orders NaN above every number, so the reason this constraint "
        "is written with a magnitude bound has changed and must be re-derived"
    )
    assert bounded is False, (
        "stand: the magnitude clause does NOT exclude NaN here, so the journal's money CHECKs have "
        "no mechanism against it at all"
    )
    assert stored_definition is not None and "abs" in stored_definition.lower(), (
        f"the delta constraint as the database stores it no longer carries the magnitude bound "
        f"that is what excludes NaN: {stored_definition!r}"
    )


@pytest.mark.asyncio
async def test_the_journal_tables_refuse_a_forged_row_at_the_database(stand: Stand) -> None:
    """C19 on PostgreSQL: the CHECKs close the shapes a raw writer could forge.

    `exec_driver_sql` is the guard's one documented blind spot, so what stands between a raw
    statement and a nonsensical entry is the database. Shape-VALID forgeries are NOT closed here
    and are a step-6 verifier item - a stated boundary, not a silence.

    MUTATION that must redden this: drop `chk_debt_journal_entries_shape`.
    """

    ident = identity("forge-pg")
    async with stand.factory() as session:
        async with stand.operation("forge-pg", session=session, identity=ident):
            session.add(stand.debt("0", raw_amount=Decimal("70.00000000")))
            await session.flush()
        await session.commit()
    operation_id = (await stand.envelopes(ident))[0]["id"]

    refused = {}
    async with stand.engine.connect() as connection:
        for name, values in (
            ("insert_with_before", "'I', 1.0, 2.0, 1.0"),
            ("update_that_changed_nothing", "'U', 2.0, 2.0, 1.0"),
            ("zero_delta", "'U', 2.0, 3.0, 0"),
            ("nan_delta", "'U', 2.0, 3.0, 'NaN'"),
        ):
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql(
                    "INSERT INTO debt_journal_entries (id, operation_id, flush_ordinal, "
                    "equivalent_id, debtor_id, creditor_id, effect, amount_before, amount_after, "
                    f"delta) VALUES ('{uuid.uuid4()}', '{operation_id}', 9, "
                    f"'{stand.equivalent_id}', '{stand.debtor_id}', '{stand.extra_ids[0]}', "
                    f"{values})"
                )
            except Exception as exc:  # noqa: BLE001 - the database's refusal is the subject
                refused[name] = type(exc).__name__
            finally:
                await transaction.rollback()

        # NON-VACUITY: a well-shaped row through the same raw path IS accepted.
        transaction = await connection.begin()
        await connection.exec_driver_sql(
            "INSERT INTO debt_journal_entries (id, operation_id, flush_ordinal, equivalent_id, "
            "debtor_id, creditor_id, effect, amount_before, amount_after, delta) VALUES "
            f"('{uuid.uuid4()}', '{operation_id}', 9, '{stand.equivalent_id}', "
            f"'{stand.debtor_id}', '{stand.extra_ids[0]}', 'U', 2.0, 3.0, 1.0)"
        )
        accepted = await connection.scalar(
            select(debt_journal_entries.c.delta).where(
                debt_journal_entries.c.flush_ordinal == 9
            )
        )
        await transaction.rollback()

    assert accepted == Decimal("1.00000000"), (
        f"stand: the well-shaped control row was refused too ({accepted!r}), so the refusals above "
        f"are not about the shapes"
    )
    assert set(refused) == {
        "insert_with_before",
        "update_that_changed_nothing",
        "zero_delta",
        "nan_delta",
    }, refused
