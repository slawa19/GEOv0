import uuid
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, Numeric, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class TrustLine(Base):
    __tablename__ = "trust_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_participant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='CASCADE'), nullable=False, index=True)
    to_participant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='CASCADE'), nullable=False, index=True)
    equivalent_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('equivalents.id', ondelete='CASCADE'), nullable=False, index=True)
    limit: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    policy: Mapped[dict | None] = mapped_column(JSON, default=lambda: {
        'auto_clearing': True,
        'can_be_intermediate': True,
        'max_hop_usage': None,
        'daily_limit': None,
        'blocked_participants': []
    })
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='active', index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    from_participant = relationship("Participant", foreign_keys=[from_participant_id])
    to_participant = relationship("Participant", foreign_keys=[to_participant_id])
    equivalent = relationship("Equivalent")

    __table_args__ = (
        UniqueConstraint('from_participant_id', 'to_participant_id', 'equivalent_id', name='uq_trust_lines_from_to_equivalent'),
        CheckConstraint("status IN ('active', 'frozen', 'closed')", name='chk_trust_line_status'),
        CheckConstraint('"limit" >= 0', name='chk_trust_line_limit_positive'),
        Index('ix_trust_lines_from_status', 'from_participant_id', 'status'),
    )