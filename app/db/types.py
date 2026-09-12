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
* `MoneyNumeric` below is the guarantee for everything that goes through SQLAlchemy, and it is the
  ONLY guard that can refuse a `NaN` on SQLite. Measured on `sqlite+aiosqlite` 2026-09-12:

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

  Those measurements are pinned by
  `tests/unit/test_p015_t1526_nan_amount_is_refused_by_the_wrong_constraint.py`, which turns red
  the day either stops holding and the design choice here has to be re-derived.

This is deliberately NOT the money door. `app/utils/validation.py::parse_money_amount` and
`is_storable_money` own the domain rule (scale 8, magnitude below 10^12, and they already refuse
non-finite values) for writers that answer an HTTP request or build amounts out of config. This
module is the last thing before the wire, for the writers that never passed a door: it refuses only
what no money column can hold at all, and it refuses it the same way on every dialect.
`AGENTS.md` §9 - every stage correct by itself, no leaning on a guard further along.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator

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


def _is_non_finite(value: Any) -> bool:
    """True for `NaN`, `Infinity` and `-Infinity` in any spelling a bind parameter can carry.

    Anything this cannot interpret as a number answers False: refusing it is the dialect's job and
    inventing a second opinion here would be this module guessing at values it does not own.
    """

    if isinstance(value, Decimal):
        return not value.is_finite()
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    if isinstance(value, str):
        try:
            return not Decimal(value).is_finite()
        except (InvalidOperation, ValueError):
            return False
    return False


class MoneyNumeric(TypeDecorator):
    """`Numeric`, plus a refusal to bind a value that is not a finite number.

    The DDL it emits is exactly the DDL of its `impl`, so a column that changes to this type keeps
    its type in every database and no migration is needed for the type itself.

    The refusal raises `ValueError`, which SQLAlchemy wraps in `StatementError` carrying the
    statement and its parameters - so the failure names the table, the column and the value,
    instead of naming `NOT NULL`.
    """

    impl = Numeric
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is not None and _is_non_finite(value):
            raise ValueError(
                f"non-finite value {value!r} cannot be stored as money: a money column holds "
                f"finite decimal amounts only. NaN and Infinity are refused before the statement "
                f"is sent, on every dialect - on PostgreSQL because NUMERIC accepts NaN and every "
                f"sum over it becomes NaN, on SQLite because the driver would silently turn it "
                f"into NULL. See app/db/types.py and migration 021_money_columns_reject_nan."
            )
        return value
