from datetime import datetime
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: Optional[str] = None
    uptime_seconds: int = Field(..., ge=0)
    timestamp: str

class AdminDbHealthDetail(BaseModel):
    dialect: str
    reachable: bool
    # Always emitted - null on the failure path, where no round trip completed - so it is
    # required-and-nullable rather than optional (app/api/v1/health.py:135, :149).
    latency_ms: Optional[int] = Field(..., ge=0)


class AdminDbHealthResponse(BaseModel):
    """What `/admin/health/db` really returns (`app/api/v1/health.py:132-151`).

    011/T1102. DOCUMENTATION ONLY, wired through `responses=`, never `response_model=`: the
    handler returns a plain dict on success and a raw `JSONResponse` on failure, and
    `response_model` would filter the former. Guarded by
    `tests/contract/test_p011_documentation_models_do_not_filter.py`.
    """

    status: Literal["ok", "error"]
    db: AdminDbHealthDetail
    details: Optional[str] = None
    # `_utc_now_iso()` - an ISO-8601 instant. Typed as datetime so the published schema says
    # `format: date-time`; safe because this model documents and never serializes.
    timestamp: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

class ErrorEnvelope(BaseModel):
    error: ErrorDetail

class SignedRequest(BaseModel):
    signature: str = Field(..., description="base64 signature")


class PaginationParams(BaseModel):
    page: int = Field(1, ge=1, description="1-based page number")
    per_page: int = Field(20, ge=1, le=200, description="Items per page")
