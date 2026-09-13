"""debts.equivalent_id: CASCADE -> RESTRICT. Deleting an equivalent must not delete debt.

Revision ID: 020_debts_equivalent_fk_restrict
Revises: 019_trust_lines_partial_unique_live
Create Date: 2026-09-11

Spec 015 / T1524.

WHAT WAS WRONG. `fk_debts_equivalent_id` was created `ondelete="CASCADE"` (migration 005), and the
model declared the same. `DELETE /admin/equivalents/{code}` refuses an active equivalent and one with
non-zero usage counts, then deletes the row - and the DATABASE deleted every debt denominated in it.
No Debt instance was loaded, so no application code, no audit row and no journal hook observed an
obligation disappearing.

The usage count made it a race rather than a certainty: a debt created between the count and the
commit was destroyed with the equivalent. Reproduced on PostgreSQL 16 before this migration by making
the count miss a debt that exists: the route answered 200 and the 42.00000000 debt was gone.

It is also the one production path programme 015's phase B journal - a flush listener on the ORM -
cannot see, which is why it is closed at the constraint rather than instrumented.

WHAT THIS MIGRATION DOES. Recreates the constraint with `ondelete="RESTRICT"`. Deleting an
equivalent that still carries debt now fails in the database, whatever the application checked; the
route maps that refusal to the same 409 it already returns for an equivalent in use.

WHAT IT DOES NOT CHANGE, deliberately. `trust_lines.equivalent_id` and
`integrity_checkpoints.equivalent_id` still cascade. Those are credit agreements and evidence rather
than money obligations, both are covered by the same usage count, and changing them is outside T1524.
The participant foreign keys on `debts` still cascade too; nothing in the application deletes a
participant today. NO LONGER TRUE OF THE CASCADE, as of migration 025 (T1533), which closed that
half: `fk_debts_debtor_id` and `fk_debts_creditor_id` are RESTRICT now. The second sentence still
holds - there is no participant hard-delete path in `app/` - and it was never a reason to keep the
cascade. This paragraph is corrected rather than deleted because it is the record of what T1524
knowingly left open.

THE CONSTRAINT NAME IS LOOKED UP, NOT ASSUMED - and the first edition of this migration assumed it.
Migration 005 drops `debts_equivalent_id_fkey` and creates `fk_debts_equivalent_id`, so a database
migrated from the start carries the second name. But the PostgreSQL test database `geov0_test_ci`
carries `debts_equivalent_id_fkey`, PostgreSQL's default for the unnamed foreign key the model
declares - the name a schema built by `create_all` and then stamped gets. Dropping by the name 005
chose failed there with `constraint "fk_debts_equivalent_id" does not exist` and the transaction
rolled back. So the migration reflects the foreign key on `debts.equivalent_id -> equivalents` and
drops whatever it is called. Constraint names that depend on how a database was created are a hazard
for every later migration that drops by name; recorded in `specs/BACKLOG.md`.

Batch mode, as in 016, so the same migration is valid on SQLite. SQLite unit tests build the schema
from the model and do not run Alembic, which is why the model carries the same change.

DOWNGRADE restores CASCADE. That reintroduces the defect and is provided only for symmetry.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "020_debts_equivalent_fk_restrict"
down_revision = "019_trust_lines_partial_unique_live"
branch_labels = None
depends_on = None


def _equivalent_fk_name() -> str:
    """The actual name of the debts.equivalent_id -> equivalents foreign key on this database."""
    inspector = sa.inspect(op.get_bind())
    matches = [
        fk["name"]
        for fk in inspector.get_foreign_keys("debts")
        if fk.get("referred_table") == "equivalents"
        and list(fk.get("constrained_columns") or []) == ["equivalent_id"]
    ]
    if len(matches) != 1 or not matches[0]:
        raise RuntimeError(
            "expected exactly one named foreign key debts.equivalent_id -> equivalents, "
            f"found {matches!r}; refusing to guess which constraint to replace"
        )
    return matches[0]


def _replace(ondelete: str) -> None:
    name = _equivalent_fk_name()
    with op.batch_alter_table("debts") as batch_op:
        batch_op.drop_constraint(name, type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_debts_equivalent_id",
            "equivalents",
            ["equivalent_id"],
            ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace("RESTRICT")


def downgrade() -> None:
    _replace("CASCADE")
