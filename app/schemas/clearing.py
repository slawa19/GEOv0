from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class ClearingCycleEdge(BaseModel):
    debt_id: str
    debtor: str
    creditor: str
    amount: str


class ClearingCyclesResponse(BaseModel):
    cycles: list[list[ClearingCycleEdge]]


class ClearingAutoResponse(BaseModel):
    equivalent: str
    cleared_cycles: int
