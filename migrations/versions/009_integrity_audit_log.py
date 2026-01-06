"""Integrity audit trail table

Revision ID: 009
Revises: 008
Create Date: 2026-01-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integrity_audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("operation_type", sa.String(50), nullable=False),
        sa.Column("tx_id", sa.String(64), nullable=True),
        sa.Column("equivalent_code", sa.String(16), nullable=False),
        sa.Column("state_checksum_before", sa.String(64), nullable=False),
        sa.Column("state_checksum_after", sa.String(64), nullable=False),
        sa.Column("affected_participants", sa.JSON(), nullable=False),
        sa.Column("invariants_checked", sa.JSON(), nullable=False),
        sa.Column("verification_passed", sa.Boolean(), nullable=False),
        sa.Column("error_details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )

    op.create_index("idx_integrity_audit_timestamp", "integrity_audit_log", ["timestamp"])
    op.create_index("idx_integrity_audit_tx_id", "integrity_audit_log", ["tx_id"])
    op.create_index("idx_integrity_audit_verification_passed", "integrity_audit_log", ["verification_passed"])


def downgrade() -> None:
    op.drop_index("idx_integrity_audit_verification_passed", table_name="integrity_audit_log")
    op.drop_index("idx_integrity_audit_tx_id", table_name="integrity_audit_log")
    op.drop_index("idx_integrity_audit_timestamp", table_name="integrity_audit_log")
    op.drop_table("integrity_audit_log")
