import uuid
from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class PrepareLock(Base):
    __tablename__ = "prepare_locks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tx_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    participant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id'), nullable=False)
    effects: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    participant = relationship("Participant", foreign_keys=[participant_id])

    __table_args__ = (
        UniqueConstraint('tx_id', 'participant_id', name='uq_prepare_locks_tx_participant'),
    )