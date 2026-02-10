from typing import List, Optional, Any, Dict

from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from decimal import Decimal

class CapacityResponse(BaseModel):
    can_pay: bool
    max_amount: str
    routes_count: int
    estimated_hops: int

class MaxFlowPath(BaseModel):
    path: List[str]
    capacity: str

class Bottleneck(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    limit: str
    used: str
    available: str

class MaxFlowResponse(BaseModel):
    max_amount: str
    paths: List[MaxFlowPath]
    bottlenecks: List[Bottleneck]
    algorithm: str
    computed_at: str

class PaymentConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_hops: Optional[int] = Field(default=None, ge=1, le=10)
    max_paths: Optional[int] = Field(default=None, ge=1, le=10)
    timeout_ms: Optional[int] = Field(default=None, ge=1, le=120_000)
    avoid: Optional[List[str]] = None

class PaymentCreateRequest(BaseModel):
    tx_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    to: str
    equivalent: str
    amount: str
    description: Optional[str] = None
    constraints: Optional[PaymentConstraints] = None
    signature: str


class PaymentRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: List[str]
    amount: str


class PaymentError(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

class PaymentResult(BaseModel):
    tx_id: str
    status: str
    from_: str = Field(..., alias="from")
    to: str
    equivalent: str
    amount: str
    routes: Optional[List[PaymentRoute]] = None
    error: Optional[PaymentError] = None
    created_at: datetime
    committed_at: Optional[datetime] = None

class PaymentDetail(BaseModel):
    tx_id: str
    type: str
    state: str
    payload: Dict[str, Any]
    created_at: datetime
    error: Optional[Dict[str, Any]] = None

class PaymentsList(BaseModel):
    items: List[PaymentResult]
