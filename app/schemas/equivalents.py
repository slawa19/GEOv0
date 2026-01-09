from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class Equivalent(BaseModel):
    code: str
    symbol: Optional[str] = None
    description: Optional[str] = None
    precision: int
    metadata: Optional[dict[str, Any]] = Field(default=None, validation_alias="metadata_", serialization_alias="metadata")
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class EquivalentsList(BaseModel):
    items: list[Equivalent]
