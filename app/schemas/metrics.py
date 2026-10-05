from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.trustline import PlainDecimal, TrustLine as TrustLineSchema


class AdminParticipantBalanceRow(BaseModel):
    equivalent: str
    outgoing_limit: PlainDecimal
    outgoing_used: PlainDecimal
    incoming_limit: PlainDecimal
    incoming_used: PlainDecimal
    total_debt: PlainDecimal
    total_credit: PlainDecimal
    net: PlainDecimal


class AdminParticipantCounterpartySplitRow(BaseModel):
    pid: str
    display_name: str
    amount: PlainDecimal
    share: float


class AdminParticipantCounterpartySplit(BaseModel):
    eq: str
    total_debt: PlainDecimal = Field(serialization_alias="totalDebt")
    total_credit: PlainDecimal = Field(serialization_alias="totalCredit")
    creditors: list[AdminParticipantCounterpartySplitRow]
    debtors: list[AdminParticipantCounterpartySplitRow]


class AdminParticipantConcentrationSide(BaseModel):
    top1: float
    top5: float
    hhi: float


class AdminParticipantConcentration(BaseModel):
    eq: str
    outgoing: AdminParticipantConcentrationSide
    incoming: AdminParticipantConcentrationSide


class AdminParticipantNetDistributionBin(BaseModel):
    from_atoms: str
    to_atoms: str
    count: int


class AdminParticipantNetDistribution(BaseModel):
    eq: str
    min_atoms: str
    max_atoms: str
    bins: list[AdminParticipantNetDistributionBin]


class AdminParticipantRank(BaseModel):
    eq: str
    rank: int
    n: int
    percentile: float
    net: PlainDecimal


class AdminParticipantCapacitySide(BaseModel):
    limit: PlainDecimal
    used: PlainDecimal
    pct: float


class AdminParticipantCapacityBottleneck(BaseModel):
    dir: Literal["out", "in"]
    other: str
    trustline: TrustLineSchema


class AdminParticipantCapacity(BaseModel):
    eq: str
    out: AdminParticipantCapacitySide
    inc: AdminParticipantCapacitySide
    bottlenecks: list[AdminParticipantCapacityBottleneck]


class AdminParticipantActivity(BaseModel):
    windows: list[int] = Field(default_factory=lambda: [7, 30, 90])
    trustline_created: dict[int, int]
    trustline_closed: dict[int, int]
    incident_count: dict[int, int]
    participant_ops: dict[int, int]
    payment_committed: dict[int, int]
    clearing_committed: dict[int, int]
    has_transactions: bool


class AdminParticipantMetricsResponse(BaseModel):
    pid: str
    equivalent: Optional[str] = None

    balance_rows: list[AdminParticipantBalanceRow]

    # Present only when equivalent is provided.
    counterparty: Optional[AdminParticipantCounterpartySplit] = None
    concentration: Optional[AdminParticipantConcentration] = None
    distribution: Optional[AdminParticipantNetDistribution] = None
    rank: Optional[AdminParticipantRank] = None
    capacity: Optional[AdminParticipantCapacity] = None
    activity: Optional[AdminParticipantActivity] = None

    # For forward compatibility; UI should ignore.
    meta: dict[str, Any] = Field(default_factory=dict)
