from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.trustline import PlainDecimal


class AdminParticipantBalanceRow(BaseModel):
    equivalent: str
    outgoing_limit: PlainDecimal
    outgoing_used: PlainDecimal
    incoming_limit: PlainDecimal
    incoming_used: PlainDecimal
    total_debt: PlainDecimal
    total_credit: PlainDecimal
    net: PlainDecimal


class AdminParticipantMetricsResponse(BaseModel):
    """`GET /admin/participants/{pid}/metrics` - only the balance rows since 032 S5 (F-1, owner 2026-10-07)."""

    pid: str
    equivalent: Optional[str] = None

    balance_rows: list[AdminParticipantBalanceRow]
