"""debts amount > 0 (remove baseline rows)

Revision ID: 010_debts_amount_gt_zero
Revises: 009_integrity_audit_log
Create Date: 2026-01-09
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "010_debts_amount_gt_zero"
down_revision = "009_integrity_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove any existing baseline rows.
    op.execute("DELETE FROM debts WHERE amount = 0")

    # Tighten constraint to forbid 0 rows.
    op.execute("ALTER TABLE debts DROP CONSTRAINT chk_debt_amount_positive")
    op.execute(
        """
        ALTER TABLE debts
        ADD CONSTRAINT chk_debt_amount_positive
        CHECK (amount > 0)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE debts DROP CONSTRAINT chk_debt_amount_positive")
    op.execute(
        """
        ALTER TABLE debts
        ADD CONSTRAINT chk_debt_amount_positive
        CHECK (amount >= 0)
        """
    )
