from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ClearingCycleEdge(BaseModel):
    debt_id: str
    debtor: str
    creditor: str
    amount: str = Field(
        description="The debt's amount on the snapshot, at the equivalent's precision - not the amount the cycle "
        "would clear."
    )


class ClearingCyclesResponse(BaseModel):
    cycles: list[list[ClearingCycleEdge]]


# Programme 023, slice (d), decision R3 (spec 2026-09-28): the answer of `POST /clearing/auto` is the committed
# progress of one pass of the common runner. Every field is present; a nullable one is an explicit `null`. Money is
# an exact decimal string (never a float, an exponent or a display-precision rounding); atoms stay inside.


class ClearingAutoCommittedEdge(BaseModel):
    """One edge a committed occurrence reduced, in the runner's progress direction debtor -> creditor (UUIDs)."""

    debt_id: str
    debtor_id: str
    creditor_id: str


class ClearingAutoCommittedOccurrence(BaseModel):
    occurrence_id: str
    plan_id: str
    ordinal: int = Field(ge=0)
    amount: str
    edges: list[ClearingAutoCommittedEdge]
    after_cancellation: bool


class ClearingAutoError(BaseModel):
    code: str
    message: str
    details: Optional[dict[str, Any]]


class ClearingAutoResponse(BaseModel):
    equivalent: str
    cleared_cycles: int = Field(ge=0)
    status: Literal["complete", "interrupted"]
    reason: Optional[
        Literal["cancelled", "lease_lost", "budget_exhausted", "replan_limit", "operational_limit", "error"]
    ]
    v_edge: str
    v_cyc: str
    remaining_cycles: Optional[int] = Field(ge=0)
    remaining_v_edge: Optional[str]
    committed: list[ClearingAutoCommittedOccurrence]
    error: Optional[ClearingAutoError]
