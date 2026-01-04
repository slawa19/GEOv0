"""Payment idempotency uniqueness + equivalents code format

Revision ID: 007
Revises: 006
Create Date: 2026-01-04

- Ensure (initiator_id, type, idempotency_key) is unique when idempotency_key is provided.
- Strengthen equivalents.code format check on Postgres (regex).

SQLite tests remain compatible (no regex check there).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else "unknown"

    # Idempotency uniqueness for payments (and future transaction types).
    op.create_index(
        "ux_transactions_initiator_type_idempotency",
        "transactions",
        ["initiator_id", "type", "idempotency_key"],
        unique=True,
    )

    # Stronger equivalent code format: only for Postgres.
    # Already have uppercase CHECK from revision 004.
    if dialect == "postgresql":
        op.create_check_constraint(
            "chk_equivalents_code_format",
            "equivalents",
            "code ~ '^[A-Z0-9_]{1,16}$'",
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else "unknown"

    if dialect == "postgresql":
        op.drop_constraint("chk_equivalents_code_format", "equivalents", type_="check")

    op.drop_index("ux_transactions_initiator_type_idempotency", table_name="transactions")
