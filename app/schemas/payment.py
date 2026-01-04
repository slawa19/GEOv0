from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime
from decimal import Decimal

class CapacityResponse(BaseModel):
    can_pay: bool
    max_amount: str
    routes_count: int
    estimated_hops: Optional[int] = None

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

class PaymentCreateRequest(BaseModel):
    to: str
    equivalent: str
    amount: str
    description: Optional[str] = None
    constraints: Optional[Dict[str, Any]] = None
    signature: str


class PaymentRoute(BaseModel):
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