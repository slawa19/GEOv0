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
    # Optional fields for future use or validation
    # signature: Optional[str] = None

class PaymentResult(BaseModel):
    tx_id: str
    status: str
    path: List[str]

class PaymentDetail(BaseModel):
    tx_id: str
    type: str
    state: str
    payload: Dict[str, Any]
    created_at: datetime
    error: Optional[Dict[str, Any]] = None

class PaymentsList(BaseModel):
    items: List[PaymentDetail]