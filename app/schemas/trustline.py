from decimal import Decimal
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field

class TrustLineBase(BaseModel):
    policy: Optional[Dict[str, Any]] = None

class TrustLine(TrustLineBase):
    id: UUID
    from_pid: str = Field(..., serialization_alias="from")
    to_pid: str = Field(..., serialization_alias="to")
    equivalent_code: str = Field(..., serialization_alias="equivalent")
    limit: Decimal
    used: Decimal
    available: Decimal
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True

class TrustLineCreateRequest(BaseModel):
    to: str
    equivalent: str
    limit: Decimal
    policy: Optional[Dict[str, Any]] = None

class TrustLineUpdateRequest(BaseModel):
    limit: Optional[Decimal] = None
    policy: Optional[Dict[str, Any]] = None

class TrustLinesList(BaseModel):
    items: List[TrustLine]