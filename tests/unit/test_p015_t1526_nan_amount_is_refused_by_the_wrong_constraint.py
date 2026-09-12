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
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant


async def _seed():
    """One equivalent, two participants and one ordinary debt, committed on their own session."""
    from tests.conftest import TestingSessionLocal

    n = uuid.uuid4().hex[:8]
    async with TestingSessionLocal() as session:
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


async def _amounts(equivalent_id) -> list[str]:
    """The amounts the DATABASE holds for this equivalent, read on a NEW session."""
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as fresh:
        rows = (
            await fresh.execute(select(Debt.amount).where(Debt.equivalent_id == equivalent_id))
        ).scalars().all()
    return [str(value) for value in rows]


@pytest.mark.asyncio
async def test_a_the_refusal_of_a_nan_amount_must_name_the_money_rule(db_session):
    """RED TODAY: the refusal is `NOT NULL constraint failed: debts.amount`.

    `db_session` is requested for its schema setup and its per-test table reset only; every write
    below goes through its own session so that "committed" and "never written" cannot be confused.
    """
    from tests.conftest import TestingSessionLocal

    equivalent_id, debtor_id, creditor_id = await _seed()

    # NON-VACUITY: this tier really stores money, so a refusal below is about the VALUE.
    assert await _amounts(equivalent_id) == ["5.00000000"], (
        f"the stand could not store an ordinary debt ({await _amounts(equivalent_id)!r}), so it "
        f"cannot tell a refusal from a broken stand"
    )

    refusal: BaseException | None = None
    async with TestingSessionLocal() as session:
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

    stored = await _amounts(equivalent_id)
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
async def test_b_a_check_constraint_on_this_tier_can_never_refuse_a_nan(db_session):
    """MEASUREMENT, green before and after: why the SQLite fix cannot be a CHECK constraint.

    Two facts of this dialect, both measured against the real driver rather than recalled:

    * a float `NaN` bound as a parameter arrives as SQL `NULL`;
    * every comparison against that value yields `NULL`, and SQLite's `CHECK` is satisfied unless
      its expression evaluates to FALSE - so a `CHECK (amount > 0 AND amount <= ...)`, which is
      exactly the predicate that closes this hole on PostgreSQL, would pass a `NaN` here.

    This test fails if either fact stops holding, which is the day the design decision behind
    `MoneyNumeric` has to be revisited.
    """
    from tests.conftest import engine

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
