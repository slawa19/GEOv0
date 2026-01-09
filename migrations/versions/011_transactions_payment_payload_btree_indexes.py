"""Postgres btree indexes for payment filters

Revision ID: 011_transactions_payment_payload_btree_indexes
Revises: 010_debts_amount_gt_zero
Create Date: 2026-01-09

Adds expression btree indexes for common payment list filters:
- payload->>'from'
- payload->>'to'
- payload->>'equivalent'

These complement the existing GIN index on transactions.payload.
Skipped for non-Postgres (tests use SQLite).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "011_transactions_payment_payload_btree_indexes"
down_revision = "010_debts_amount_gt_zero"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_transactions_payment_payload_from
        ON transactions ((payload->>'from'))
        WHERE type = 'PAYMENT'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_transactions_payment_payload_to
        ON transactions ((payload->>'to'))
        WHERE type = 'PAYMENT'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_transactions_payment_payload_equivalent
        ON transactions ((payload->>'equivalent'))
        WHERE type = 'PAYMENT'
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_transactions_payment_payload_equivalent")
    op.execute("DROP INDEX IF EXISTS ix_transactions_payment_payload_to")
    op.execute("DROP INDEX IF EXISTS ix_transactions_payment_payload_from")
