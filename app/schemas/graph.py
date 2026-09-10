from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.equivalents import StoredEquivalent
from app.schemas.trustline import TrustLine as TrustLineSchema


class AdminGraphParticipant(BaseModel):
    pid: str
    display_name: str
    type: str
    status: str

    # Optional net-visualization fields (populated only when an equivalent is provided).
    net_balance_atoms: Optional[str] = None
    net_sign: Optional[int] = None
    viz_color_key: Optional[str] = None
    viz_size: Optional[dict[str, int]] = None


class AdminGraphDebt(BaseModel):
    equivalent: str
    debtor: str
    creditor: str
    amount: Decimal


class AdminGraphSnapshotResponse(BaseModel):
    participants: list[AdminGraphParticipant]
    trustlines: list[TrustLineSchema]
    equivalents: list[StoredEquivalent]
    debts: list[AdminGraphDebt]

    # Present for UI compatibility (GraphPage reads these keys).
    incidents: list[Any] = Field(default_factory=list)
    audit_log: list[Any] = Field(default_factory=list)
    transactions: list[Any] = Field(default_factory=list)

    # F-013-1 / T1302, 2026-09-10. WHAT THE THREE LISTS ABOVE CANNOT SAY BY THEMSELVES.
    #
    # Each of them is an empty list in two unrelated situations - "you did not ask for it" and
    # "you asked, and there are none" - and until these two fields the wire could not tell them
    # apart. The consumer that counts committed payments read the length and reported zero for a
    # period it had never been told about (`admin-ui/src/composables/useGraphAnalytics.ts`).
    #
    # `included` lists the optional collections this response actually carries; `truncated` lists
    # the ones that hit their include limit, because a count over a cut list is a lower bound
    # presented as a total.
    #
    # WHY TWO LISTS AND NOT `transactions_included` / `transactions_truncated`, which is how
    # `T1302` names them. The `include` mechanism governs three collections, not one, and all
    # three carry the identical defect in the identical response. Six flat booleans say the same
    # thing worse, and naming only transactions would have closed one third of a defect while
    # standing next to the other two. Recorded in the spec rather than decided silently.
    included: list[str] = Field(default_factory=list)
    truncated: list[str] = Field(default_factory=list)


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
