"""One run mode: `chk_simulator_runs_mode` narrows to `mode = 'real'`.

Revision ID: 037_simulator_run_mode_real_only
Revises: 036_clearing_records_no_initiator
Create Date: 2026-10-16

Programme 042, stage 1 (`T4202`; owner, 2026-10-16, "вариант 3"): the server run mode `fixtures` - events drawn on
the scenario's picture with no money behind them - is removed; the API refuses it (422). This narrows the column to
the one mode left.

UPGRADE REFUSES while a row says `mode = 'fixtures'`, naming the count; it deletes nothing (spec 042, "Запрещено": a
migration does not remove rows). Such a row is the history of a run nobody can start any more; whoever owns the
database decides what to do with it (simulator history is disposable) and runs the upgrade again. The table is locked
against writers between the count and the new constraint, so a row cannot slip in between.

DOWNGRADE widens the CHECK back to `mode IN ('fixtures','real')`, migration 013's definition; it always applies.
Round trip: `tests/integration/test_p042_s1_migration_037_postgres.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "037_simulator_run_mode_real_only"
down_revision = "036_clearing_records_no_initiator"
branch_labels = None
depends_on = None

_NAME = "chk_simulator_runs_mode"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE simulator_runs IN SHARE ROW EXCLUSIVE MODE"))
    legacy = int(bind.execute(sa.text("SELECT count(*) FROM simulator_runs WHERE mode = 'fixtures'")).scalar_one())
    if legacy:
        raise RuntimeError(
            f"refusing to upgrade to 037: {legacy} simulator run(s) have mode = 'fixtures', a run mode that no longer "
            "exists (programme 042). The migration deletes no row: remove those runs (simulator history is "
            "disposable) and upgrade again."
        )
    op.drop_constraint(_NAME, "simulator_runs", type_="check")
    op.create_check_constraint(_NAME, "simulator_runs", "mode = 'real'")


def downgrade() -> None:
    op.drop_constraint(_NAME, "simulator_runs", type_="check")
    op.create_check_constraint(_NAME, "simulator_runs", "mode IN ('fixtures','real')")
