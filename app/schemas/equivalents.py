from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Equivalent(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9_]{1,16}$")
    symbol: Optional[str] = None
    description: Optional[str] = None
    precision: int = Field(ge=0, le=18)
    metadata: Optional[dict[str, Any]] = Field(
        default=None, validation_alias="metadata_", serialization_alias="metadata"
    )
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("created_at", "updated_at")
    @classmethod
    def attach_utc_to_naive_database_timestamps(cls, value: datetime) -> datetime:
        # SQLite drops timezone metadata even for DateTime(timezone=True). The
        # wire contract is RFC 3339, so interpret those server timestamps as UTC.
        if value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class EquivalentsList(BaseModel):
    items: list[Equivalent]
