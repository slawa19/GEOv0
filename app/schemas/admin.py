from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.types import StrictInt

from app.schemas.trustline import PlainDecimal, TrustLine as TrustLineSchema


class AdminConfigItem(BaseModel):
    key: str
    value: Any
    mutable: bool


class AdminConfigResponse(BaseModel):
    items: list[AdminConfigItem]


class AdminConfigPatchRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None


class AdminConfigPatchResponse(BaseModel):
    updated: list[str]


class AdminParticipantActionRequest(BaseModel):
    reason: str


class AdminParticipantStatusResponse(BaseModel):
    pid: str
    status: Literal["active", "suspended"]

    model_config = ConfigDict(extra="forbid")


class AdminAuditLogItem(BaseModel):
    id: UUID
    timestamp: datetime
    actor_id: Optional[UUID] = None
    actor_role: Optional[str] = None
    action: str
    object_type: Optional[str] = None
    object_id: Optional[str] = None
    reason: Optional[str] = None
    before_state: Optional[dict[str, Any]] = None
    after_state: Optional[dict[str, Any]] = None
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AdminPaginatedMeta(BaseModel):
    page: StrictInt = Field(..., ge=1)
    per_page: StrictInt = Field(..., ge=1, le=200)
    total: StrictInt = Field(..., ge=0)


class AdminParticipantListItem(BaseModel):
    pid: str
    display_name: str
    type: str
    status: str
    verification_level: int
    created_at: datetime


class AdminParticipantsListResponse(AdminPaginatedMeta):
    items: list[AdminParticipantListItem]


class AdminTrustLinesListResponse(AdminPaginatedMeta):
    items: list[TrustLineSchema]


class AdminAuditLogListResponse(AdminPaginatedMeta):
    items: list[AdminAuditLogItem]


class AdminParticipantsStatsResponse(BaseModel):
    participants_by_status: dict[str, StrictInt] = Field(default_factory=dict)
    participants_by_type: dict[str, StrictInt] = Field(default_factory=dict)
    total_participants: StrictInt = Field(0, ge=0)


class AdminLiquiditySummaryResponse(BaseModel):
    """One equivalent's active lines and their money - the Dashboard row (032 S5, F-2, F-3)."""

    equivalent: Optional[str] = None
    updated_at: datetime

    active_trustlines: StrictInt = Field(0, ge=0)

    # 028 F-028-37 (owner В-3): null without an equivalent - money is never summed across them.
    total_limit: Optional[PlainDecimal] = None
    total_used: Optional[PlainDecimal] = None
    total_available: Optional[PlainDecimal] = None


class AdminMigrationsStatus(BaseModel):
    current_revision: Optional[str] = None
    head_revision: Optional[str] = None
    is_up_to_date: bool


class AdminEquivalentCreateRequest(BaseModel):
    code: str = Field(..., pattern=r"^[A-Z0-9_]{1,16}$")
    symbol: Optional[str] = None
    description: Optional[str] = None
    # 0..8, not 0..18 (012 / S1, 2026-08-25): `debts.amount` and `trust_lines.limit` are
    # `Numeric(20, 8)`, and the protocol declares `precision` as 0-8
    # (`docs/ru/02-protocol-spec.md:155`). A wider declaration promised a resolution the
    # ledger silently rounds away. Must stay equal to `api/openapi.yaml` - the contract
    # tests compare the generated schema against the canon.
    precision: int = Field(default=2, ge=0, le=8)
    metadata: Optional[dict[str, Any]] = None
    is_active: bool = True
    reason: Optional[str] = None


class AdminEquivalentUpdateRequest(BaseModel):
    symbol: Optional[str] = None
    description: Optional[str] = None
    # See `AdminEquivalentCreateRequest.precision` above: 0..8, the storage scale.
    precision: Optional[int] = Field(default=None, ge=0, le=8)
    metadata: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    reason: Optional[str] = None


class AdminEquivalentDeleteRequest(BaseModel):
    reason: str


class AdminEquivalentIntegrityHoldClearRequest(BaseModel):
    """Programme 015 step 5c: why the operator lifts an integrity hold. Required, and written to audit."""

    reason: str = Field(min_length=1)


class AdminEquivalentUsageResponse(BaseModel):
    code: str
    trustlines: int
    debts: int
    integrity_checkpoints: int

    model_config = ConfigDict(extra="forbid")


class AdminDeleteResponse(BaseModel):
    deleted: str

    model_config = ConfigDict(extra="forbid")
