from decimal import Decimal
from datetime import datetime
from typing import Annotated, Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field, PlainSerializer, field_serializer
from pydantic.config import ConfigDict

from app.utils.money import to_money_str

# 029 F-029-5, matrix row 9: an Admin API amount keeps its stored scale and is plain decimal text - pydantic's own
# `str(Decimal)` wrote a zero column or sum as `0E-8` (measured: `/admin/trustlines`, `/admin/liquidity/summary`).
PlainDecimal = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json")]

class TrustLineBase(BaseModel):
    policy: Optional[Dict[str, Any]] = None

class TrustLine(TrustLineBase):
    id: UUID
    from_pid: str = Field(..., serialization_alias="from")
    to_pid: str = Field(..., serialization_alias="to")
    from_display_name: Optional[str] = None
    to_display_name: Optional[str] = None
    equivalent_code: str = Field(..., serialization_alias="equivalent")
    limit: Decimal
    used: Decimal
    available: Decimal
    # 029 F-029-5: the equivalent's precision when the producer attaches it (the participant routes); never sent.
    equivalent_precision: Optional[int] = Field(default=None, exclude=True)
    status: str
    created_at: datetime
    updated_at: datetime
    # 026 `T2603.1`: when the creditor asked to close; NULL = no request. No default: every projection says it.
    close_requested_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_serializer("limit", "used", "available", when_used="json")
    def _money(self, value: Decimal) -> str:
        """One amount - one spelling (029 F-029-5): the equivalent's step, as `/balance` writes the same quantity.

        Without a precision (the Admin API, matrix row 9) the stored scale stays, as `PlainDecimal` writes it.
        """

        if self.equivalent_precision is None:
            return format(value, "f")
        return to_money_str(value, self.equivalent_precision)

class TrustLineCreateRequest(BaseModel):
    to: str
    equivalent: str
    # A string, verbatim, because the signature is taken over it verbatim (012 / T1201).
    # `api/openapi.yaml` has declared `limit: type: string` all along; typing it `Decimal`
    # here made pydantic re-spell the client's money before the service could sign-check it,
    # so for `"0.00000001"` the server rebuilt the payload with `str(Decimal('1E-8'))` and
    # the client's Ed25519 signature could never verify.  The `ge=0` bound moved with the
    # rest of the money rules into `parse_money_amount(..., require_non_negative=True)`,
    # which the service calls before the signature check.  Same contract as
    # `PaymentCreateRequest.amount`.
    limit: str
    policy: Optional[Dict[str, Any]] = None
    signature: str

class TrustLineExpectedState(BaseModel):
    """030 S5 (F-030-13, `T3000` item 1): the line as its owner saw it, bound by the signature of an UPDATE or CLOSE.

    All four fields are required (`close_requested_at` and `policy` may be null, never absent): a field left out
    cannot widen the match the service makes against the row it locked.
    """

    limit: str
    policy: Optional[Dict[str, Any]]
    status: str
    close_requested_at: Optional[str]


# 030 S5: `operation`, `expected` and `issued_at` are REQUIRED on the public routes (the canon says so and the service
# refuses a body without them - no compatibility with the pre-S5 signature over `{id}`). They are optional on the
# model only because the same model is the argument of the service's unsigned INTERNAL execution path (the
# simulator's trusted callers), which has no owner-seen state to declare and never reads them.
class TrustLineUpdateRequest(BaseModel):
    operation: Optional[str] = None
    # Same contract as `TrustLineCreateRequest.limit`.
    limit: Optional[str] = None
    policy: Optional[Dict[str, Any]] = None
    expected: Optional[TrustLineExpectedState] = None
    issued_at: Optional[str] = None
    signature: str


class TrustLineCloseRequest(BaseModel):
    operation: Optional[str] = None
    expected: Optional[TrustLineExpectedState] = None
    issued_at: Optional[str] = None
    signature: str


class TrustLineCloseResult(BaseModel):
    """`DELETE /trustlines/{id}` (026 `T2603.1`): the line's factual state - `closed`, or still live with a request."""

    status: str = "success"
    message: str
    trustline: TrustLine

class TrustLinesList(BaseModel):
    items: List[TrustLine]
