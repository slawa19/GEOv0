from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _attach_utc_to_naive_timestamp(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class InvariantResult(BaseModel):
    """An invariant that was actually evaluated. `passed` stays a required bool."""

    passed: bool
    value: Optional[str] = None
    violations: Optional[int] = None
    details: Optional[Dict[str, Any]] = None


class InvariantWithdrawn(BaseModel):
    """An invariant that is NOT evaluated, said so that no reader can mistake it for a verdict.

    T1402 of programme 014. `check_zero_sum` sums the same `Debt` rows grouped by creditor and
    grouped by debtor and returns the difference, so it telescopes to zero for any row set: it
    cannot fail on data corruption, and publishing `passed: true` for it was a claim the code
    could not support. Measured on PostgreSQL 2026-09-11 - one debt inflated by one storage
    quantum, and a three-edge cycle inflated uniformly, both leave it PASSED.

    Deliberately NOT `InvariantResult` with `passed` made optional: widening `passed` would
    weaken the two checks that do work. This is a separate variant that CANNOT carry `passed`,
    a fabricated `value`, or a violation count, so "not verified" can never be read as a pass.

    Building the replacement is programme 015. This type states the gap; it does not fill it.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_verified"] = "not_verified"
    reason: Literal["check_withdrawn"] = "check_withdrawn"


# The union order matters: `InvariantWithdrawn` forbids extra keys, so a real result can never
# match it, while a withdrawn entry has no `passed` and can never match `InvariantResult`.
InvariantOutcome = Union[InvariantWithdrawn, InvariantResult]

ZERO_SUM_WITHDRAWN: Dict[str, Any] = {
    "status": "not_verified",
    "reason": "check_withdrawn",
}


class EquivalentIntegrityStatus(BaseModel):
    status: str  # healthy | warning | critical
    checksum: str = ""
    last_verified: Optional[datetime] = None
    invariants: Dict[str, InvariantOutcome] = Field(default_factory=dict)
    # The names in `invariants` that carry no verdict. Without this the aggregate `status` above
    # would read as "everything checked and healthy" while one check is not being run at all -
    # the false assurance would simply move from the entry to the summary.
    unverified: List[str] = Field(default_factory=list)

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
