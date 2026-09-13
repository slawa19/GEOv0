"""`delta` was bounded and non-zero and was never required to be `after - before`.

Revision ID: 024_debt_journal_delta
Revises: 023_debt_journal_counts
Create Date: 2026-09-13

Spec 015 / `T1530`, found by the third review circle of the `T1528` delta and measured on this tree
before this migration existed.

WHAT THE TABLE GUARANTEED AND WHAT IT DID NOT. `chk_debt_journal_entries_delta` says the delta is
non-zero, inside `NUMERIC(20, 8)` and not `NaN`; `chk_debt_journal_entries_shape` says which of
`amount_before` / `amount_after` is NULL for each effect. Neither says anything about the
ARITHMETIC, so an entry reading `amount_before = 10, amount_after = 11, delta = 2` satisfied every
constraint the table had. Measured 2026-09-13: a `before_execute` listener registered after the
journal's rewrote the entry INSERT's `delta` from 1 to 2, `debts` kept 11, the entry was stored and
the envelope completed with a digest taken over it, and nothing refused anywhere.

That row is not an amount that is wrong by a nameable sum - it is a row that contradicts itself, and
criterion (a) of design v2 (the journal's per-edge deltas equal the edge's final amount minus its
initial one) is false for the whole edge from the moment one exists.

WHY THIS SITS IN THE DATABASE AND NOT ONLY IN THE PROCESS. The in-process half of `T1530` reads the
stored entries back and compares them with the effects the flush hook computed
(`app/core/ledger/journal.py::_verify_entries`). That half lives inside the same listener pipeline
every other in-process guard lives in. This constraint is below all of it: no listener, no session
and no `exec_driver_sql` reaches past it, and it also holds for `psql` and for any future writer.

POSTGRESQL ONLY, AND THE ASYMMETRY IS MEASURED. Every migration in this tree is PostgreSQL-only and
the SQLite tiers build their schema from `Base.metadata` - but here the metadata side is ALSO
PostgreSQL-only (`ddl_if(dialect="postgresql")` in `app/db/journal_tables.py`), which is unusual
enough to state the measurement. On SQLite `Numeric` binds through `float`, so this equality is
floating point there, and it is false for ORDINARY money: measured 2026-09-13 on sqlite3, the
legitimate movement `10.00000001 -> 10.00000002, delta 0.00000001` gives a left-hand side of
`9.99999905104687e-09`, and `33554431.99999999 -> 33554432.00000001, delta 0.00000002` gives
`1.862645149230957e-08`. A constraint that refuses real writes is the same defect as one that admits
false ones, so it is not installed there and SQLite's guarantee for this class is the readback.
"""

from alembic import op

revision = "024_debt_journal_delta"
down_revision = "023_debt_journal_counts"
branch_labels = None
depends_on = None

_TABLE = "debt_journal_entries"
_CONSTRAINT = "chk_debt_journal_entries_delta_arithmetic"
_PREDICATE = "delta = COALESCE(amount_after, 0) - COALESCE(amount_before, 0)"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_check_constraint(_CONSTRAINT, _TABLE, _PREDICATE)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
