import uuid
from sqlalchemy import Boolean, CheckConstraint, DateTime, JSON, SmallInteger, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, validates
from app.db.base import Base
from app.utils.validation import validate_equivalent_code, validate_equivalent_precision

class Equivalent(Base):
    __tablename__ = "equivalents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    precision: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("code = upper(code)", name="chk_equivalents_code_upper"),
    )

    @validates("code")
    def validate_code(self, _key: str, value: str) -> str:
        validate_equivalent_code(value)
        return value

    @validates("precision")
    def validate_precision(self, _key: str, value: int) -> int:
        return validate_equivalent_precision(value)
