import uuid
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.types import MoneyNumeric, finite_money_clauses

class Debt(Base):
    __tablename__ = "debts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RESTRICT, not CASCADE - T1533 of programme 015, the participant half of the same defect
    # T1524 closed for the equivalent below. Deleting a participant removed every obligation they
    # owed or were owed INSIDE THE DATABASE: no Debt instance loaded, no grant, no `_RowState`, no
    # journal entry - so the debt journal cannot observe it by construction and neither can anything
    # else in the application. Measured on the migrated schema at head 024 before this change:
    # `DELETE FROM participants` returned `DELETE 1` with no error and `SUM(amount)` over the
    # equivalent went 925.31000000 -> 0.
    # The protocol never asks for the row to go: `docs/en/02-protocol-spec.md` §3.1 gives a
    # participant a `status` of `active | suspended | left | deleted`, and `deleted` is that status,
    # not a missing row. There is no participant hard-delete endpoint in `app/`.
    # Migration 025 makes the same change on existing databases.
    debtor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='RESTRICT'), nullable=False, index=True)
    creditor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='RESTRICT'), nullable=False, index=True)
    # RESTRICT, not CASCADE - T1524 of programme 015. Deleting an equivalent must never delete
    # the obligations denominated in it. Under CASCADE the database removed them itself, with no
    # Debt row ever loaded, so no application code, audit or journal hook could see a single
    # obligation disappear. Migration 020 makes the same change on existing databases.
    equivalent_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('equivalents.id', ondelete='RESTRICT'), nullable=False, index=True)
    # `MoneyNumeric`, not `Numeric` - T1526. The DDL is unchanged (`NUMERIC(20, 8)`); what the
    # type adds is a refusal to BIND a non-finite value. It is the only guard that can refuse a
    # `NaN` on SQLite, where the driver turns one into `NULL` and `NOT NULL` - a constraint about a
    # different rule - is what fires today. The database-level half is the CHECK below.
    amount: Mapped[Decimal] = mapped_column(MoneyNumeric(20, 8), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __mapper_args__ = {"version_id_col": version}

    debtor = relationship("Participant", foreign_keys=[debtor_id])
    creditor = relationship("Participant", foreign_keys=[creditor_id])
    equivalent = relationship("Equivalent")

    __table_args__ = (
        UniqueConstraint('debtor_id', 'creditor_id', 'equivalent_id', name='uq_debts_debtor_creditor_equivalent'),
        # T1526. THREE CLAUSES, THREE JOBS, and none of them may be dropped as a simplification:
        #   `amount > 0`                        SIGN - a debt of zero is not a debt.
        #   `amount <= 999999999999.99999999`   MAGNITUDE - the column's own maximum. Refuses
        #                                       nothing NUMERIC(20, 8) accepts on PostgreSQL; on
        #                                       SQLite, where the type is not enforced, it is the
        #                                       only magnitude bound and it is what refuses a
        #                                       positive Infinity (measured - `> 0` alone stores
        #                                       one as REAL inf there).
        #   `amount <> 'NaN'`                   NOT A NUMBER, stated explicitly. The magnitude
        #                                       bound already rejects NaN on PostgreSQL, but only
        #                                       as a side effect of being an upper bound, and a
        #                                       guard that refuses the right value for an unwritten
        #                                       reason is removed by the next editor without them
        #                                       knowing what they removed. This clause is that
        #                                       reason, written down.
        # Why `amount > 0` alone was not enough: PostgreSQL orders NaN ABOVE every number, so the
        # positivity check is TRUE for it, and one NaN row makes every sum over the book NaN.
        # The NAME is kept - it is quoted across programmes 012 and 015 and matched by
        # `tests/unit/test_trustline_conflict_identity.py`'s sibling for trust lines - and what it
        # guards is still "a valid positive amount", now stated completely. Predicate and rationale
        # live in `app/db/types.py::finite_money_clauses`.
        CheckConstraint(
            f"amount > 0 AND {finite_money_clauses('amount')}",
            name='chk_debt_amount_positive',
        ),
        CheckConstraint('debtor_id != creditor_id', name='chk_debt_no_self_loop'),
        Index('ix_debts_debtor_creditor', 'debtor_id', 'creditor_id'),
        Index('ix_debts_equivalent_debtor', 'equivalent_id', 'debtor_id'),
    )