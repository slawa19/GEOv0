"""The money column type and the money CHECK clauses: what a `NUMERIC` column may be asked to hold.

015 / `T1526`. `debts.amount` is `NUMERIC(20, 8)` guarded by `CHECK (amount > 0)`, and that guard
admits `NaN`, because PostgreSQL orders `NaN` ABOVE every number:

    geov0_test_ci=> SELECT 'NaN'::numeric(20,8) > 0;
     t

One such row is not a debt of the wrong size - it is a book that can no longer be summed. Every
aggregate containing it is `NaN`, so "the sum of all debts is zero" stops being reachable at all
rather than becoming wrong by a nameable amount.

TWO GUARDS, BECAUSE NEITHER ONE COVERS THE OTHER'S PATHS.

* The CHECK constraints (migration `021_money_columns_reject_nan`) are the guarantee for everything
  that reaches the table: `psql`, a maintenance script, a future writer, anything that does not go
  through this process. They are the only guard that holds for raw SQL.
* `MoneyNumeric` below is the guarantee for everything that goes through SQLAlchemy. HISTORY (the
  application runs on PostgreSQL only since programme 017): it was also the ONLY guard that could
  refuse a `NaN` on SQLite. Measured on `sqlite+aiosqlite` 2026-09-12:

      typeof(?)      with float('nan')  ->  'null'
      SELECT ? > 0   with float('nan')  ->  NULL
      CHECK (v > 0 AND v <= 1e12), then INSERT nan  ->  row accepted, stored as NULL

  `sqlite3` converts a bound `NaN` to SQL `NULL`, so it never reaches the predicate at all; and if
  it did, every comparison against it is `NULL` and a SQLite `CHECK` is satisfied unless its
  expression is FALSE. What refuses the value today is therefore `NOT NULL`: a constraint about
  whether an amount was SUPPLIED, answering a question about whether the supplied amount is a
  NUMBER. The message sends the reader looking for a missing field, and the refusal is an accident
  of the driver rather than a rule of this money core - it does not transfer to PostgreSQL, where
  the same value is stored.

  Those measurements were pinned by a SQLite test that left with SQLite (017 stage 3); the
  PostgreSQL half is `tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py`.

This is deliberately NOT the money door. `app/utils/validation.py::parse_money_amount` owns the
wire grammar and the HTTP-shaped refusal for writers that answer a request. This module is the last
thing before the wire, for the writers that never passed a door.

WHAT IT REFUSES WIDENED ON 2026-09-24 (018 / FORK-1, slice B0a). Until then `MoneyNumeric` refused
only non-finite values, and the scale-8 and magnitude refusal before debt SQL lived in exactly one
place: the debt journal's listener (`journal.py::_check_storable`), which stage B of 018 removes.
PostgreSQL cannot take that refusal over - a `NUMERIC(20, 8)` column coerces `0.123456789` to
`0.12345679` before any CHECK or trigger sees it. So the bind now applies THE storability predicate
(`app/utils/validation.py::money_storability_violation`) with the column's own declared capacity:
finiteness, `abs(value) < 10**(precision - scale)`, and exact representability at `scale`. That is
a deliberate strengthening of every `MoneyNumeric` column's binding contract, not only of
`debts.amount`. What it does NOT cover: raw SQL text (`exec_driver_sql`, `text()` with untyped
binds, SQL expressions computed in the database) - the CHECK constraints stay the guarantee there,
and they bound the magnitude and exclude `NaN` but cannot see a rounded ninth digit.
`AGENTS.md` §9 - every stage correct by itself, no leaning on a guard further along.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator

from app.utils.validation import MONEY_FINITENESS, money_storability_violation

__all__ = ["MONEY_COLUMN_MAX", "MoneyNumeric", "finite_money_clauses"]


#: The largest value `NUMERIC(20, 8)` can hold: 12 integer digits and 8 fraction digits.
MONEY_COLUMN_MAX = "999999999999.99999999"


def finite_money_clauses(column: str) -> str:
    """The part of a money CHECK constraint that is the same for every money column.

    A money constraint has THREE jobs, and each clause does exactly one of them. They are written
    here together so the next editor can see which is which before removing one:

    1. SIGN - `amount > 0`, `"limit" >= 0`. Owned by the CALLER, because it differs per column: a
       debt of zero is not a debt, a trust limit of zero is a real line with no headroom.
    2. MAGNITUDE - `<= 999999999999.99999999`, produced here. It is the column's own maximum, so on
       PostgreSQL it refuses nothing `NUMERIC(20, 8)` would have accepted. On SQLite, where the
       column type is not enforced at all, it is a real bound and the only one there is - and it
       is not decoration there either: measured 2026-09-12, `CHECK (v > 0)` alone ACCEPTS positive
       `Infinity` on SQLite and stores it as REAL `inf`, and this clause is what refuses it.
    3. NOT A NUMBER - `<> 'NaN'`, produced here, and it is EXPLICIT ON PURPOSE.

    WHY 3 EXISTS WHEN 2 ALREADY REJECTS `NaN`. It does reject it - PostgreSQL orders `NaN` above
    every number, so `NaN` fails any upper bound - but that is the bound's SIDE EFFECT. A
    constraint that refuses the right value for a reason nobody wrote down is the same defect as
    `NOT NULL` refusing `NaN` on SQLite: the next editor relaxes the magnitude bound, has no way to
    know they also removed the `NaN` guard, and the hole reopens silently. Clause 3 states the
    intent, and on PostgreSQL it is independently sufficient - so the two clauses are also each
    other's backstop.

    Measured on PostgreSQL 16.9 against `geov0_test_ci`, 2026-09-12:

        value                   > 0   abs(v) < 1e12   <= 999999999999.99999999   <> 'NaN'
        'NaN'                    t          f                    f                   f
        1                        t          t                    t                   t
        0.00000001               t          t                    t                   t
        999999999999.99999999    t          t                    t                   t
        0                        f          t                    t                   t
        -1                       f          t                    t                   t

    and the constraint as PostgreSQL then stores it:

        CHECK (((v > (0)::numeric) AND (v <= 999999999999.99999999) AND (v <> 'NaN'::numeric)))

    `v = v` is NOT in that table because it does not belong there: it is the obvious IEEE trick and
    PostgreSQL's `numeric` deliberately departs from IEEE - `'NaN' = 'NaN'` is TRUE, so that `NaN`
    can be sorted, grouped and indexed. That departure is the same one that admits `NaN` here.

    ON SQLITE, clause 3 can neither refuse nor accept anything: a bound `NaN` arrives as `NULL`, so
    the comparison is `NULL` and the CHECK passes (measured above). That tier's refusal is
    `MoneyNumeric`, not this clause. The clause is kept in the DDL of both dialects anyway, because
    a predicate that differs between dialects is a second thing to keep in step, and because it is
    the sentence that says what the constraint is FOR.
    """

    return f"{column} <= {MONEY_COLUMN_MAX} AND {column} <> 'NaN'"


class MoneyNumeric(TypeDecorator):
    """`Numeric(precision, scale)`, plus a refusal to bind a value the column cannot hold exactly.

    The DDL it emits is exactly the DDL of its `impl`, so a column that changes to this type keeps
    its type in every database and no migration is needed for the type itself.

    The refusal is THE storability predicate (`money_storability_violation`) with this column's
    declared capacity: not finite (T1526), `abs(value) >= 10**(precision - scale)`, or not exactly
    representable at `scale` (PostgreSQL would round it). Insignificant trailing zeros pass;
    so does the full width, `999999999999.99999999` for `(20, 8)`. A value that is not a number at
    all in any spelling this can read (not `Decimal`/`int`/`float`/`str`) is passed through:
    refusing it is the dialect's job.

    The refusal raises `ValueError`, which SQLAlchemy wraps in `StatementError` carrying the
    statement and its parameters - so the failure names the table, the column and the value,
    instead of naming `NOT NULL` or a rounded row. Its message begins with the predicate's reason.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int, scale: int, **kwargs: Any) -> None:
        # A money column without a declared capacity would leave the bind unable to say what fits.
        super().__init__(precision, scale, **kwargs)
        self._max_integer_digits = int(precision) - int(scale)
        self._max_scale = int(scale)

    def _refuse_unstorable(self, value: Any) -> None:
        """Raise when the column cannot hold `value` exactly; the one enforcement point at bind."""

        if not isinstance(value, (Decimal, int, float, str)):
            return
        if isinstance(value, str):
            try:
                Decimal(value)
            except (InvalidOperation, ValueError):
                return
        reason = money_storability_violation(
            value, max_integer_digits=self._max_integer_digits, max_scale=self._max_scale
        )
        if reason is None:
            return
        if reason == MONEY_FINITENESS:
            raise ValueError(
                f"{reason}: non-finite value {value!r} cannot be stored as money: a money column "
                f"holds finite decimal amounts only. NaN and Infinity are refused before the "
                f"statement is sent, because PostgreSQL's NUMERIC accepts NaN and every sum over it "
                f"becomes NaN. See app/db/types.py and migration 021_money_columns_reject_nan."
            )
        raise ValueError(
            f"{reason}: value {value!r} does not fit NUMERIC({self._max_integer_digits + self._max_scale}, "
            f"{self._max_scale}) exactly - PostgreSQL would round the fraction or overflow the "
            f"integer part, and the stored money would differ from the value given. Refused at "
            f"bind (app/db/types.py, 018 FORK-1)."
        )

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is not None:
            self._refuse_unstorable(value)
        return value
