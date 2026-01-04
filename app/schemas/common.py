from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

class ErrorEnvelope(BaseModel):
    error: ErrorDetail

class SignedRequest(BaseModel):
    signature: str = Field(..., description="base64 signature")