"""`flush_count <= effect_count` is not true of a real writer, so the completion CHECK drops it.

Revision ID: 023_debt_journal_counts
Revises: 022_debt_journal
Create Date: 2026-09-12

Spec 015 / `B4`, step 4 slice C. Found by ACTIVATING the journal, which is the only way it could
have been found: slice A built `022_debt_journal` against a stand whose every flush produced an
entry, and under that assumption `flush_count <= effect_count` reads like an invariant.

IT IS NOT ONE. `PaymentEngine._apply_flow` (`app/core/payments/engine.py`) answers a
`StaleDataError` by rolling back to a savepoint, expiring the identity map and flushing again. The
losing attempt flushed - so the operation counted a flush - and its journal entries went with the
savepoint, so it contributed no effect. One operation, two flushes, one entry, and
`022_debt_journal`'s CHECK refused to complete it. That is an ordinary optimistic-lock retry on the
main payment path, not an exotic case, and the counterexample that surfaced it is `C18` in
`tests/unit/test_p015_b4_entries_and_money.py`.

The all-or-nothing pair went with it, for the same reason read the other way round: an operation
whose only flush rolled back completes with `flush_count = 1` and `effect_count = 0`, which
`(flush_count = 0 AND effect_count = 0) OR (both > 0)` also refuses.

WHAT REMAINS IS WHAT IS ACTUALLY TRUE: an entry cannot exist without a flush that wrote it, so
`effect_count > 0` implies `flush_count > 0`. The reverse does not hold and never did. Everything
else about the completion columns is unchanged - they still move together, the digest is still 64
characters, and neither count may be negative.

PostgreSQL only, like every migration here; the SQLite tiers build their schema from
`Base.metadata` (`app/db/journal_tables.py`, changed in the same slice).
"""

from alembic import op

revision = "023_debt_journal_counts"
down_revision = "022_debt_journal"
branch_labels = None
depends_on = None

_CONSTRAINT = "chk_debt_operations_completion"

_OLD = (
    "("
    " state = 'OPEN'"
    " AND completed_at IS NULL AND flush_count IS NULL"
    " AND effect_count IS NULL AND effect_digest IS NULL"
    ") OR ("
    " state = 'COMPLETED'"
    " AND completed_at IS NOT NULL AND flush_count IS NOT NULL"
    " AND effect_count IS NOT NULL AND effect_digest IS NOT NULL"
    " AND length(effect_digest) = 64"
    " AND flush_count >= 0 AND effect_count >= 0"
    " AND flush_count <= effect_count"
    " AND ((flush_count = 0 AND effect_count = 0)"
    "      OR (flush_count > 0 AND effect_count > 0))"
    ")"
)

_NEW = (
    "("
    " state = 'OPEN'"
    " AND completed_at IS NULL AND flush_count IS NULL"
    " AND effect_count IS NULL AND effect_digest IS NULL"
    ") OR ("
    " state = 'COMPLETED'"
    " AND completed_at IS NOT NULL AND flush_count IS NOT NULL"
    " AND effect_count IS NOT NULL AND effect_digest IS NOT NULL"
    " AND length(effect_digest) = 64"
    " AND flush_count >= 0 AND effect_count >= 0"
    " AND (flush_count > 0 OR effect_count = 0)"
    ")"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint(_CONSTRAINT, "debt_operations", type_="check")
    op.create_check_constraint(_CONSTRAINT, "debt_operations", _NEW)


def downgrade() -> None:
    """Restores the narrower predicate, and will REFUSE if any row already violates it.

    That refusal is the honest outcome: a completed operation with more flushes than effects is a
    real record of a real retry, and there is no way to express it under the old constraint. A
    downgrade that dropped such rows, or that installed a constraint the table does not satisfy,
    would be worse than one that stops.
    """

    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_constraint(_CONSTRAINT, "debt_operations", type_="check")
    op.create_check_constraint(_CONSTRAINT, "debt_operations", _OLD)
