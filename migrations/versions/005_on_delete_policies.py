"""Add ON DELETE policies to foreign keys

Revision ID: 005
Revises: 004
Create Date: 2026-01-04

Adds explicit ON DELETE CASCADE/RESTRICT/SET NULL policies for key foreign keys.

Note: This migration is applied for Postgres. SQLite is skipped because test
setup uses Base.metadata.create_all and SQLite FK alterations are non-trivial.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # trust_lines
    op.drop_constraint("trust_lines_from_participant_id_fkey", "trust_lines", type_="foreignkey")
    op.drop_constraint("trust_lines_to_participant_id_fkey", "trust_lines", type_="foreignkey")
    op.drop_constraint("trust_lines_equivalent_id_fkey", "trust_lines", type_="foreignkey")

    op.create_foreign_key(
        "fk_trust_lines_from_participant_id",
        "trust_lines",
        "participants",
        ["from_participant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_trust_lines_to_participant_id",
        "trust_lines",
        "participants",
        ["to_participant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_trust_lines_equivalent_id",
        "trust_lines",
        "equivalents",
        ["equivalent_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # debts
    op.drop_constraint("debts_debtor_id_fkey", "debts", type_="foreignkey")
    op.drop_constraint("debts_creditor_id_fkey", "debts", type_="foreignkey")
    op.drop_constraint("debts_equivalent_id_fkey", "debts", type_="foreignkey")

    op.create_foreign_key(
        "fk_debts_debtor_id",
        "debts",
        "participants",
        ["debtor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_debts_creditor_id",
        "debts",
        "participants",
        ["creditor_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_debts_equivalent_id",
        "debts",
        "equivalents",
        ["equivalent_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # prepare_locks
    op.drop_constraint("prepare_locks_participant_id_fkey", "prepare_locks", type_="foreignkey")
    op.create_foreign_key(
        "fk_prepare_locks_participant_id",
        "prepare_locks",
        "participants",
        ["participant_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # transactions
    op.drop_constraint("transactions_initiator_id_fkey", "transactions", type_="foreignkey")
    op.create_foreign_key(
        "fk_transactions_initiator_id",
        "transactions",
        "participants",
        ["initiator_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # audit_log
    op.drop_constraint("audit_log_actor_id_fkey", "audit_log", type_="foreignkey")
    op.create_foreign_key(
        "fk_audit_log_actor_id",
        "audit_log",
        "participants",
        ["actor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # integrity_checkpoints
    op.drop_constraint(
        "integrity_checkpoints_equivalent_id_fkey",
        "integrity_checkpoints",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_integrity_checkpoints_equivalent_id",
        "integrity_checkpoints",
        "equivalents",
        ["equivalent_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Drop new FKs
    op.drop_constraint("fk_trust_lines_from_participant_id", "trust_lines", type_="foreignkey")
    op.drop_constraint("fk_trust_lines_to_participant_id", "trust_lines", type_="foreignkey")
    op.drop_constraint("fk_trust_lines_equivalent_id", "trust_lines", type_="foreignkey")

    op.drop_constraint("fk_debts_debtor_id", "debts", type_="foreignkey")
    op.drop_constraint("fk_debts_creditor_id", "debts", type_="foreignkey")
    op.drop_constraint("fk_debts_equivalent_id", "debts", type_="foreignkey")

    op.drop_constraint("fk_prepare_locks_participant_id", "prepare_locks", type_="foreignkey")
    op.drop_constraint("fk_transactions_initiator_id", "transactions", type_="foreignkey")
    op.drop_constraint("fk_audit_log_actor_id", "audit_log", type_="foreignkey")
    op.drop_constraint(
        "fk_integrity_checkpoints_equivalent_id",
        "integrity_checkpoints",
        type_="foreignkey",
    )

    # Recreate old (default-named) FKs without explicit ondelete
    op.create_foreign_key(
        "trust_lines_from_participant_id_fkey",
        "trust_lines",
        "participants",
        ["from_participant_id"],
        ["id"],
    )
    op.create_foreign_key(
        "trust_lines_to_participant_id_fkey",
        "trust_lines",
        "participants",
        ["to_participant_id"],
        ["id"],
    )
    op.create_foreign_key(
        "trust_lines_equivalent_id_fkey",
        "trust_lines",
        "equivalents",
        ["equivalent_id"],
        ["id"],
    )

    op.create_foreign_key(
        "debts_debtor_id_fkey",
        "debts",
        "participants",
        ["debtor_id"],
        ["id"],
    )
    op.create_foreign_key(
        "debts_creditor_id_fkey",
        "debts",
        "participants",
        ["creditor_id"],
        ["id"],
    )
    op.create_foreign_key(
        "debts_equivalent_id_fkey",
        "debts",
        "equivalents",
        ["equivalent_id"],
        ["id"],
    )

    op.create_foreign_key(
        "prepare_locks_participant_id_fkey",
        "prepare_locks",
        "participants",
        ["participant_id"],
        ["id"],
    )

    op.create_foreign_key(
        "transactions_initiator_id_fkey",
        "transactions",
        "participants",
        ["initiator_id"],
        ["id"],
    )

    op.create_foreign_key(
        "audit_log_actor_id_fkey",
        "audit_log",
        "participants",
        ["actor_id"],
        ["id"],
    )

    op.create_foreign_key(
        "integrity_checkpoints_equivalent_id_fkey",
        "integrity_checkpoints",
        "equivalents",
        ["equivalent_id"],
        ["id"],
    )
