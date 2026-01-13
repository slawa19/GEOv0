import uuid
from sqlalchemy import CheckConstraint, DateTime, JSON, SmallInteger, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default='person')
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='active', index=True)
    verification_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    profile: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("type IN ('person', 'business', 'hub')", name='chk_participant_type'),
        CheckConstraint("status IN ('active', 'suspended', 'left', 'deleted')", name='chk_participant_status'),
    )