"""A PAYMENT envelope may carry intent encoding version 2: the CHECK is widened to `IN (1, 2)`.

Revision ID: 027_payment_intent_version_2
Revises: 026_debt_reconciliation_baseline
Create Date: 2026-09-14

Spec 015 / step 5b (`T1505` criterion (b)). Decisions: spec.md, "Ключевое ревью шага 5: `ON-TRACK`" -
the payment envelope gets version 2, recording the pre-state of BOTH directions of every intent pair,
so that the scheduled verifier can recompute the netted deltas in full.

WHY A MIGRATION AND NOT ONLY A CONSTANT. Migration 022 put `CHECK ... IN (1)` on every version column,
so a version-2 envelope is refused by the database until this runs. Only the intent-encoding column is
widened: the schema and money encodings did not change, and their CHECKs still say `IN (1)`.

NOTHING IS REWRITTEN. Version-1 payments written before this stay exactly as they are; the verifier
reads them and checks their structure only, without a claim of full replay.

POSTGRESQL ONLY, like every migration here; the SQLite tiers build the schema from `Base.metadata`
(`app/db/journal_tables.py`, changed in the same slice), and the two paths are compared by
`tests/integration/test_p015_step5b_criterion_b_postgres.py`.

DOWNGRADE REFUSES WHILE A VERSION-2 ENVELOPE EXISTS: the narrower CHECK cannot describe it, and a
downgrade that dropped or rewrote the only record of what a payment declared would be worse than one
that stops.
"""

from alembic import op
import sqlalchemy as sa

revision = "027_payment_intent_version_2"
down_revision = "026_debt_reconciliation_baseline"
branch_labels = None
depends_on = None

_TABLE = "debt_operations"
_CONSTRAINT = "chk_debt_operations_intent_version"
_NEW = "intent_encoding_version IN (1, 2)"
_OLD = "intent_encoding_version IN (1)"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    newer = int(
        bind.execute(
            sa.text(f"SELECT count(*) FROM {_TABLE} WHERE intent_encoding_version <> 1")  # noqa: S608
        ).scalar_one()
    )
    if newer:
        raise RuntimeError(
            f"refusing to narrow {_CONSTRAINT} while {newer} envelope(s) carry an intent encoding version "
            f"other than 1. They are the record of what those payments declared; empty or keep them "
            f"deliberately first."
        )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD)
