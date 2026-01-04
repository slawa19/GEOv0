"""DB schema enhancements for v0.1

Revision ID: 004
Revises: 003
Create Date: 2026-01-04

- transactions.idempotency_key + best-effort index for active transactions
- equivalents.symbol + basic CHECK on code format
- prepare_locks.lock_type
- config.version

Note: Some operations are best-effort/conditional by dialect to keep SQLite tests working.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACTIVE_STATES = ("NEW", "ROUTED", "PREPARE_IN_PROGRESS", "PREPARED", "PROPOSED", "WAITING")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else "unknown"

    # === transactions.idempotency_key ===
    op.add_column(
        "transactions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )

    # Index to support (initiator_id, idempotency_key) lookups.
    # For Postgres, use a partial index to focus on active transactions only.
    if dialect == "postgresql":
        op.create_index(
            "ix_transactions_initiator_idempotency_active",
            "transactions",
            ["initiator_id", "idempotency_key"],
            unique=False,
            postgresql_where=sa.text(
                "idempotency_key IS NOT NULL AND state IN (" + ",".join([f"'{s}'" for s in _ACTIVE_STATES]) + ")"
            ),
        )
    else:
        op.create_index(
            "ix_transactions_initiator_idempotency",
            "transactions",
            ["initiator_id", "idempotency_key"],
            unique=False,
        )

    # === equivalents.symbol + code CHECK ===
    op.add_column(
        "equivalents",
        sa.Column("symbol", sa.String(length=16), nullable=True),
    )

    # Portable-ish CHECK: enforce uppercase code.
    # (SQLite supports upper(); Postgres too.)
    op.create_check_constraint(
        "chk_equivalents_code_upper",
        "equivalents",
        "code = upper(code)",
    )

    # === prepare_locks.lock_type ===
    op.add_column(
        "prepare_locks",
        sa.Column("lock_type", sa.String(length=16), nullable=False, server_default="PAYMENT"),
    )
    op.create_index(
        "ix_prepare_locks_lock_type",
        "prepare_locks",
        ["lock_type"],
        unique=False,
    )
    op.create_check_constraint(
        "chk_prepare_locks_lock_type",
        "prepare_locks",
        "lock_type IN ('PAYMENT','CLEARING')",
    )
    op.alter_column("prepare_locks", "lock_type", server_default=None)

    # === config.version ===
    op.add_column(
        "config",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("config", "version", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else "unknown"

    # config.version
    op.drop_column("config", "version")

    # prepare_locks.lock_type
    op.drop_constraint("chk_prepare_locks_lock_type", "prepare_locks", type_="check")
    op.drop_index("ix_prepare_locks_lock_type", table_name="prepare_locks")
    op.drop_column("prepare_locks", "lock_type")

    # equivalents.symbol + code CHECK
    op.drop_constraint("chk_equivalents_code_upper", "equivalents", type_="check")
    op.drop_column("equivalents", "symbol")

    # transactions.idempotency_key
    if dialect == "postgresql":
        op.drop_index("ix_transactions_initiator_idempotency_active", table_name="transactions")
    else:
        op.drop_index("ix_transactions_initiator_idempotency", table_name="transactions")
    op.drop_column("transactions", "idempotency_key")
