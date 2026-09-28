from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class StoredEquivalent(BaseModel):
    """Read projection that keeps pre-contract rows visible to operators."""

    code: str
    symbol: Optional[str] = None
    description: Optional[str] = None
    precision: int
    metadata: Optional[dict[str, Any]] = Field(
        default=None, validation_alias="metadata_", serialization_alias="metadata"
    )
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Equivalent(StoredEquivalent):
    """Strict response for successful mutations that enforce current bounds."""

    code: str = Field(pattern=r"^[A-Z0-9_]{1,16}$")
    # 0..8, not 0..18 (012 / S1, 2026-08-25): the storage scale of `Numeric(20, 8)` and the
    # protocol's own declaration (`docs/ru/02-protocol-spec.md:155`). `StoredEquivalent` above
    # deliberately keeps NO bound, so legacy rows outside the domain stay readable and
    # repairable through `PATCH /admin/equivalents/{code}`.
    precision: int = Field(ge=0, le=8)


class EquivalentsList(BaseModel):
    items: list[StoredEquivalent]
