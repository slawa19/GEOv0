"""debts: optimistic locking version column

Revision ID: 015_debts_optimistic_lock_version
Revises: 014_prepare_locks_tx_fk_and_participant_expires_index
Create Date: 2026-02-13

Adds `debts.version` for SQLAlchemy ORM optimistic concurrency control.

Note: SQLite tests use Base.metadata.create_all and do not run Alembic.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "015_debts_optimistic_lock_version"
down_revision = "014_prepare_locks_tx_fk_and_participant_expires_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "debts",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )

    # Keep default for backfills; app code will manage increments.


def downgrade() -> None:
    op.drop_column("debts", "version")
