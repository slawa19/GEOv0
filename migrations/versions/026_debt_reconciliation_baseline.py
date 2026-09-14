"""Debt reconciliation: the per-equivalent baseline, its non-zero edge offsets, and the result rows.

Revision ID: 026_debt_reconciliation_baseline
Revises: 025_debts_participant_fk_restrict
Create Date: 2026-09-14

Spec 015 / step 5a: `T1501` (baseline) and `T1505` criterion (a). Decisions: spec.md, "Ключевое ревью
шага 5: `ON-TRACK`". Model: `app/db/reconciliation_tables.py`, which carries the same names.

SCHEMA ONLY. THIS MIGRATION TAKES NO BASELINE, and that is the decision rather than an omission. A
baseline adopts whatever `debts` holds that the journal does not explain, so taking one here would
silently bless the data that happens to be in the database at upgrade time. On an upgrade, taking the
baseline is an explicit, deliberate cutover - `scripts/take_reconciliation_baseline.py`, run by a
human on a quiet system after writers of the old envelope format have stopped. Until then the
scheduled verifier reports every equivalent `UNVERIFIABLE`, which is the honest state.

A BASELINE DOES NOT CERTIFY THE DEBTS IT ADOPTED. It makes later change checkable; it says nothing about
whether the opening balances were right. PostgreSQL stores that sentence as the baseline table's
comment.

WHAT IS NOT HERE: epochs, re-baselining, a sequence, heads, slots, a hash chain. One header per
equivalent (primary key), and an absent offset row means an exact zero.

FOREIGN KEYS. The baseline and its offsets are `RESTRICT`: they are part of what makes reconciliation
possible, and a participant or equivalent must not disappear from under them. The result rows are
`CASCADE`, following `integrity_checkpoints`: a result is a report, not an obligation, and `RESTRICT`
there would make every equivalent the scheduled loop ever looked at undeletable.

DOWNGRADE REFUSES WHILE A BASELINE EXISTS, for the reason 022 refuses on journal rows: dropping it
cannot be undone and silently turns every checkable equivalent unverifiable. Result rows alone do not
block it - they are recomputed on the next scheduled run.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "026_debt_reconciliation_baseline"
down_revision = "025_debts_participant_fk_restrict"
branch_labels = None
depends_on = None

#: Written out rather than imported, as in 021 and 022: a migration keeps describing the change it made.
MONEY_COLUMN_MAX = "999999999999.99999999"

BASELINE_COMMENT = (
    "Reconciliation baseline (programme 015 T1501). Makes later change to debts checkable against the "
    "journal; it does NOT certify the debts it adopted."
)

_TABLES = (
    "debt_reconciliation_baselines",
    "debt_reconciliation_baseline_offsets",
    "debt_reconciliation_results",
)
_BLOCKING = ("debt_reconciliation_baselines", "debt_reconciliation_baseline_offsets")


def upgrade() -> None:
    op.create_table(
        "debt_reconciliation_baselines",
        sa.Column("equivalent_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("equivalent_id", name="pk_debt_reconciliation_baselines"),
        sa.ForeignKeyConstraint(
            ["equivalent_id"],
            ["equivalents.id"],
            name="fk_debt_reconciliation_baselines_equivalent",
            ondelete="RESTRICT",
        ),
        comment=BASELINE_COMMENT,
    )

    op.create_table(
        "debt_reconciliation_baseline_offsets",
        sa.Column("equivalent_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("debtor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("creditor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("offset_amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.PrimaryKeyConstraint(
            "equivalent_id",
            "debtor_id",
            "creditor_id",
            name="pk_debt_reconciliation_baseline_offsets",
        ),
        sa.ForeignKeyConstraint(
            ["equivalent_id"],
            ["debt_reconciliation_baselines.equivalent_id"],
            name="fk_debt_reconciliation_baseline_offsets_baseline",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["debtor_id"],
            ["participants.id"],
            name="fk_debt_reconciliation_baseline_offsets_debtor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["creditor_id"],
            ["participants.id"],
            name="fk_debt_reconciliation_baseline_offsets_creditor",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "debtor_id <> creditor_id",
            name="chk_debt_reconciliation_baseline_offsets_no_self_loop",
        ),
        sa.CheckConstraint(
            f"offset_amount <> 0 AND abs(offset_amount) <= {MONEY_COLUMN_MAX} "
            f"AND offset_amount <> 'NaN'",
            name="chk_debt_reconciliation_baseline_offsets_amount",
        ),
    )

    op.create_table(
        "debt_reconciliation_results",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("equivalent_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_debt_reconciliation_results"),
        sa.ForeignKeyConstraint(
            ["equivalent_id"],
            ["equivalents.id"],
            name="fk_debt_reconciliation_results_equivalent",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('PASSED', 'FAILED', 'UNVERIFIABLE')",
            name="chk_debt_reconciliation_results_status",
        ),
        sa.CheckConstraint(
            "length(fingerprint) = 64",
            name="chk_debt_reconciliation_results_fingerprint",
        ),
    )
    # At most one LATEST row per equivalent. Result rows record transitions, not observations: an
    # identical verdict only advances `last_checked_at` on the latest row (see the model).
    op.create_index(
        "uq_debt_reconciliation_results_latest",
        "debt_reconciliation_results",
        ["equivalent_id"],
        unique=True,
        postgresql_where=sa.text("is_latest"),
        sqlite_where=sa.text("is_latest"),
    )


def _rows_in(table: str) -> int | None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(table):
        return None
    return int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def downgrade() -> None:
    populated = {table: count for table in _BLOCKING if (count := _rows_in(table))}
    if populated:
        listed = ", ".join(f"{table}={count}" for table, count in sorted(populated.items()))
        raise RuntimeError(
            f"refusing to drop the reconciliation baseline while it holds rows ({listed}). Without it "
            f"every baselined equivalent becomes unverifiable and no upgrade can restore the offsets. "
            f"If that is really intended, empty the tables deliberately first."
        )
    bind = op.get_bind()
    if sa.inspect(bind).has_table("debt_reconciliation_results"):
        op.drop_index(
            "uq_debt_reconciliation_results_latest",
            table_name="debt_reconciliation_results",
        )
    for table in reversed(_TABLES):
        if sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)
