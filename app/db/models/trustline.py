import uuid
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, Numeric, String, Uuid, func, text
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
        # Uniqueness holds among LIVE rows only, matching the protocol precondition of
        # TRUST_LINE_CREATE: «Не существует активной линии (from, to, equivalent)»
        # (docs/ru/02-protocol-spec.md:333).  Closing keeps the row (`:379`), so a closed
        # incarnation must not block a new one — see migration
        # 019_trust_lines_partial_unique_live and spec 009 (F-009-3 / F-009-4).
        Index(
            'uq_trust_lines_live_from_to_equivalent',
            'from_participant_id',
            'to_participant_id',
            'equivalent_id',
            unique=True,
            sqlite_where=text("status <> 'closed'"),
            postgresql_where=text("status <> 'closed'"),
        ),
        CheckConstraint("status IN ('active', 'frozen', 'closed')", name='chk_trust_line_status'),
        CheckConstraint('"limit" >= 0', name='chk_trust_line_limit_positive'),
        Index('ix_trust_lines_from_status', 'from_participant_id', 'status'),
    )