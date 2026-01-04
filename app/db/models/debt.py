import uuid
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class Debt(Base):
    __tablename__ = "debts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    debtor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='CASCADE'), nullable=False, index=True)
    creditor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='CASCADE'), nullable=False, index=True)
    equivalent_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('equivalents.id', ondelete='CASCADE'), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    debtor = relationship("Participant", foreign_keys=[debtor_id])
    creditor = relationship("Participant", foreign_keys=[creditor_id])
    equivalent = relationship("Equivalent")

    __table_args__ = (
        UniqueConstraint('debtor_id', 'creditor_id', 'equivalent_id', name='uq_debts_debtor_creditor_equivalent'),
        CheckConstraint('amount >= 0', name='chk_debt_amount_positive'),
        Index('ix_debts_debtor_creditor', 'debtor_id', 'creditor_id'),
    )