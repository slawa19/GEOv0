"""The reconciliation baseline and the reconciliation result: unmapped Core `Table`s.

Programme 015, step 5a (`T1501`, `T1505` criterion (a)). Decisions: spec.md, "Ключевое ревью шага 5".

WHAT A BASELINE IS. One immutable header row per `Equivalent.id`, and one child row for every edge whose
`offset(edge) = current debt(edge) - sum of every recorded journal delta(edge)` was NOT zero at the
moment the baseline was taken. An absent child row means an exact zero offset. From then on criterion
(a) - `current debt - sum(delta) == offset` for every edge - is checkable.

WHAT A BASELINE IS NOT, and it is written here because this is where the baseline is recorded: IT DOES
NOT CERTIFY THE DEBTS IT ADOPTED. An offset is whatever the database held that the journal does not
explain - an opening balance, a debt written before migration 022, a debt changed by hand. A baseline
only makes LATER change checkable against it. No epochs, no re-baselining, no sequence, no heads, no
slots, no hash chain: a second baseline for the same equivalent is refused by the primary key, and
nothing here re-baselines automatically.

WHAT THE RESULT IS. `PASSED | FAILED | UNVERIFIABLE` and the evidence, one row per TRANSITION of an
equivalent's verdict; an unchanged repeat only advances the latest row's `last_checked_at`. It is deliberately a SEPARATE object. It never enters an integrity checkpoint's
`checks`, `passed`, `status`, `alerts`, nor any `IntegrityAuditLog.verification_passed` - the same reason
`InvariantWithdrawn` (`app/schemas/integrity.py`) is not a nullable boolean: a non-result folded into an
aggregate verdict reads as a result.

WHY CORE TABLES. Same reasoning as `app/db/journal_tables.py`: nothing is meant to reach them through
`session.add`. They live on `Base.metadata` so that `Base.metadata.create_all` and
`alembic upgrade head` (migration 026) build the same thing; both paths are compared by
`tests/integration/test_p015_step5a_reconciliation_postgres.py`. Every constraint is named in both.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Table,
    Uuid,
    func,
    text,
)

from app.db.base import Base
from app.db.types import MONEY_COLUMN_MAX, MoneyNumeric

__all__ = [
    "BASELINE_COMMENT",
    "RECONCILIATION_STATUSES",
    "debt_reconciliation_baseline_offsets",
    "debt_reconciliation_baselines",
    "debt_reconciliation_results",
]

#: The three values a result may carry. `FAILED` dominates `UNVERIFIABLE`.
RECONCILIATION_STATUSES = ("PASSED", "FAILED", "UNVERIFIABLE")

#: Stored as the table comment on PostgreSQL, so the catalogue itself says what a baseline is not.
BASELINE_COMMENT = (
    "Reconciliation baseline (programme 015 T1501). Makes later change to debts checkable against the "
    "journal; it does NOT certify the debts it adopted."
)

_STATUS_LIST = ", ".join("'%s'" % status for status in RECONCILIATION_STATUSES)


debt_reconciliation_baselines = Table(
    "debt_reconciliation_baselines",
    Base.metadata,
    Column("equivalent_id", Uuid(as_uuid=True), nullable=False),
    # Informational only. A timestamp is not an ordering substitute and nothing reads it as one.
    Column("taken_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("equivalent_id", name="pk_debt_reconciliation_baselines"),
    ForeignKeyConstraint(
        ["equivalent_id"],
        ["equivalents.id"],
        name="fk_debt_reconciliation_baselines_equivalent",
        ondelete="RESTRICT",
    ),
    comment=BASELINE_COMMENT,
)


debt_reconciliation_baseline_offsets = Table(
    "debt_reconciliation_baseline_offsets",
    Base.metadata,
    Column("equivalent_id", Uuid(as_uuid=True), nullable=False),
    Column("debtor_id", Uuid(as_uuid=True), nullable=False),
    Column("creditor_id", Uuid(as_uuid=True), nullable=False),
    # Signed: a debt removed around the application before the baseline leaves a negative offset.
    # Not named `offset`, which is an SQL keyword.
    Column("offset_amount", MoneyNumeric(20, 8), nullable=False),
    PrimaryKeyConstraint(
        "equivalent_id", "debtor_id", "creditor_id", name="pk_debt_reconciliation_baseline_offsets"
    ),
    ForeignKeyConstraint(
        ["equivalent_id"],
        ["debt_reconciliation_baselines.equivalent_id"],
        name="fk_debt_reconciliation_baseline_offsets_baseline",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["debtor_id"],
        ["participants.id"],
        name="fk_debt_reconciliation_baseline_offsets_debtor",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["creditor_id"],
        ["participants.id"],
        name="fk_debt_reconciliation_baseline_offsets_creditor",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "debtor_id <> creditor_id", name="chk_debt_reconciliation_baseline_offsets_no_self_loop"
    ),
    # A zero offset is represented by the ABSENCE of a row, never by a row holding zero: two spellings
    # of one fact are two things a reader has to agree on.
    CheckConstraint(
        "offset_amount <> 0 AND abs(offset_amount) <= "
        + MONEY_COLUMN_MAX
        + " AND offset_amount <> 'NaN'",
        name="chk_debt_reconciliation_baseline_offsets_amount",
    ),
)


#: TRANSITIONS, NOT OBSERVATIONS (coordinator decision, step 5a review round). A result row is written
#: when an equivalent's verdict CHANGES - status, the sorted uncapped findings, or the missing evidence,
#: hashed into `fingerprint`. An identical repeat only advances `last_checked_at` on the latest row. So
#: every distinct FAILED, UNVERIFIABLE and later PASSED is kept, and growth is bounded by state changes.
#:
#: `is_latest` MARKS the row a repeat is compared against, and at most one row per equivalent may carry
#: it (partial unique index, both dialects). It exists so that "the latest row" is a stored fact rather
#: than an order of timestamps - a clock step would otherwise make an older row look newest.
debt_reconciliation_results = Table(
    "debt_reconciliation_results",
    Base.metadata,
    Column("id", Uuid(as_uuid=True), nullable=False, default=uuid.uuid4),
    Column("equivalent_id", Uuid(as_uuid=True), nullable=False),
    Column("status", String(16), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("detail", JSON, nullable=False),
    # When this verdict was first observed, and when it was last observed unchanged.
    Column("checked_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_checked_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("is_latest", Boolean, nullable=False),
    PrimaryKeyConstraint("id", name="pk_debt_reconciliation_results"),
    # CASCADE, following `integrity_checkpoints`: a result is a report about an equivalent, not an
    # obligation, and RESTRICT here would make every equivalent the scheduled loop has ever looked at
    # undeletable. The baseline above is RESTRICT because it is part of what makes the check possible.
    ForeignKeyConstraint(
        ["equivalent_id"],
        ["equivalents.id"],
        name="fk_debt_reconciliation_results_equivalent",
        ondelete="CASCADE",
    ),
    CheckConstraint("status IN (" + _STATUS_LIST + ")", name="chk_debt_reconciliation_results_status"),
    CheckConstraint("length(fingerprint) = 64", name="chk_debt_reconciliation_results_fingerprint"),
    Index(
        "uq_debt_reconciliation_results_latest",
        "equivalent_id",
        unique=True,
        postgresql_where=text("is_latest"),
    ),
)
