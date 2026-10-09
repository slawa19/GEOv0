"""The READ side of payments: a stored `transactions` row as the `PaymentResult` a participant is answered with.

Moved here from `app/core/payments/service.py` on 2026-10-09 (035 A8, F-035-5) with no change of behaviour: the three
bodies below are the bodies `PaymentService.get_payment_for_participant`, `PaymentService._tx_to_payment_result` and
`PaymentService.list_payments` had, with `self.session` spelled `session`. The methods stay on `PaymentService` and
delegate here, so no caller changed.

WHAT IS HERE AND WHAT IS NOT. Functions that only READ on the session they are given: they write nothing, lock
nothing, and end no transaction. `tx_to_payment_result` reads no database at all - it is also what the idempotent
replay of `pay()` renders a stored row with (`PaymentService._resolve_existing_payment`), which is why it is a plain
function of the row. The idempotency policy itself (`_resolve_existing_payment`) and the reads `pay()` makes while
settling a failed attempt (`_read_existing`, `_read_existing_row`) are part of the money path and stay in
`service.py`.

This module imports nothing from `service.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.transaction import Transaction
from app.schemas.payment import PaymentError, PaymentResult, PaymentRoute
from app.utils.error_codes import ErrorCode
from app.utils.exceptions import NotFoundException


def tx_to_payment_result(tx: Transaction) -> PaymentResult:
    payload = tx.payload or {}
    routes_payload = payload.get("routes")
    routes = None
    if routes_payload is not None:
        routes = [PaymentRoute.model_validate(r) for r in routes_payload] or None

    committed_at = tx.updated_at if tx.state == "COMMITTED" else None
    error = None
    if tx.error:
        error = PaymentError(
            code=str(tx.error.get("code") or ErrorCode.E010.value),
            message=str(tx.error.get("message", "")),
            details=tx.error.get("details"),
        )

    status = tx.state if tx.state in {"COMMITTED", "ABORTED"} else "ABORTED"
    return PaymentResult(
        tx_id=tx.tx_id,
        status=status,
        **{"from": str(payload.get("from", ""))},
        to=str(payload.get("to", "")),
        equivalent=str(payload.get("equivalent", "")),
        amount=str(payload.get("amount", "")),
        routes=routes,
        error=error,
        created_at=tx.created_at,
        committed_at=committed_at,
    )


async def get_payment_for_participant(
    session: AsyncSession,
    tx_id: str,
    *,
    requester_participant_id: uuid.UUID,
    requester_pid: str,
) -> PaymentResult:
    tx = (
        await session.execute(
            select(Transaction).where(Transaction.tx_id == tx_id)
        )
    ).scalar_one_or_none()
    if not tx or tx.type != "PAYMENT":
        raise NotFoundException(f"Payment {tx_id} not found")

    payload = tx.payload or {}
    # Access rule (MVP): allow initiator or receiver; otherwise return 404 to avoid leaking existence.
    if (
        tx.initiator_id != requester_participant_id
        and str(payload.get("to", "")) != requester_pid
    ):
        raise NotFoundException(f"Payment {tx_id} not found")

    return tx_to_payment_result(tx)


async def list_payments(
    session: AsyncSession,
    *,
    requester_participant_id: uuid.UUID,
    requester_pid: str,
    direction: Literal["sent", "received", "all"] = "all",
    equivalent: str | None = None,
    status: Literal["COMMITTED", "ABORTED", "all"] = "all",
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = 1,
    per_page: int = 20,
) -> List[PaymentResult]:
    def _normalize_dt(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        # For client/server DBs (e.g. Postgres), prefer aware UTC.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    from_date = _normalize_dt(from_date)
    to_date = _normalize_dt(to_date)

    offset = (page - 1) * per_page

    clauses = [Transaction.type == "PAYMENT"]
    if status != "all":
        clauses.append(Transaction.state == status)
    if from_date is not None:
        clauses.append(Transaction.created_at >= from_date)
    if to_date is not None:
        clauses.append(Transaction.created_at <= to_date)

    # Direction filtering.
    payload = Transaction.payload
    to_expr = payload["to"].as_string()
    from_expr = payload["from"].as_string()
    eq_expr = payload["equivalent"].as_string()

    if direction == "sent":
        clauses.append(
            or_(
                Transaction.initiator_id == requester_participant_id,
                from_expr == requester_pid,
            )
        )
    elif direction == "received":
        clauses.append(to_expr == requester_pid)
    else:
        clauses.append(
            or_(
                Transaction.initiator_id == requester_participant_id,
                to_expr == requester_pid,
                from_expr == requester_pid,
            )
        )

    if equivalent:
        clauses.append(eq_expr == equivalent)

    stmt = (
        select(Transaction)
        .where(and_(*clauses))
        .order_by(Transaction.created_at.desc())
        .limit(per_page)
        .offset(offset)
    )

    txs = (await session.execute(stmt)).scalars().all()
    return [tx_to_payment_result(tx) for tx in txs]
