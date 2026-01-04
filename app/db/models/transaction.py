import uuid
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tx_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    initiator_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='RESTRICT'), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    signatures: Mapped[list | None] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default='NEW', index=True)
    error: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    initiator = relationship("Participant", foreign_keys=[initiator_id])

    __table_args__ = (
        CheckConstraint("type IN ('TRUST_LINE_CREATE', 'TRUST_LINE_UPDATE', 'TRUST_LINE_CLOSE', 'PAYMENT', 'CLEARING', 'COMPENSATION', 'COMMODITY_REDEMPTION')", name='chk_transaction_type'),
        CheckConstraint("state IN ('NEW', 'ROUTED', 'PREPARE_IN_PROGRESS', 'PREPARED', 'COMMITTED', 'ABORTED', 'PROPOSED', 'WAITING', 'REJECTED')", name='chk_transaction_state'),
        UniqueConstraint('initiator_id', 'type', 'idempotency_key', name='uq_transactions_initiator_type_idempotency'),
    )