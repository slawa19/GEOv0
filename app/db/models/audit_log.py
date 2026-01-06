import uuid
from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, Uuid, func
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


class IntegrityAuditLog(Base):
    """Integrity audit trail (spec section 11.4.2)."""

    __tablename__ = "integrity_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    tx_id: Mapped[str | None] = mapped_column(String(64), index=True)
    equivalent_code: Mapped[str] = mapped_column(String(16), nullable=False)
    state_checksum_before: Mapped[str] = mapped_column(String(64), nullable=False)
    state_checksum_after: Mapped[str] = mapped_column(String(64), nullable=False)
    affected_participants: Mapped[dict] = mapped_column(JSON, nullable=False)
    invariants_checked: Mapped[dict] = mapped_column(JSON, nullable=False)
    verification_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    error_details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())