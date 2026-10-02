"""A trust line remembers that its creditor asked to close it: `trust_lines.close_requested_at`.

Revision ID: 033_trust_line_close_requested_at
Revises: 032_audit_row_may_record_no_check
Create Date: 2026-10-02

Programme 026, slice S3 (`specs/026-geo-alignment/spec.md`, `T2603.1`; owner В1 2026-09-29, fork 1 of `T2600`).
A close with debt is a REQUEST: the limit goes to 0, the line stays live until the debt it supports is 0, and
the book closes it then. The timestamp says both that and when; `NULL` = no request, so every existing row
(closed ones and active ones with limit 0 included) gets `NULL`. `CHECK`: a requested line has limit 0. The
live-line partial unique index is not touched - a requested line is live and blocks a new one.

DOWNGRADE REFUSES while a live line holds a request: dropping the column would silently turn "close when repaid"
back into an ordinary zero-limit line; nothing here rewrites a limit or a debt. A debt above a lowered limit is
NOT checked: that state exists since 026 S2 without a schema change, and the application at 032 handles it -
returning to an application before 026 is a separate decision (spec 026, fork 1). The table is locked against
writes between the count and the `ALTER`s; writers must be stopped for a coordinated rollback. Round trip: `tests/integration/test_p026_s3_migration_033_postgres.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "033_trust_line_close_requested_at"
down_revision = "032_audit_row_may_record_no_check"
branch_labels = None
depends_on = None

_CHECK = "chk_trust_line_close_request_zero_limit"


def upgrade() -> None:
    op.add_column("trust_lines", sa.Column("close_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(_CHECK, "trust_lines", 'close_requested_at IS NULL OR "limit" = 0')


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE trust_lines IN SHARE ROW EXCLUSIVE MODE"))
    pending = int(bind.execute(sa.text(
        "SELECT count(*) FROM trust_lines WHERE close_requested_at IS NOT NULL AND status <> 'closed'")).scalar_one())
    if pending:
        raise RuntimeError(
            f"refusing to downgrade 033: {pending} live trust line(s) hold a close request. 032 cannot represent "
            "it: the line would become an ordinary zero-limit line that never closes. Let the debts be repaid "
            "(the line then closes) or decide their fate explicitly outside this migration first."
        )
    op.drop_constraint(_CHECK, "trust_lines", type_="check")
    op.drop_column("trust_lines", "close_requested_at")
