"""debts: (equivalent_id, debtor_id) index + forbid self-loops

Revision ID: 016_debts_equivalent_debtor_index_and_no_self_loop
Revises: 015_debts_optimistic_lock_version
Create Date: 2026-02-15

- Adds composite index to speed up clearing SQL joins and equivalent-scoped scans.
- Adds a check constraint to forbid debtor_id == creditor_id (self-loop debts).

Note: SQLite unit/integration tests use Base.metadata.create_all and do not run Alembic.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "016_debts_equivalent_debtor_index_and_no_self_loop"
down_revision = "015_debts_optimistic_lock_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use batch mode for SQLite compatibility (table recreation) while staying valid for Postgres.
    with op.batch_alter_table("debts") as batch_op:
        batch_op.create_index(
            "ix_debts_equivalent_debtor",
            ["equivalent_id", "debtor_id"],
            unique=False,
        )
        batch_op.create_check_constraint(
            "chk_debt_no_self_loop",
            "debtor_id != creditor_id",
        )


def downgrade() -> None:
    with op.batch_alter_table("debts") as batch_op:
        batch_op.drop_constraint("chk_debt_no_self_loop", type_="check")
        batch_op.drop_index("ix_debts_equivalent_debtor")
