from decimal import Decimal
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

class TrustLineBase(BaseModel):
    policy: Optional[Dict[str, Any]] = None

class TrustLine(TrustLineBase):
    id: UUID
    from_pid: str = Field(..., serialization_alias="from")
    to_pid: str = Field(..., serialization_alias="to")
    from_display_name: Optional[str] = None
    to_display_name: Optional[str] = None
    equivalent_code: str = Field(..., serialization_alias="equivalent")
    limit: Decimal
    used: Decimal
    available: Decimal
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class TrustLineCreateRequest(BaseModel):
    to: str
    equivalent: str
    limit: Decimal = Field(..., ge=0)
    policy: Optional[Dict[str, Any]] = None
    signature: str

class TrustLineUpdateRequest(BaseModel):
    limit: Optional[Decimal] = Field(default=None, ge=0)
    policy: Optional[Dict[str, Any]] = None
    signature: str


class TrustLineCloseRequest(BaseModel):
    signature: str

class TrustLinesList(BaseModel):
    items: List[TrustLine]