"""Add owner_id and owner_kind to simulator_runs.

Revision ID: 017_add_owner_to_simulator_runs
Revises: 016_debts_equivalent_debtor_index_and_no_self_loop
Create Date: 2026-02-18

Adds owner_id (String(200)) and owner_kind (String(50)) columns to simulator_runs
to identify the owner of each run (anonymous cookie, user, admin, etc.).

- Both columns added as nullable for safe migration.
- Existing rows are backfilled with legacy sentinel values.
- For PostgreSQL: owner_id is made NOT NULL after backfill.
- For SQLite (tests): ALTER COLUMN is not supported, column stays nullable.
- Composite index on (owner_id, state, created_at) for efficient owner-scoped list queries.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "017_add_owner_to_simulator_runs"
down_revision = "016_debts_equivalent_debtor_index_and_no_self_loop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Add columns as nullable (works on both SQLite and PostgreSQL).
    op.add_column("simulator_runs", sa.Column("owner_id", sa.String(200), nullable=True))
    op.add_column("simulator_runs", sa.Column("owner_kind", sa.String(50), nullable=True))

    # Step 2: Backfill existing rows.
    # owner_id: use sentinel for legacy rows (needed for NOT NULL constraint).
    # owner_kind: leave NULL for legacy rows (§5.2 spec: legacy rows have NULL owner_kind).
    op.execute("UPDATE simulator_runs SET owner_id = 'legacy:unknown' WHERE owner_id IS NULL")
    # owner_kind stays NULL for legacy rows — do NOT backfill with 'admin'.

    # Step 3: On PostgreSQL, enforce NOT NULL now that all rows have values.
    # SQLite does not support ALTER COLUMN, so we leave the column nullable there.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column("simulator_runs", "owner_id", nullable=False)

    # Step 4: Composite index for efficient owner-scoped list queries.
    op.create_index(
        "ix_simulator_runs_owner_state_created",
        "simulator_runs",
        ["owner_id", "state", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_simulator_runs_owner_state_created", table_name="simulator_runs")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column("simulator_runs", "owner_id", nullable=True)

    op.drop_column("simulator_runs", "owner_kind")
    op.drop_column("simulator_runs", "owner_id")
