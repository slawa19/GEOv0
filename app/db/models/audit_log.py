import uuid
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey('participants.id', ondelete='SET NULL'), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    object_type: Mapped[str | None] = mapped_column(String(50)) # Index handled via composite
    object_id: Mapped[str | None] = mapped_column(String(64)) # Index handled via composite
    reason: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[dict | None] = mapped_column(JSON)
    after_state: Mapped[dict | None] = mapped_column(JSON)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)

    actor = relationship("Participant", foreign_keys=[actor_id])

    # Note: Alembic migration creates idx_audit_log_object on (object_type, object_id)