from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.types import StrictInt

from app.schemas.trustline import TrustLine as TrustLineSchema


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


class AdminFeatureFlags(BaseModel):
    multipath_enabled: bool
    full_multipath_enabled: bool
    clearing_enabled: bool


class AdminFeatureFlagsPatchRequest(BaseModel):
    # Partial PATCH: all fields optional; only provided ones are updated.
    multipath_enabled: Optional[bool] = None
    full_multipath_enabled: Optional[bool] = None
    clearing_enabled: Optional[bool] = None
    reason: Optional[str] = None


class AdminWhoAmIResponse(BaseModel):
    role: str = Field(..., pattern="^admin$")


class AdminParticipantActionRequest(BaseModel):
    reason: str


class AdminAbortTxRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class AdminAbortTxResponse(BaseModel):
    tx_id: str
    status: str = Field(..., pattern="^aborted$")


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


class AdminAuditLogResponse(BaseModel):
    items: list[AdminAuditLogItem]


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


class AdminIncidentItem(BaseModel):
    tx_id: str
    state: str
    initiator_pid: str
    equivalent: str
    age_seconds: StrictInt = Field(..., ge=0)
    sla_seconds: StrictInt = Field(..., ge=0)
    created_at: Optional[datetime] = None


class AdminIncidentsListResponse(AdminPaginatedMeta):
    items: list[AdminIncidentItem]


class AdminParticipantsStatsResponse(BaseModel):
    participants_by_status: dict[str, StrictInt] = Field(default_factory=dict)
    participants_by_type: dict[str, StrictInt] = Field(default_factory=dict)
    total_participants: StrictInt = Field(0, ge=0)


class AdminTrustLinesBottlenecksResponse(BaseModel):
    threshold: float = Field(..., ge=0.0)
    items: list[TrustLineSchema]


class AdminLiquidityNetRow(BaseModel):
    pid: str
    display_name: str
    net: Decimal


class AdminLiquiditySummaryResponse(BaseModel):
    equivalent: Optional[str] = None
    threshold: float = Field(..., ge=0.0)
    updated_at: datetime

    active_trustlines: StrictInt = Field(0, ge=0)
    bottlenecks: StrictInt = Field(0, ge=0)
    incidents_over_sla: StrictInt = Field(0, ge=0)

    total_limit: Decimal = Decimal("0")
    total_used: Decimal = Decimal("0")
    total_available: Decimal = Decimal("0")

    top_creditors: list[AdminLiquidityNetRow] = Field(default_factory=list)
    top_debtors: list[AdminLiquidityNetRow] = Field(default_factory=list)
    top_by_abs_net: list[AdminLiquidityNetRow] = Field(default_factory=list)
    top_bottleneck_edges: list[TrustLineSchema] = Field(default_factory=list)


class AdminMigrationsStatus(BaseModel):
    current_revision: Optional[str] = None
    head_revision: Optional[str] = None
    is_up_to_date: bool


class AdminEquivalentCreateRequest(BaseModel):
    code: str
    symbol: Optional[str] = None
    description: Optional[str] = None
    precision: int = 2
    metadata: Optional[dict[str, Any]] = None
    is_active: bool = True
    reason: Optional[str] = None


class AdminEquivalentUpdateRequest(BaseModel):
    symbol: Optional[str] = None
    description: Optional[str] = None
    precision: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    reason: Optional[str] = None


class AdminEquivalentDeleteRequest(BaseModel):
    reason: str


class AdminEquivalentUsageResponse(BaseModel):
    code: str
    trustlines: int
    debts: int
    integrity_checkpoints: int


class AdminDeleteResponse(BaseModel):
    deleted: str
