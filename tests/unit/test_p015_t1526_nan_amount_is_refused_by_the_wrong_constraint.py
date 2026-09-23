"""T1526, the SQLite tier: `NaN` is refused here today, and by a constraint about a DIFFERENT rule.

WHAT THIS MODULE IS. The SQLite half of T1526. Its PostgreSQL sibling is
`tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py`, where a `NaN`
amount is genuinely STORED. Here the outcome looks right and the reason is wrong, which is a
different defect and needs its own test.

WHAT WAS MEASURED HERE (2026-09-12, `sqlite+aiosqlite`, schema from `create_all`):

    Debt(amount=Decimal("NaN"))  ->  IntegrityError: NOT NULL constraint failed: debts.amount

Nothing objected to the value. `sqlite3` cannot bind a float `NaN` at all: it converts it to SQL
`NULL` (measured in `test_b` below), and `NOT NULL` - a constraint about whether an amount was
SUPPLIED - is what refuses it. Three things follow, and the third is why this is a defect rather
than luck:

1. The error message names the wrong rule, so whoever hits it goes looking for a missing value.
2. The refusal is a property of the DRIVER, not of this system's money rules. It survives only as
   long as `sqlite3` keeps coercing; nothing in this tree asserts it.
3. IT DOES NOT TRANSFER. The same `Decimal("NaN")` on PostgreSQL is stored, because there the
   driver CAN send it. A refusal that exists on the tier that does not hold the money and is
   absent on the tier that does is not a money rule at all.

AND A CHECK CONSTRAINT COULD NOT FIX IT HERE, which is the measurement `test_b` exists for: on
SQLite a comparison against `NaN` evaluates to `NULL`, and a `CHECK` whose expression is `NULL` is
SATISFIED. So the DDL guard that closes this hole on PostgreSQL is structurally incapable of
closing it on SQLite, and the explicit refusal on this tier has to come from the bind layer -
`MoneyNumeric` in `app/db/types.py`.

RED TODAY: `test_a`. `test_b` is a MEASUREMENT of the dialect, green before and after; it is here
because it is the evidence for the design choice above, and because it turns red the day someone
tries to replace the bind-layer guard on this tier with a `CHECK`.

THE MUTATION that must turn `test_a` red again: remove `MoneyNumeric` from `Debt.amount` in
`app/db/models/debt.py` - the refusal falls back to `NOT NULL` and the assertion below quotes it.

THE STAND IS THIS MODULE'S OWN SQLITE DATABASE, NOT THE TIER'S (programme 017, `T1702`, 2026-09-23).
Both tests are statements about SQLite and nothing else, so they used to be true only while the
default tier happened to run on SQLite. On a PostgreSQL tier `test_b` could not even be spelled
(`typeof(?)` is SQLite), and `test_a` passed there while measuring PostgreSQL - and, worse, left its
seeded debt committed in the tier database, where it turned neighbours' global debt counts red
(`specs/017-postgres-only-engine/stage2-catalogue.md`, 9.6). Each test now builds a disposable SQLite
file under its own `tmp_path`, with exactly what the SQLite tier engine gets on connect - the
conftest's pragmas and the T1525 transaction control - and a schema from `create_all`, as that tier
has. The assertions are unchanged; what changed is only that "this tier" now always means SQLite,
whatever `TEST_DATABASE_URL` is, and that nothing is written to the tier database at all. The module
goes away with SQLite in stage 3.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.sqlite_transaction_control import install_sqlite_transaction_control

from tests.debt_setup import debt_fixture_setup
from tests.scratch_db import install_test_sqlite_pragmas


@pytest_asyncio.fixture
async def sqlite_stand(tmp_path):
    """`(engine, sessionmaker)` over a fresh SQLite file, shaped like the SQLite tier's engine.

    Same connect-time pragmas (`tests/scratch_db.py`), same transaction control, same
    `create_all` schema and the same sessionmaker options as `tests.conftest.TestingSessionLocal`
    minus the savepoint join, which only matters for a fixture-owned outer transaction and there is
    none here.
    """
    url = f"sqlite+aiosqlite:///{(tmp_path / 't1526.db').as_posix()}"
    stand_engine = create_async_engine(url, poolclass=NullPool, connect_args={"timeout": 30})
    install_test_sqlite_pragmas(stand_engine.sync_engine, url=url)
    install_sqlite_transaction_control(stand_engine.sync_engine)
    async with stand_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=stand_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    try:
        yield stand_engine, factory
    finally:
        await stand_engine.dispose()


async def _seed(factory):
    """One equivalent, two participants and one ordinary debt, committed on their own session."""
    n = uuid.uuid4().hex[:8]
    async with factory() as session:
        equivalent = Equivalent(code=f"NAN{n}".upper()[:16], precision=2, is_active=True)
        debtor = Participant(
            pid=f"NAND_{n}", display_name="Debtor", public_key=f"pk_nand_{n}",
            type="person", status="active", profile={},
        )
        creditor = Participant(
            pid=f"NANC_{n}", display_name="Creditor", public_key=f"pk_nanc_{n}",
            type="person", status="active", profile={},
        )
        session.add_all([equivalent, debtor, creditor])
        await session.flush()
        async with debt_fixture_setup(session, label="setup"):
            session.add(
                Debt(
                    debtor_id=debtor.id,
                    creditor_id=creditor.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("5"),
                )
            )
        await session.commit()
        return equivalent.id, debtor.id, creditor.id


async def _amounts(factory, equivalent_id) -> list[str]:
    """The amounts the DATABASE holds for this equivalent, read on a NEW session."""
    async with factory() as fresh:
        rows = (
            await fresh.execute(select(Debt.amount).where(Debt.equivalent_id == equivalent_id))
        ).scalars().all()
    return [str(value) for value in rows]


@pytest.mark.asyncio
async def test_a_the_refusal_of_a_nan_amount_must_name_the_money_rule(sqlite_stand):
    """RED TODAY: the refusal is `NOT NULL constraint failed: debts.amount`.

    Every write below goes through its own session on the module's SQLite stand, so that
    "committed" and "never written" cannot be confused.
    """
    engine, factory = sqlite_stand
    assert engine.dialect.name == "sqlite", engine.dialect.name

    equivalent_id, debtor_id, creditor_id = await _seed(factory)

    # NON-VACUITY: this tier really stores money, so a refusal below is about the VALUE.
    assert await _amounts(factory, equivalent_id) == ["5.00000000"], (
        f"the stand could not store an ordinary debt "
        f"({await _amounts(factory, equivalent_id)!r}), so it "
        f"cannot tell a refusal from a broken stand"
    )

    # THE DEBT JOURNAL STANDS DOWN FOR THIS WRITE, and it must. Armed (step 4 slice C), the journal
    # refuses a non-finite amount by its OWN finiteness predicate, before any SQL - so `MoneyNumeric`,
    # the guard in `app/db/types.py` that this test exists to hold in place, would never be reached
    # and its MUTATION ("remove `MoneyNumeric` from `Debt.amount`") would leave the test green. A
    # test screened by a second guard is a test that has stopped measuring its subject.
    #
    # This is a real consequence of arming the journal and is recorded as such: on every path that
    # goes through an operation, the journal's money predicates run first and the column's own
    # refusal is the SECOND line, not the first. Both are wanted (`AGENTS.md` §9: every stage correct
    # by itself); only one of them can be observed at a time, and this file observes the column's.
    from app.core.ledger import journal

    refusal: BaseException | None = None
    journal.uninstall_write_guard(engine)
    try:
        async with factory() as session:
            async with debt_fixture_setup(session, label="setup"):
                session.add(
                    Debt(
                        debtor_id=creditor_id,
                        creditor_id=debtor_id,
                        equivalent_id=equivalent_id,
                        amount=Decimal("NaN"),
                    )
                )
            try:
                await session.commit()
            except (StatementError, IntegrityError, ValueError) as exc:
                refusal = exc
                await session.rollback()
    finally:
        journal.install_write_guard(engine)

    stored = await _amounts(factory, equivalent_id)
    assert stored == ["5.00000000"], (
        f"a NaN amount reached the column on this tier too: {stored!r}"
    )
    assert refusal is not None, "the write was not refused at all"

    message = str(refusal)
    assert "NOT NULL constraint failed" not in message, (
        f"the refusal comes from the WRONG CONSTRAINT: {message!r}. `NOT NULL` says an amount was "
        f"not supplied; one WAS supplied, and it is not a number. What refuses this write today is "
        f"the sqlite3 driver coercing a float NaN to SQL NULL (see test_b), i.e. an accident of "
        f"the dialect rather than a rule of this money core - and the same value is STORED on "
        f"PostgreSQL, where the driver can send it."
    )
    assert "non-finite" in message.lower(), (
        f"the refusal does not say what is wrong with the value: {message!r}. It must name the "
        f"value's own defect - that it is not a finite number - so the next reader is not sent "
        f"looking for a missing field."
    )


@pytest.mark.asyncio
async def test_b_a_check_constraint_on_this_tier_can_never_refuse_a_nan(sqlite_stand):
    """MEASUREMENT, green before and after: why the SQLite fix cannot be a CHECK constraint.

    Two facts of this dialect, both measured against the real driver rather than recalled:

    * a float `NaN` bound as a parameter arrives as SQL `NULL`;
    * every comparison against that value yields `NULL`, and SQLite's `CHECK` is satisfied unless
      its expression evaluates to FALSE - so a `CHECK (amount > 0 AND amount <= ...)`, which is
      exactly the predicate that closes this hole on PostgreSQL, would pass a `NaN` here.

    This test fails if either fact stops holding, which is the day the design decision behind
    `MoneyNumeric` has to be revisited.
    """
    engine, _factory = sqlite_stand
    assert engine.dialect.name == "sqlite", engine.dialect.name

    nan = float("nan")
    async with engine.begin() as conn:
        bound_type = (await conn.exec_driver_sql("SELECT typeof(?)", (nan,))).scalar_one()
        comparison = (await conn.exec_driver_sql("SELECT ? > 0", (nan,))).scalar_one()
        full_predicate = (
            await conn.exec_driver_sql(
                "SELECT ? > 0 AND ? <= 999999999999.99999999", (nan, nan)
            )
        ).scalar_one()
        # A real table, so this is the behaviour of CHECK itself and not of a SELECT.
        await conn.execute(text("DROP TABLE IF EXISTS t1526_check_probe"))
        await conn.execute(
            text(
                "CREATE TABLE t1526_check_probe (v NUMERIC(20,8) CHECK (v > 0 AND v <= 1e12))"
            )
        )
        await conn.exec_driver_sql("INSERT INTO t1526_check_probe VALUES (?)", (nan,))
        kept = (
            await conn.execute(text("SELECT typeof(v), v IS NULL FROM t1526_check_probe"))
        ).all()
        await conn.execute(text("DROP TABLE t1526_check_probe"))

    assert bound_type == "null", (
        f"sqlite3 no longer coerces a bound float NaN to NULL (typeof -> {bound_type!r}). The "
        f"reason `debts.amount` refuses NaN on this tier has changed; re-derive the guard."
    )
    assert comparison is None and full_predicate is None, (
        f"a comparison against NaN no longer yields NULL on this tier "
        f"(> 0 -> {comparison!r}, full predicate -> {full_predicate!r})"
    )
    assert kept == [("null", 1)], (
        f"the CHECK constraint refused the NaN row on SQLite: {kept!r}. If that is now true, the "
        f"DDL guard can carry this tier as well and `MoneyNumeric` is no longer the only place "
        f"the refusal can live."
    )
