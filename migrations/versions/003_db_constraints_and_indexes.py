"""Add constraints and indexes for v0.1

Revision ID: 003
Revises: 002
Create Date: 2026-01-04

- Ensure participants.public_key is unique
- Add composite indexes used by hot queries
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # participants.public_key must be unique
    op.create_unique_constraint(
        "uq_participants_public_key",
        "participants",
        ["public_key"],
    )

    # Hot-path indexes
    op.create_index(
        "ix_debts_debtor_creditor",
        "debts",
        ["debtor_id", "creditor_id"],
        unique=False,
    )

    op.create_index(
        "ix_trust_lines_from_status",
        "trust_lines",
        ["from_participant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_trust_lines_from_status", table_name="trust_lines")
    op.drop_index("ix_debts_debtor_creditor", table_name="debts")
    op.drop_constraint("uq_participants_public_key", "participants", type_="unique")
