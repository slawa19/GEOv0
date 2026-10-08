"""The participant metrics the admin graph drawer reads: balance rows per equivalent (032 S5, F-1).

Programme 032 S5 removed the rest of the participant analytics by the owner's decision of 2026-10-07 - rank and
net distribution, counterparty split and concentration (HHI), capacity and its bottlenecks, the 7/30/90 activity
windows (with `incident_count` and `has_transactions`): none of them led to an operator action.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.equivalents import canonical_code
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.metrics import AdminParticipantBalanceRow, AdminParticipantMetricsResponse
from app.utils.exceptions import NotFoundException


async def compute_participant_metrics(
    db: AsyncSession,
    *,
    pid: str,
    equivalent: str | None,
) -> AdminParticipantMetricsResponse:
    pid = str(pid or "").strip()
    if not pid:
        raise NotFoundException("Participant not found")

    participant = (
        await db.execute(select(Participant).where(Participant.pid == pid))
    ).scalar_one_or_none()
    if participant is None:
        raise NotFoundException("Participant not found")

    eq_code = canonical_code(equivalent) if equivalent is not None else None

    all_codes = [str(code) for code in (await db.execute(select(Equivalent.code))).scalars().all()]

    if eq_code is not None and eq_code not in all_codes:
        raise NotFoundException(f"Equivalent {eq_code} not found")

    balance_rows = await _compute_balance_rows(db, participant_id=participant.id, eq_code=eq_code, all_codes=all_codes)
    return AdminParticipantMetricsResponse(pid=pid, equivalent=eq_code, balance_rows=balance_rows)


async def _compute_balance_rows(
    db: AsyncSession,
    *,
    participant_id: Any,
    eq_code: str | None,
    all_codes: list[str],
) -> list[AdminParticipantBalanceRow]:
    # Outgoing: participant is creditor (from_participant_id)
    tl = TrustLine
    eq = Equivalent
    debt = aliased(Debt)

    outgoing_stmt = (
        select(
            eq.code,
            func.sum(tl.limit).label("out_limit"),
            func.sum(func.coalesce(debt.amount, 0)).label("out_used"),
        )
        .join(eq, eq.id == tl.equivalent_id)
        .outerjoin(
            debt,
            (debt.debtor_id == tl.to_participant_id)
            & (debt.creditor_id == tl.from_participant_id)
            & (debt.equivalent_id == tl.equivalent_id),
        )
        .where(
            tl.from_participant_id == participant_id,
            # LIVE rows only.  Since migration 019 a closed incarnation can coexist with
            # the live one; summing both would double-count the limit AND join the same
            # debt row twice, corrupting the money shown to the operator.
            tl.status != "closed",
        )
        .group_by(eq.code)
    )

    incoming_stmt = (
        select(
            eq.code,
            func.sum(tl.limit).label("in_limit"),
            func.sum(func.coalesce(debt.amount, 0)).label("in_used"),
        )
        .join(eq, eq.id == tl.equivalent_id)
        .outerjoin(
            debt,
            (debt.debtor_id == tl.to_participant_id)
            & (debt.creditor_id == tl.from_participant_id)
            & (debt.equivalent_id == tl.equivalent_id),
        )
        .where(
            tl.to_participant_id == participant_id,
            # LIVE rows only — same reason as the outgoing aggregate.
            tl.status != "closed",
        )
        .group_by(eq.code)
    )

    if eq_code is not None:
        outgoing_stmt = outgoing_stmt.where(eq.code == eq_code)
        incoming_stmt = incoming_stmt.where(eq.code == eq_code)

    out_rows = (await db.execute(outgoing_stmt)).all()
    in_rows = (await db.execute(incoming_stmt)).all()

    total_debt_stmt = (
        select(eq.code, func.sum(Debt.amount).label("total_debt"))
        .join(eq, eq.id == Debt.equivalent_id)
        .where(Debt.debtor_id == participant_id)
        .group_by(eq.code)
    )
    total_credit_stmt = (
        select(eq.code, func.sum(Debt.amount).label("total_credit"))
        .join(eq, eq.id == Debt.equivalent_id)
        .where(Debt.creditor_id == participant_id)
        .group_by(eq.code)
    )
    if eq_code is not None:
        total_debt_stmt = total_debt_stmt.where(eq.code == eq_code)
        total_credit_stmt = total_credit_stmt.where(eq.code == eq_code)

    debt_rows = (await db.execute(total_debt_stmt)).all()
    credit_rows = (await db.execute(total_credit_stmt)).all()

    out_by_eq: dict[str, tuple[Decimal, Decimal]] = {str(code): (lim or Decimal("0"), used or Decimal("0")) for code, lim, used in out_rows}
    in_by_eq: dict[str, tuple[Decimal, Decimal]] = {str(code): (lim or Decimal("0"), used or Decimal("0")) for code, lim, used in in_rows}
    debt_by_eq: dict[str, Decimal] = {str(code): (amt or Decimal("0")) for code, amt in debt_rows}
    credit_by_eq: dict[str, Decimal] = {str(code): (amt or Decimal("0")) for code, amt in credit_rows}

    codes: list[str]
    if eq_code is not None:
        codes = [eq_code]
    else:
        # Deterministic, stable set from equivalents table.
        codes = sorted(all_codes)

    out: list[AdminParticipantBalanceRow] = []
    for code in codes:
        out_limit, out_used = out_by_eq.get(code, (Decimal("0"), Decimal("0")))
        in_limit, in_used = in_by_eq.get(code, (Decimal("0"), Decimal("0")))
        total_debt = debt_by_eq.get(code, Decimal("0"))
        total_credit = credit_by_eq.get(code, Decimal("0"))
        net = total_credit - total_debt
        out.append(
            AdminParticipantBalanceRow(
                equivalent=code,
                outgoing_limit=out_limit,
                outgoing_used=out_used,
                incoming_limit=in_limit,
                incoming_used=in_used,
                total_debt=total_debt,
                total_credit=total_credit,
                net=net,
            )
        )

    return out
