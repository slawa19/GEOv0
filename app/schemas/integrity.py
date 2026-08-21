from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _attach_utc_to_naive_timestamp(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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

    @field_validator("last_verified")
    @classmethod
    def attach_utc_to_naive_database_timestamp(
        cls,
        value: Optional[datetime],
    ) -> Optional[datetime]:
        return _attach_utc_to_naive_timestamp(value) if value is not None else None


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

    @field_validator("created_at")
    @classmethod
    def attach_utc_to_naive_database_timestamp(cls, value: datetime) -> datetime:
        return _attach_utc_to_naive_timestamp(value)


class IntegrityVerifyRequest(BaseModel):
    equivalent: Optional[str] = None


class IntegrityVerifyResponse(BaseModel):
    status: str
    checked_at: datetime
    equivalents: Dict[str, EquivalentIntegrityStatus]
    alerts: List[str] = Field(default_factory=list)


class IntegrityNetMutualDebtsRepairResponse(BaseModel):
    ok: Literal[True]
    action: Literal["net-mutual-debts"]
    netted_pairs: int = Field(ge=0)
    updated: int = Field(ge=0)
    deleted: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class IntegrityCapDebtsRepairResponse(BaseModel):
    ok: Literal[True]
    action: Literal["cap-debts-to-trust-limits"]
    scanned: int = Field(ge=0)
    updated: int = Field(ge=0)
    deleted: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class IntegrityAuditLogItem(BaseModel):
    timestamp: datetime
    actor_id: Optional[str] = None
    action: str
    object_type: Optional[str] = None
    object_id: Optional[str] = None
    after_state: Optional[Dict[str, Any]] = None

    @field_validator("timestamp")
    @classmethod
    def attach_utc_to_naive_database_timestamp(cls, value: datetime) -> datetime:
        return _attach_utc_to_naive_timestamp(value)


class IntegrityAuditLogResponse(BaseModel):
    items: List[IntegrityAuditLogItem]
