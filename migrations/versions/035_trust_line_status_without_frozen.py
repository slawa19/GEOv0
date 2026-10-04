"""A trust line is `active` or `closed`: the status `frozen` is removed.

Revision ID: 035_trust_line_status_without_frozen
Revises: 034_simulator_run_seed_bigint
Create Date: 2026-10-04

Programme 028, F-028-29 (`T2832`, owner В-2): a freeze exists only on the participant (`participants.status =
'suspended'`), and a suspended participant takes no part in money (F-028-28, `T2831`, which lands first - the
participant's row, not the line's status, is what refuses). Every `frozen` line becomes `active` with its limit, policy
and close request unchanged; the CHECK then admits `active` and `closed` only.

DOWNGRADE IS IRREVERSIBLE BY DATA: which lines were `frozen` is not stored, so the downgrade restores only the old
CHECK (admitting `frozen` again) and leaves every line as it is. Round trip:
`tests/integration/test_p028_e3_migration_035_postgres.py`.
"""

from __future__ import annotations

from alembic import op

revision = "035_trust_line_status_without_frozen"
down_revision = "034_simulator_run_seed_bigint"
branch_labels = None
depends_on = None

_CHECK = "chk_trust_line_status"


def upgrade() -> None:
    op.execute("UPDATE trust_lines SET status = 'active' WHERE status = 'frozen'")
    op.drop_constraint(_CHECK, "trust_lines", type_="check")
    op.create_check_constraint(_CHECK, "trust_lines", "status IN ('active', 'closed')")


def downgrade() -> None:
    # Irreversible by data (see the module docstring): the old CHECK only, no line becomes `frozen` again.
    op.drop_constraint(_CHECK, "trust_lines", type_="check")
    op.create_check_constraint(_CHECK, "trust_lines", "status IN ('active', 'frozen', 'closed')")
