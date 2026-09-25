"""Drop `prepare_locks`: REST payments hold no durable reservations.

Revision ID: 031_drop_prepare_locks
Revises: 030_payment_rows_are_terminal
Create Date: 2026-09-25

Programme 019, stage 5 (`specs/019-payment-one-transaction/spec.md`, `T1909`; fourth consultation,
precondition 4). Since stage 4 no code path writes a reservation, and since stage 5 no code path reads one:
the router, the payment's capacity read and the clearing's reserved-pair skip are gone, and the ORM model
`PrepareLock` with them. Concurrent money writers are coordinated by SERIALIZABLE, whole-transaction
retries and ONE equivalent advisory lock (shared for payments, exclusive for the clearing) - protocol §7.6.1.

THE CUTOVER IS DRAINED, like `030` (`docs/ru/05-deployment.md`; mixed versions are not supported): stop
intake and every writer, apply, start the new code. The upgrade takes the table `ACCESS EXCLUSIVE`, counts
its rows and REFUSES if any exists - a reservation left in the table would mean a writer of the old binary
is still running or a drain was skipped, and silently discarding it is not a schema change's decision.
Then `DROP TABLE prepare_locks`. Transactions, debts and the debt journal are not touched.

DOWNGRADE IS ALLOWED (under the same stop): it restores the FULL EMPTY schema of the table as migrations
`001`, `004`, `005`, `006` and `014` left it - the columns in their order with their types, nullability and
defaults (`id` UUID `gen_random_uuid()`, `tx_id` varchar(64), `participant_id` UUID, `effects` JSONB,
`expires_at` and `created_at` timestamptz, `created_at` defaulting to `now()`, `lock_type` varchar(16)
without a default), the primary key, the unique `(tx_id, participant_id)`, the `lock_type` CHECK, both
foreign keys with their delete behaviour (participant `ON DELETE CASCADE`, `tx_id` -> `transactions.tx_id`
`NO ACTION`), the indexes on `tx_id`, `expires_at`, `lock_type`, `(participant_id, expires_at)` and the GIN
index on `effects`. It recreates no rows: there were none. The round trip is compared, catalogue against
catalogue, by `tests/integration/test_p019_migration_031_postgres.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "031_drop_prepare_locks"
down_revision = "030_payment_rows_are_terminal"
branch_labels = None
depends_on = None

_TABLE = "prepare_locks"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Nothing may write a reservation between the count and the drop.
    bind.execute(sa.text("LOCK TABLE prepare_locks IN ACCESS EXCLUSIVE MODE"))
    reservations = int(bind.execute(sa.text("SELECT count(*) FROM prepare_locks")).scalar_one())
    if reservations:
        raise RuntimeError(
            f"refusing to apply 031 on an undrained database: {reservations} prepare_locks row(s) exist. "
            "No 019 binary writes a reservation, so a writer of the pre-019 binary is still running or its "
            "drain was skipped. Stop every writer, let the pre-019 recovery empty the table (the drained "
            "cutover of migration 030, docs/ru/05-deployment.md), then upgrade. This migration never "
            "discards reservations itself."
        )
    op.drop_table(_TABLE)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # 001: the table, its primary key and its column defaults.
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tx_id", sa.String(64), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effects", postgresql.JSONB, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        # 004: added NOT NULL with a default that 004 itself then dropped.
        sa.Column("lock_type", sa.String(length=16), nullable=False),
    )
    op.create_unique_constraint("uq_prepare_locks_tx_participant", _TABLE, ["tx_id", "participant_id"])
    op.create_index("idx_prepare_locks_tx_id", _TABLE, ["tx_id"])
    op.create_index("idx_prepare_locks_expires_at", _TABLE, ["expires_at"])
    # 004
    op.create_index("ix_prepare_locks_lock_type", _TABLE, ["lock_type"], unique=False)
    op.create_check_constraint("chk_prepare_locks_lock_type", _TABLE, "lock_type IN ('PAYMENT','CLEARING')")
    # 005: the participant foreign key, CASCADE.
    op.create_foreign_key(
        "fk_prepare_locks_participant_id",
        _TABLE,
        "participants",
        ["participant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # 006
    op.execute("CREATE INDEX IF NOT EXISTS ix_prepare_locks_effects_gin ON prepare_locks USING GIN (effects)")
    # 014: the transaction foreign key (NO ACTION) and the participant/expiry index.
    op.create_foreign_key("fk_prepare_locks_tx_id", _TABLE, "transactions", ["tx_id"], ["tx_id"])
    op.create_index(
        "ix_prepare_locks_participant_expires_at",
        _TABLE,
        ["participant_id", "expires_at"],
        unique=False,
    )
