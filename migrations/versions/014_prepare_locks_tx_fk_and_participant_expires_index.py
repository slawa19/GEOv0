"""prepare_locks: FK tx_id -> transactions + participant/expires index

Revision ID: 014_prepare_locks_tx_fk_and_participant_expires_index
Revises: 013_simulator_storage_mvp
Create Date: 2026-02-10

- Cleanup orphan prepare locks before enabling FK.
- Add FK: prepare_locks.tx_id -> transactions.tx_id.
- Add btree index: (participant_id, expires_at).

Postgres-only: SQLite tests use Base.metadata.create_all.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "014_prepare_locks_tx_fk_and_participant_expires_index"
down_revision = "013_simulator_storage_mvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Cleanup orphan locks (single statement, safe to re-run).
    op.execute(
        """
        DELETE FROM prepare_locks pl
        WHERE NOT EXISTS (
            SELECT 1
            FROM transactions t
            WHERE t.tx_id = pl.tx_id
        )
        """
    )

    op.create_foreign_key(
        "fk_prepare_locks_tx_id",
        "prepare_locks",
        "transactions",
        ["tx_id"],
        ["tx_id"],
    )

    op.create_index(
        "ix_prepare_locks_participant_expires_at",
        "prepare_locks",
        ["participant_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_index("ix_prepare_locks_participant_expires_at", table_name="prepare_locks")
    op.drop_constraint("fk_prepare_locks_tx_id", "prepare_locks", type_="foreignkey")

