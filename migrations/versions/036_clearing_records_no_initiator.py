"""A clearing records no initiator: `transactions.initiator_id` may be NULL, a PAYMENT still needs one.

Revision ID: 036_clearing_records_no_initiator
Revises: 035_trust_line_status_without_frozen
Create Date: 2026-10-04

Programme 028, F-028-45 (`T2871`, owner В-8: "Зачем нам вообще фиксировать инициатора? ... Нужно избегать лишних
сущностей"). A clearing has no initiator in the protocol - the hub closes a cycle, nobody asks for it - and the column
was filled with the cycle's first debtor (`clearing/service.py`, "Let's pick the first debtor"), a value no reader
needed: the Admin graph and the participant metrics say who took part through `payload.edges`. The payment keeps its
initiator: who paid, the replay identity (`PaymentService._resolve_existing_payment`), access and the lists.

- `initiator_id` drops NOT NULL;
- every existing CLEARING row gets `initiator_id = NULL`: the stored first debtor is a guess the code made, not a
  fact anybody stated, and leaving it would keep two meanings of the column side by side (old rows "an initiator",
  new rows none). Nothing else reads it - the verifier, the ledger and the reconciliation never did;
- CHECK `chk_transaction_payment_has_initiator` (`type <> 'PAYMENT' OR initiator_id IS NOT NULL`) keeps for the
  payment exactly the guarantee NOT NULL gave it. The unique `(initiator_id, type, idempotency_key)` is not touched:
  a clearing's identity is `tx_id` (unique), and NULLs are distinct in it.

DOWNGRADE IS IRREVERSIBLE BY DATA: which participant stood in a clearing row is not kept. The downgrade drops the
CHECK and, before restoring NOT NULL, fills every NULL initiator with the first debtor of the row's cycle, as the old
writer did (`payload.edges[0].debtor` -> `participants.pid`); a row it cannot resolve makes it refuse. Round trip:
`tests/integration/test_p028_e7_migration_036_postgres.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "036_clearing_records_no_initiator"
down_revision = "035_trust_line_status_without_frozen"
branch_labels = None
depends_on = None

_CHECK = "chk_transaction_payment_has_initiator"


def upgrade() -> None:
    op.alter_column("transactions", "initiator_id", nullable=True)
    op.execute("UPDATE transactions SET initiator_id = NULL WHERE type = 'CLEARING'")
    op.create_check_constraint(_CHECK, "transactions", "type <> 'PAYMENT' OR initiator_id IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint(_CHECK, "transactions", type_="check")
    op.execute(
        "UPDATE transactions t SET initiator_id = p.id FROM participants p "
        "WHERE t.initiator_id IS NULL AND p.pid = t.payload::jsonb -> 'edges' -> 0 ->> 'debtor'"
    )
    left = op.get_bind().execute(sa.text("SELECT count(*) FROM transactions WHERE initiator_id IS NULL")).scalar_one()
    if left:
        raise RuntimeError(f"refusing to downgrade 036: {left} transaction(s) have no initiator to restore")
    op.alter_column("transactions", "initiator_id", nullable=False)
