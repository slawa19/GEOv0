"""A run's seed fits its column: `simulator_runs.seed` becomes `bigint`.

Revision ID: 034_simulator_run_seed_bigint
Revises: 033_trust_line_close_requested_at
Create Date: 2026-10-04

Programme 028, F-028-1 (`T2811`). The seed is the first 4 bytes of sha256(run_id), up to 2**32-1; as `integer`
about half of all runs failed to persist. The type widens, no value is rewritten. DOWNGRADE REFUSES while a row
holds a seed >= 2**31: `integer` cannot hold it, and masking would change the run's seed. Round trip:
`tests/integration/test_p028_e1_migration_034_postgres.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "034_simulator_run_seed_bigint"
down_revision = "033_trust_line_close_requested_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("simulator_runs", "seed", type_=sa.BigInteger(), existing_type=sa.Integer(), existing_nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE simulator_runs IN SHARE ROW EXCLUSIVE MODE"))
    wide = int(bind.execute(sa.text("SELECT count(*) FROM simulator_runs WHERE seed >= 2147483648")).scalar_one())
    if wide:
        raise RuntimeError(
            f"refusing to downgrade 034: {wide} simulator run(s) hold a seed >= 2**31 that 033's integer column "
            "cannot represent. Delete those runs (simulator history is disposable) or keep 034."
        )
    op.alter_column("simulator_runs", "seed", type_=sa.Integer(), existing_type=sa.BigInteger(), existing_nullable=True)
