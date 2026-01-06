from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class InvariantResult(BaseModel):
    passed: bool
    value: Optional[str] = None
    violations: Optional[int] = None
    details: Optional[Dict[str, Any]] = None


class EquivalentIntegrityStatus(BaseModel):
    status: str  # healthy | warning | critical
    checksum: str = ""
    last_verified: Optional[datetime] = None
    invariants: Dict[str, InvariantResult] = Field(default_factory=dict)


class IntegrityStatusResponse(BaseModel):
    status: str  # healthy | warning | critical
    last_check: datetime
    equivalents: Dict[str, EquivalentIntegrityStatus]
    alerts: List[str] = Field(default_factory=list)


class IntegrityChecksumResponse(BaseModel):
    equivalent: str
    checksum: str
    created_at: datetime
    invariants_status: Dict[str, Any]


class IntegrityVerifyRequest(BaseModel):
    equivalent: Optional[str] = None


class IntegrityVerifyResponse(BaseModel):
    status: str
    checked_at: datetime
    equivalents: Dict[str, EquivalentIntegrityStatus]
    alerts: List[str] = Field(default_factory=list)


class IntegrityAuditLogItem(BaseModel):
    timestamp: datetime
    actor_id: Optional[str] = None
    action: str
    object_type: Optional[str] = None
    object_id: Optional[str] = None
    after_state: Optional[Dict[str, Any]] = None


class IntegrityAuditLogResponse(BaseModel):
    items: List[IntegrityAuditLogItem]
