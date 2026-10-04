from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class InvariantResult(BaseModel):
    """An invariant that was actually evaluated. `passed` stays a required bool."""

    passed: bool
    value: Optional[str] = None
    violations: Optional[int] = None
    details: Optional[Dict[str, Any]] = None


class InvariantWithdrawn(BaseModel):
    """An invariant that is NOT evaluated, said so that no reader can mistake it for a verdict.

    T1402 of programme 014. `check_zero_sum` (removed by 024 `T2411`) summed the same `Debt` rows
    grouped by creditor and grouped by debtor and returned the difference, so it telescoped to zero
    for any row set: it could not fail on data corruption, and publishing `passed: true` for it was a claim the code
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


class OverLimitAllowed(BaseModel):
    """A debt above the stored limit of its supporting live line: allowed, not a violation (026 В3)."""

    model_config = ConfigDict(extra="forbid")

    debtor_id: str
    creditor_id: str
    equivalent_id: str
    debt_amount: str
    trust_limit: str
    excess: str


class GrowthNotVerified(BaseModel):
    """A snapshot cannot see the state before an operation, so it does not verify growth (026 `T2601`)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_verified"] = "not_verified"
    reason: Literal["requires_operation_prestate"] = "requires_operation_prestate"


class TrustLimitsResult(BaseModel):
    """The `trust_limits` verdict (026 `T2601`): `passed` covers structural violations only.

    `over_limit_allowed` lists debts above a lowered limit - an allowed state - and `growth` says the
    snapshot did not verify growth; that is the write path's (`InvariantChecker.check_debt_growth`).
    """

    model_config = ConfigDict(extra="forbid")

    passed: bool
    violations: int
    details: Optional[Dict[str, Any]] = None
    over_limit_allowed: List[OverLimitAllowed] = Field(default_factory=list)
    growth: GrowthNotVerified = Field(default_factory=GrowthNotVerified)


# The union order matters: `InvariantWithdrawn` forbids extra keys, so a real result can never
# match it, while a withdrawn entry has no `passed` and can never match `InvariantResult`.
# `TrustLimitsResult` forbids extra keys and carries its own required-by-default keys, so an
# `InvariantResult` (which has `value`) never validates as one.
InvariantOutcome = Union[InvariantWithdrawn, TrustLimitsResult, InvariantResult]

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


class IntegrityStatusResponse(BaseModel):
    status: str  # healthy | warning | critical
    last_check: datetime
    equivalents: Dict[str, EquivalentIntegrityStatus]
    alerts: List[str] = Field(default_factory=list)


class EquivalentIntegritySummary(BaseModel):
    """028 `F-028-44` (owner В-7): one equivalent as a participant may see it - the last stored check, nothing more."""

    equivalent: str
    status: Literal["healthy", "warning", "critical"]
    checked_at: Optional[datetime]  # the last real check; null - none stored yet (then `warning`)
    hold: bool


class IntegritySummaryResponse(BaseModel):
    equivalents: List[EquivalentIntegritySummary]


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
