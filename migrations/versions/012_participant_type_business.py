"""Replace participant type organization -> business

Revision ID: 012_participant_type_business
Revises: 011_transactions_payment_payload_btree_indexes
Create Date: 2026-01-13

This migration updates the participants.type check constraint to use:
- person
- business
- hub

and migrates existing rows from 'organization' to 'business'.
Skipped for non-Postgres (tests use SQLite).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "012_participant_type_business"
down_revision = "011_transactions_payment_payload_btree_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("UPDATE participants SET type = 'business' WHERE type = 'organization'")
    op.execute("ALTER TABLE participants DROP CONSTRAINT IF EXISTS chk_participant_type")
    op.execute(
        """
        ALTER TABLE participants
        ADD CONSTRAINT chk_participant_type
        CHECK (type IN ('person', 'business', 'hub'))
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("UPDATE participants SET type = 'organization' WHERE type = 'business'")
    op.execute("ALTER TABLE participants DROP CONSTRAINT IF EXISTS chk_participant_type")
    op.execute(
        """
        ALTER TABLE participants
        ADD CONSTRAINT chk_participant_type
        CHECK (type IN ('person', 'organization', 'hub'))
        """
    )
