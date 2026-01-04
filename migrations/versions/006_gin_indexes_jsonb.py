"""Add GIN indexes for JSONB columns

Revision ID: 006
Revises: 005
Create Date: 2026-01-04

Adds Postgres GIN indexes for hot JSONB columns used in filtering/inspection.

Note: Applied for Postgres only. SQLite is skipped because tests rely on
Base.metadata.create_all and SQLite index types differ.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trust_lines_policy_gin ON trust_lines USING GIN (policy)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_transactions_payload_gin ON transactions USING GIN (payload)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prepare_locks_effects_gin ON prepare_locks USING GIN (effects)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP INDEX IF EXISTS ix_prepare_locks_effects_gin")
    op.execute("DROP INDEX IF EXISTS ix_transactions_payload_gin")
    op.execute("DROP INDEX IF EXISTS ix_trust_lines_policy_gin")
