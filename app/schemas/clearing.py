from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ClearingCycleEdge(BaseModel):
    debt_id: UUID
    debtor: UUID
    creditor: UUID
    amount: Decimal


class ClearingCyclesResponse(BaseModel):
    cycles: list[list[ClearingCycleEdge]]


class ClearingAutoResponse(BaseModel):
    equivalent: str
    cleared_cycles: int
