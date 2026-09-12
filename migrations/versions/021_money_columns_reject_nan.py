"""debts.amount and trust_lines.limit: the positivity checks admitted NaN. Close them.

Revision ID: 021_money_columns_reject_nan
Revises: 020_debts_equivalent_fk_restrict
Create Date: 2026-09-12

Spec 015 / T1526.

WHAT WAS WRONG. `debts.amount` is `NUMERIC(20, 8)` and its only guard was
`CHECK (amount > 0)`; `trust_lines."limit"` is the same type guarded by `CHECK ("limit" >= 0)`.
PostgreSQL orders `NaN` ABOVE every number, so both predicates are TRUE for it. Measured on
PostgreSQL 16.9 against the live column in `geov0_test_ci` on 2026-09-12:

    SELECT 'NaN'::numeric(20,8) > 0;                 -- t
    SELECT pg_get_constraintdef(oid) FROM pg_constraint
     WHERE conname = 'chk_debt_amount_positive';     -- CHECK ((amount > (0)::numeric))

and an `INSERT INTO debts (..., amount) VALUES (..., 'NaN')` was accepted and kept.

WHY IT IS NOT JUST ANOTHER BAD VALUE. A wrong amount makes the book wrong by something an audit
can name. A `NaN` amount makes every aggregate that includes it `NaN`: `SUM(amount)` over the
equivalent stops being a number, so "the sum of all debts is zero" - the property programme 015
exists to establish - is not reachable at all. Reproduced in
`tests/integration/test_p015_t1526_nan_amount_reaches_the_money_column_postgres.py::test_b`.

THE PREDICATE: THREE CLAUSES, THREE JOBS, and the comment saying which is which is part of the
change. Measured, not reasoned by analogy (2026-09-12, PostgreSQL 16.9, live `geov0_test_ci`):

    value                   > 0   abs(v) < 1e12   <= 999999999999.99999999   <> 'NaN'
    'NaN'                    t          f                    f                   f
    1                        t          t                    t                   t
    0.00000001               t          t                    t                   t
    999999999999.99999999    t          t                    t                   t
    0                        f          t                    t                   t
    -1                       f          t                    t                   t

* SIGN - `amount > 0`, `"limit" >= 0`. Unchanged, and per column: a debt of zero is not a debt, a
  trust limit of zero is a real line with no headroom.
* MAGNITUDE - `<= 999999999999.99999999`, the largest value the column can hold. On PostgreSQL it
  refuses NOTHING `NUMERIC(20, 8)` would have accepted. On SQLite, where the column type is not
  enforced at all, it is a real bound, it agrees with the money door's `MONEY_MAX_INTEGER_DIGITS`
  = 12 (`app/utils/validation.py`), and it is what refuses a positive `Infinity` - measured there:
  `CHECK (v > 0)` alone accepts one and stores it as REAL `inf`.
* NOT A NUMBER - `<> 'NaN'`, and it is EXPLICIT ON PURPOSE even though the magnitude bound already
  rejects `NaN` on PostgreSQL. That rejection is the bound's SIDE EFFECT, and a constraint that
  refuses the right value for a reason nobody wrote down is the same defect as `NOT NULL` refusing
  `NaN` on SQLite: whoever relaxes the magnitude bound later has no way to know they also removed
  the `NaN` guard. On PostgreSQL the two clauses are independently sufficient and therefore each
  other's backstop; on SQLite this clause can neither refuse nor accept anything, because a bound
  `NaN` arrives as `NULL` and the comparison is `NULL` - that tier's refusal is the `MoneyNumeric`
  bind guard, and the clause is kept in its DDL so the predicate does not differ between dialects.

`amount = amount` is NOT among the candidates: it is the obvious IEEE trick and it does not work
here. PostgreSQL's `numeric` deliberately departs from IEEE and defines `'NaN' = 'NaN'` as TRUE so
that `NaN` can be sorted, grouped and indexed - the same departure that admits it in the first
place.

The constraint as PostgreSQL stores it afterwards:

    CHECK (((amount > (0)::numeric) AND (amount <= 999999999999.99999999)
            AND (amount <> 'NaN'::numeric)))

THE NAMES ARE KEPT. `chk_debt_amount_positive` and `chk_trust_line_limit_positive` are quoted
across programmes 012 and 015 and are matched by
`tests/unit/test_trustline_conflict_identity.py`, which classifies a conflict by the constraint
name in the message. What the constraints say is now complete; what they are called did not need
to change to say it.

THE CONSTRAINTS ARE REFLECTED, NOT DROPPED BY AN ASSUMED NAME - the lesson migration 020 paid
for. There, a name chosen by migration 005 did not exist on a database whose schema had been built
by `create_all` and stamped, and the migration failed with `constraint ... does not exist`. Here
the check on the money column is found by INSPECTING the table and matching on the column it
mentions, and the migration refuses to guess if it does not find exactly one.

EXISTING ROWS ARE A GATE, NOT A CLEANUP. Migration 010 deleted the rows that violated the
predicate it was adding. That is not available here: a `NaN` row is an obligation that someone
recorded, and deleting it destroys money silently - the exact failure T1524 closed at the foreign
key. So this migration COUNTS the offending rows first and refuses to run, naming them, if any
exist. A human decides what a debt of `NaN` was supposed to be.

WHAT THIS DOES NOT CHANGE, deliberately. `simulator_run_metrics.value` is the third
`NUMERIC(20, 8)` column in the schema and has NO check constraint on the value at all, so there is
nothing here to repair; it is measurement rather than money, its writers swallow failures by
design, and closing it means adding a constraint to a column that never had one. Recorded in the
T1526 report with its reachability instead of changed in passing (`AGENTS.md` §18).

Batch mode, as in 016 and 020, so the same migration is valid on SQLite. `migrations/env.py`
refuses to run on any other backend, and the SQLite unit tier builds its schema from the model -
which is why the model carries the same predicate and, in addition, the `MoneyNumeric` bind guard
that is the only thing capable of refusing a `NaN` on that tier (`app/db/types.py`).

DOWNGRADE restores the bare positivity checks. That reintroduces the hole and is provided only for
symmetry.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "021_money_columns_reject_nan"
down_revision = "020_debts_equivalent_fk_restrict"
branch_labels = None
depends_on = None


#: The largest value `NUMERIC(20, 8)` can hold. Written out rather than imported from
#: `app.db.types` on purpose: a migration must keep describing the schema change it made even if
#: the application constant later moves or changes. The model builds the same predicate from
#: `app.db.types.finite_money_clauses`, and they are held together by the SQLite tier, which builds
#: its schema from the model, and the PostgreSQL tier, which builds it from these migrations - the
#: T1526 tests run on both.
MONEY_COLUMN_MAX = "999999999999.99999999"

#: `(table, column, old predicate, new predicate)`. The quoting of `"limit"` is not optional -
#: it is a reserved word in PostgreSQL.
_COLUMNS = (
    (
        "debts",
        "amount",
        "amount > 0",
        f"amount > 0 AND amount <= {MONEY_COLUMN_MAX} AND amount <> 'NaN'",
    ),
    (
        "trust_lines",
        "limit",
        '"limit" >= 0',
        f"\"limit\" >= 0 AND \"limit\" <= {MONEY_COLUMN_MAX} AND \"limit\" <> 'NaN'",
    ),
)


def _check_name_on(table: str, column: str) -> str:
    """The actual name of the check constraint guarding `column` on THIS database.

    Matched on the column the constraint mentions rather than on a name this file chose, because
    a database built by `create_all` and one built by migrations do not always agree on names -
    migration 020 failed for exactly that reason.
    """

    inspector = sa.inspect(op.get_bind())
    matches = [
        constraint["name"]
        for constraint in inspector.get_check_constraints(table)
        if column in str(constraint.get("sqltext") or "") and constraint.get("name")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one check constraint mentioning {table}.{column}, found "
            f"{matches!r}; refusing to guess which constraint to replace"
        )
    return str(matches[0])


def _refuse_if_rows_violate(table: str, column: str, predicate: str) -> None:
    """Refuse to proceed while rows exist that the new predicate would reject.

    Adding the constraint would fail anyway - this exists so that the failure names the rows and
    the reason instead of a bare `check constraint is violated by some row`. These rows are money
    someone recorded; they are not deleted here (see the module docstring).
    """

    quoted = f'"{column}"'
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"SELECT id, {quoted} AS value FROM {table} WHERE NOT ({predicate}) LIMIT 20")  # noqa: S608
    ).fetchall()
    if rows:
        listed = ", ".join(f"{row[0]}={row[1]}" for row in rows)
        raise RuntimeError(
            f"{table}.{column} holds {len(rows)} or more rows that are not valid money "
            f"(showing up to 20): {listed}. A NaN or out-of-range amount is an obligation someone "
            f"recorded; this migration will not delete it. Decide what each row should be, correct "
            f"it, and run the migration again."
        )


def _replace(table: str, column: str, predicate: str) -> None:
    name = _check_name_on(table, column)
    _refuse_if_rows_violate(table, column, predicate)
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(name, type_="check")
        batch_op.create_check_constraint(name, predicate)


def upgrade() -> None:
    for table, column, _old, new in _COLUMNS:
        _replace(table, column, new)


def downgrade() -> None:
    for table, column, old, _new in _COLUMNS:
        _replace(table, column, old)
