from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.equivalents import Equivalent as EquivalentSchema
from app.schemas.trustline import TrustLine as TrustLineSchema


class AdminGraphParticipant(BaseModel):
    pid: str
    display_name: str
    type: str
    status: str


class AdminGraphDebt(BaseModel):
    equivalent: str
    debtor: str
    creditor: str
    amount: Decimal


class AdminGraphSnapshotResponse(BaseModel):
    participants: list[AdminGraphParticipant]
    trustlines: list[TrustLineSchema]
    equivalents: list[EquivalentSchema]
    debts: list[AdminGraphDebt]

    # Present for UI compatibility (GraphPage reads these keys).
    incidents: list[Any] = Field(default_factory=list)
    audit_log: list[Any] = Field(default_factory=list)
    transactions: list[Any] = Field(default_factory=list)


class AdminClearingCycleEdge(BaseModel):
    equivalent: str
    debtor: str
    creditor: str
    amount: Decimal


class AdminClearingCyclesForEquivalent(BaseModel):
    cycles: list[list[AdminClearingCycleEdge]]


class AdminClearingCyclesResponse(BaseModel):
    equivalents: dict[str, AdminClearingCyclesForEquivalent]


class AdminGraphEgoResponse(AdminGraphSnapshotResponse):
    # Optionally include who the ego root is (not required by UI today)
    root_pid: Optional[str] = None

    model_config = ConfigDict(extra='ignore')
