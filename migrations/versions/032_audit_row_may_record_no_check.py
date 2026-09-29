"""An integrity audit row may say that no check ran: `verification_passed` becomes NULL-able.

Revision ID: 032_audit_row_may_record_no_check
Revises: 031_drop_prepare_locks
Create Date: 2026-09-29

Programme 024, step Ш3 (`specs/024-core-hygiene/spec.md`, `T2413.2`). Payments, clearings and trust-line
batches no longer compute a full-equivalent integrity checkpoint inside their transaction; their
`integrity_audit_log` row stays the record of the operation and says `verification_passed = null` - no check
ran - which is distinct from `false` (a check found a violation) and from `true` (the checks passed). The
empty-string checksum alone could not say it: it already meant "no prior checkpoint". Nothing else changes:
the index on the column stays, no row is rewritten.

DOWNGRADE restores `NOT NULL` and REFUSES while any row holds `null`. Turning "no check ran" into `false` or
`true` would falsify the audit trail, and deleting the rows would lose it - neither is a schema change's
decision. The table is locked against writes between the count and the `ALTER`. Round trip:
`tests/integration/test_p024_migration_032_postgres.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "032_audit_row_may_record_no_check"
down_revision = "031_drop_prepare_locks"
branch_labels = None
depends_on = None

_TABLE = "integrity_audit_log"
_COLUMN = "verification_passed"


def upgrade() -> None:
    op.alter_column(_TABLE, _COLUMN, existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE integrity_audit_log IN SHARE ROW EXCLUSIVE MODE"))
    not_run = int(
        bind.execute(sa.text("SELECT count(*) FROM integrity_audit_log WHERE verification_passed IS NULL")).scalar_one()
    )
    if not_run:
        raise RuntimeError(
            f"refusing to downgrade 032: {not_run} integrity_audit_log row(s) record that no check ran "
            "(verification_passed IS NULL). 031 cannot represent them: rewriting them to false or true would "
            "falsify the audit trail, deleting them would lose it. Keep 032, or decide their fate explicitly "
            "outside this migration first."
        )
    op.alter_column(_TABLE, _COLUMN, existing_type=sa.Boolean(), nullable=False)
