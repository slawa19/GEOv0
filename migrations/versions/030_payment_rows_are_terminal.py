"""The legacy payment-write fence: a `PAYMENT` row of `transactions` is `COMMITTED` or `ABORTED`.

Revision ID: 030_payment_rows_are_terminal
Revises: 029_debt_journal_by_the_database
Create Date: 2026-09-25

Programme 019, stage 4 (`specs/019-payment-one-transaction/spec.md`, "Перевод", `FORK-3`, `T1906`).
From stage 4 the hub executes a REST payment as ONE transaction and inserts its row directly as
`COMMITTED` (or `ABORTED` for a definitive refusal); `NEW`/`PREPARED` are no longer persisted, and
`app/core/recovery.py`, which terminalised stuck payments, is gone. This migration adds one immediate
CHECK:

    type <> 'PAYMENT' OR state IN ('COMMITTED', 'ABORTED')

WHAT IT IS: a fence against the LEGACY PAYMENT WRITE. A pre-019 binary inserts a payment as `NEW`
first, so after this migration it fails on that very insert instead of leaving a non-terminal payment
that nothing would ever finish. A PostgreSQL CHECK is checked immediately - an uncommitted `NEW` is
refused too - which is why every intermediate-state write left the application BEFORE this migration
(stage 4, part a). WHAT IT IS NOT: a refusal of every operation of the old binary. `CLEARING` (which
writes its own `NEW`, `docs/ru/09-decisions-and-defaults.md`) and every other transaction type are
deliberately outside it. Terminal rows, their fingerprints and the debt journal are not touched.

THE CUTOVER IS DRAINED (`docs/ru/05-deployment.md`; mixed versions are not supported): stop intake and
wait for the writers, let the OLD binary's recovery finish every payment and empty `prepare_locks`, stop
it, apply this migration, start the new code. The migration REFUSES while any `PAYMENT` row is not
`COMMITTED`/`ABORTED` or any `prepare_locks` row exists - it never terminalises them itself: whether a
prepared payment commits or aborts is the old recovery's decision, not a schema change's. The two
tables are locked against writes before the count, so the count is the state the CHECK is added over.

DOWNGRADE IS ALLOWED (under the same stop): it drops the CHECK and nothing else, back to the pre-019
implementation at 029. The reservations table is untouched here; stage 5's `031` drops it and restores
its full empty schema on its own downgrade.

THE SAME CHECK IS DECLARED ON THE MODEL (`app/db/models/transaction.py`) so that
`Base.metadata.create_all` builds the same table; `tests/integration/test_p019_migration_030_postgres.py`
compares the two paths and exercises the refusal, the fence and the way back.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "030_payment_rows_are_terminal"
down_revision = "029_debt_journal_by_the_database"
branch_labels = None
depends_on = None

_TABLE = "transactions"
_CONSTRAINT = "chk_transaction_payment_terminal"
_CONDITION = "type <> 'PAYMENT' OR state IN ('COMMITTED', 'ABORTED')"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # No payment or reservation may be written between the count and the constraint.
    bind.execute(sa.text("LOCK TABLE transactions, prepare_locks IN SHARE ROW EXCLUSIVE MODE"))
    pending = bind.execute(
        sa.text(
            "SELECT state, count(*) FROM transactions "
            "WHERE type = 'PAYMENT' AND state NOT IN ('COMMITTED', 'ABORTED') "
            "GROUP BY state ORDER BY state"
        )
    ).all()
    reservations = int(bind.execute(sa.text("SELECT count(*) FROM prepare_locks")).scalar_one())
    if pending or reservations:
        by_state = ", ".join(f"{state}: {count}" for state, count in pending) or "none"
        raise RuntimeError(
            "refusing to apply 030 on an undrained database: "
            f"{sum(count for _state, count in pending)} PAYMENT transaction(s) are not terminal "
            f"({by_state}) and {reservations} prepare_locks row(s) exist. "
            "Drain first under the pre-019 binary: stop intake and every writer, let its recovery "
            "finish every payment until prepare_locks is empty, stop it, then upgrade "
            "(docs/ru/05-deployment.md, the drained cutover for migration 030)."
        )

    op.create_check_constraint(_CONSTRAINT, _TABLE, _CONDITION)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
