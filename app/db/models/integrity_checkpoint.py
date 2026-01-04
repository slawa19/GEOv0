import uuid
from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class IntegrityCheckpoint(Base):
    __tablename__ = "integrity_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    equivalent_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('equivalents.id', ondelete='CASCADE'), nullable=False, index=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    invariants_status: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    equivalent = relationship("Equivalent")