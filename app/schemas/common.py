from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: Optional[str] = None
    uptime_seconds: int = Field(..., ge=0)
    timestamp: str

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
