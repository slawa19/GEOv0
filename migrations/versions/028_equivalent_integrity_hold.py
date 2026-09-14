"""The integrity hold: one nullable column `equivalents.integrity_hold_result_id`.

Revision ID: 028_equivalent_integrity_hold
Revises: 027_payment_intent_version_2
Create Date: 2026-09-14

Spec 015 / step 5c (`T1516` reaction, `T1546` hold). Decisions: the step 5c brief (Codex design review) -
ONE nullable column referencing the confirmed `FAILED` row in `debt_reconciliation_results`; no timestamp,
no separate table. It lives on the `equivalents` row so that the T1544 refusal reads it in the same
statement as `is_active`, under the same `FOR SHARE` and the same owner lock.

ON DELETE RESTRICT - the evidence of a hold cannot be deleted while held - and why, is written on the model
(`app/db/models/equivalent.py`).

NO BACKFILL: nothing is held until the scheduled reaction confirms a `FAILED`.

POSTGRESQL ONLY, like every migration here; the SQLite tiers build the schema from `Base.metadata`, and an
existing local SQLite file gets the column from `_sqlite_ensure_equivalents_integrity_hold_column`
(`app/main.py`). The two PostgreSQL construction paths are compared by
`tests/integration/test_p015_step5c_hold_races_postgres.py`.

DOWNGRADE REFUSES WHILE ANY EQUIVALENT IS HELD: dropping the column would let money move again in an
equivalent whose ledger was found inconsistent, with no record that it had been stopped.
"""

from alembic import op
import sqlalchemy as sa

revision = "028_equivalent_integrity_hold"
down_revision = "027_payment_intent_version_2"
branch_labels = None
depends_on = None

_TABLE = "equivalents"
_COLUMN = "integrity_hold_result_id"
_FK = "fk_equivalents_integrity_hold_result"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        _FK,
        _TABLE,
        "debt_reconciliation_results",
        [_COLUMN],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    held = int(
        bind.execute(
            sa.text(f"SELECT count(*) FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL")  # noqa: S608
        ).scalar_one()
    )
    if held:
        raise RuntimeError(
            f"refusing to drop {_TABLE}.{_COLUMN} while {held} equivalent(s) are under an integrity hold. "
            f"Clear them deliberately first (POST /admin/equivalents/{{code}}/integrity-hold/clear)."
        )
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, _COLUMN)
