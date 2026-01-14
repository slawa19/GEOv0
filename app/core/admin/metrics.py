from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.models.transaction import Transaction
from app.schemas.metrics import (
    AdminParticipantActivity,
    AdminParticipantBalanceRow,
    AdminParticipantCapacity,
    AdminParticipantCapacityBottleneck,
    AdminParticipantCapacitySide,
    AdminParticipantConcentration,
    AdminParticipantConcentrationSide,
    AdminParticipantCounterpartySplit,
    AdminParticipantCounterpartySplitRow,
    AdminParticipantMetricsResponse,
    AdminParticipantNetDistribution,
    AdminParticipantNetDistributionBin,
    AdminParticipantRank,
)
from app.schemas.trustline import TrustLine as TrustLineSchema
from app.utils.exceptions import NotFoundException
from app.utils.validation import validate_equivalent_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_eq(code: str) -> str:
    return str(code or "").strip().upper()


def _decimal_to_atoms(value: Decimal, precision: int) -> int:
    # Mirror UI: truncate toward 0 at `precision` decimal places.
    scale = Decimal(10) ** int(max(0, precision))
    neg = value < 0
    abs_v = -value if neg else value
    atoms_abs = int((abs_v * scale).to_integral_value(rounding=ROUND_DOWN))
    return -atoms_abs if neg else atoms_abs


def _atoms_to_decimal(atoms: int, precision: int) -> Decimal:
    scale = Decimal(10) ** int(max(0, precision))
    return (Decimal(atoms) / scale).quantize(Decimal(1) / scale)


def _hhi_from_shares(shares: list[float]) -> float:
    return float(sum((s or 0.0) * (s or 0.0) for s in shares))


@dataclass(frozen=True)
class _EquivalentInfo:
    id: Any
    code: str
    precision: int


async def compute_participant_metrics(
    db: AsyncSession,
    *,
    pid: str,
    equivalent: str | None,
    threshold: float | None,
) -> AdminParticipantMetricsResponse:
    pid = str(pid or "").strip()
    if not pid:
        raise NotFoundException("Participant not found")

    participant = (
        await db.execute(select(Participant).where(Participant.pid == pid))
    ).scalar_one_or_none()
    if participant is None:
        raise NotFoundException("Participant not found")

    eq_code = _norm_eq(equivalent) if equivalent is not None else None
    if eq_code is not None:
        validate_equivalent_code(eq_code)

    eq_rows = (
        await db.execute(select(Equivalent.id, Equivalent.code, Equivalent.precision).order_by(Equivalent.code.asc()))
    ).all()
    eq_by_code: dict[str, _EquivalentInfo] = {
        str(code): _EquivalentInfo(id=eq_id, code=str(code), precision=int(prec))
        for (eq_id, code, prec) in eq_rows
    }

    if eq_code is not None and eq_code not in eq_by_code:
        raise NotFoundException(f"Equivalent {eq_code} not found")

    balance_rows = await _compute_balance_rows(db, participant_id=participant.id, eq_code=eq_code, eq_by_code=eq_by_code)

    if eq_code is None:
        return AdminParticipantMetricsResponse(pid=pid, equivalent=None, balance_rows=balance_rows)

    eq_info = eq_by_code[eq_code]

    counterparty = await _compute_counterparty_split(
        db,
        participant_id=participant.id,
        participant_pid=pid,
        eq=eq_info,
    )

    concentration = _compute_concentration(counterparty)

    distribution, rank = await _compute_rank_and_distribution(
        db,
        participant_pid=pid,
        eq=eq_info,
    )

    capacity = await _compute_capacity(
        db,
        participant_id=participant.id,
        eq=eq_info,
        threshold=threshold,
    )

    activity = await _compute_activity(
        db,
        participant_id=participant.id,
        participant_pid=pid,
        eq_code=eq_code,
    )

    return AdminParticipantMetricsResponse(
        pid=pid,
        equivalent=eq_code,
        balance_rows=balance_rows,
        counterparty=counterparty,
        concentration=concentration,
        distribution=distribution,
        rank=rank,
        capacity=capacity,
        activity=activity,
        meta={
            "threshold": threshold,
        },
    )


async def _compute_balance_rows(
    db: AsyncSession,
    *,
    participant_id: Any,
    eq_code: str | None,
    eq_by_code: dict[str, _EquivalentInfo],
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
        .where(tl.from_participant_id == participant_id)
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
        .where(tl.to_participant_id == participant_id)
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
        codes = sorted(eq_by_code.keys())

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


async def _compute_counterparty_split(
    db: AsyncSession,
    *,
    participant_id: Any,
    participant_pid: str,
    eq: _EquivalentInfo,
) -> AdminParticipantCounterpartySplit:
    # creditors: who participant owes to (participant is debtor)
    other_p = aliased(Participant)

    creditors_stmt = (
        select(other_p.pid, other_p.display_name, func.sum(Debt.amount).label("amt"))
        .join(other_p, other_p.id == Debt.creditor_id)
        .where(Debt.equivalent_id == eq.id, Debt.debtor_id == participant_id)
        .group_by(other_p.pid, other_p.display_name)
    )

    debtors_stmt = (
        select(other_p.pid, other_p.display_name, func.sum(Debt.amount).label("amt"))
        .join(other_p, other_p.id == Debt.debtor_id)
        .where(Debt.equivalent_id == eq.id, Debt.creditor_id == participant_id)
        .group_by(other_p.pid, other_p.display_name)
    )

    creditors_rows = (await db.execute(creditors_stmt)).all()
    debtors_rows = (await db.execute(debtors_stmt)).all()

    total_debt = sum((amt or Decimal("0")) for _, __, amt in creditors_rows)
    total_credit = sum((amt or Decimal("0")) for _, __, amt in debtors_rows)

    def to_rows(rows: list[tuple[str, str, Decimal]], total: Decimal) -> list[AdminParticipantCounterpartySplitRow]:
        out_rows: list[AdminParticipantCounterpartySplitRow] = []
        for opid, name, amt in rows:
            a = amt or Decimal("0")
            share = float(a / total) if total and total > 0 else 0.0
            out_rows.append(
                AdminParticipantCounterpartySplitRow(
                    pid=str(opid),
                    display_name=str(name or opid or "").strip() or str(opid),
                    amount=a,
                    share=share,
                )
            )
        out_rows.sort(key=lambda r: (-r.amount, r.pid))
        return out_rows

    return AdminParticipantCounterpartySplit(
        eq=eq.code,
        total_debt=total_debt,
        total_credit=total_credit,
        creditors=to_rows(creditors_rows, total_debt),
        debtors=to_rows(debtors_rows, total_credit),
    )


def _compute_concentration(split: AdminParticipantCounterpartySplit) -> AdminParticipantConcentration:
    out_shares = [r.share for r in split.creditors]
    in_shares = [r.share for r in split.debtors]

    def side(shares: list[float]) -> AdminParticipantConcentrationSide:
        top1 = float(shares[0]) if shares else 0.0
        top5 = float(sum(shares[:5])) if shares else 0.0
        hhi = _hhi_from_shares(shares)
        return AdminParticipantConcentrationSide(top1=top1, top5=top5, hhi=hhi)

    return AdminParticipantConcentration(eq=split.eq, outgoing=side(out_shares), incoming=side(in_shares))


async def _compute_rank_and_distribution(
    db: AsyncSession,
    *,
    participant_pid: str,
    eq: _EquivalentInfo,
) -> tuple[AdminParticipantNetDistribution, AdminParticipantRank]:
    # Net = credits - debts.
    all_pids = [
        str(x)
        for (x,) in (
            await db.execute(select(Participant.pid).order_by(Participant.pid.asc()))
        ).all()
    ]

    # Aggregate debt and credit separately in DB, then convert to atoms in Python.
    debtor_stmt = (
        select(Participant.pid, func.sum(Debt.amount).label("amt"))
        .join(Participant, Participant.id == Debt.debtor_id)
        .where(Debt.equivalent_id == eq.id)
        .group_by(Participant.pid)
    )
    creditor_stmt = (
        select(Participant.pid, func.sum(Debt.amount).label("amt"))
        .join(Participant, Participant.id == Debt.creditor_id)
        .where(Debt.equivalent_id == eq.id)
        .group_by(Participant.pid)
    )

    debt_by_pid: dict[str, int] = {}
    for pid, amt in (await db.execute(debtor_stmt)).all():
        debt_by_pid[str(pid)] = _decimal_to_atoms(amt or Decimal("0"), eq.precision)

    credit_by_pid: dict[str, int] = {}
    for pid, amt in (await db.execute(creditor_stmt)).all():
        credit_by_pid[str(pid)] = _decimal_to_atoms(amt or Decimal("0"), eq.precision)

    net_by_pid: dict[str, int] = {}
    for pid in all_pids:
        net_by_pid[pid] = int(credit_by_pid.get(pid, 0) - debt_by_pid.get(pid, 0))

    sorted_pids = sorted(all_pids, key=lambda p: (-net_by_pid.get(p, 0), p))

    n = len(sorted_pids)

    vals = [net_by_pid[p] for p in sorted_pids]
    min_v = min(vals) if vals else 0
    max_v = max(vals) if vals else 0

    bins_count = 20
    span = max_v - min_v
    bins: list[AdminParticipantNetDistributionBin] = []
    if span <= 0:
        bins = [
            AdminParticipantNetDistributionBin(
                from_atoms=str(min_v),
                to_atoms=str(max_v),
                count=n,
            )
        ]
    else:
        w = (span + bins_count - 1) // bins_count
        # Build bins first for deterministic boundaries.
        buckets = [0 for _ in range(bins_count)]
        for v in vals:
            idx = (v - min_v) // w if w > 0 else 0
            if idx < 0:
                idx = 0
            if idx >= bins_count:
                idx = bins_count - 1
            buckets[int(idx)] += 1

        for i in range(bins_count):
            frm = min_v + i * w
            to = max_v if i == bins_count - 1 else (frm + w)
            bins.append(AdminParticipantNetDistributionBin(from_atoms=str(frm), to_atoms=str(to), count=buckets[i]))

    try:
        idx = sorted_pids.index(participant_pid)
    except ValueError:
        idx = -1

    if idx < 0:
        rank = 0
        percentile = 0.0
        net_atoms = 0
    else:
        rank = idx + 1
        percentile = 1.0 if n <= 1 else float((n - rank) / (n - 1))
        net_atoms = net_by_pid.get(participant_pid, 0)

    return (
        AdminParticipantNetDistribution(eq=eq.code, min_atoms=str(min_v), max_atoms=str(max_v), bins=bins),
        AdminParticipantRank(eq=eq.code, rank=rank, n=n, percentile=percentile, net=_atoms_to_decimal(net_atoms, eq.precision)),
    )


async def _compute_capacity(
    db: AsyncSession,
    *,
    participant_id: Any,
    eq: _EquivalentInfo,
    threshold: float | None,
) -> AdminParticipantCapacity:
    # Capacity around participant: trustlines where from/to = participant.
    tl = TrustLine
    debt = aliased(Debt)

    tl_stmt = (
        select(
            tl,
            func.coalesce(debt.amount, 0).label("used"),
        )
        .outerjoin(
            debt,
            (debt.debtor_id == tl.to_participant_id)
            & (debt.creditor_id == tl.from_participant_id)
            & (debt.equivalent_id == tl.equivalent_id),
        )
        .where(
            tl.equivalent_id == eq.id,
            (tl.from_participant_id == participant_id) | (tl.to_participant_id == participant_id),
        )
    )

    rows = (await db.execute(tl_stmt)).all()

    # Resolve pids in one go.
    pids_by_id = {
        pid_id: pid
        for pid_id, pid in (
            await db.execute(select(Participant.id, Participant.pid).where(Participant.id.in_({r[0].from_participant_id for r in rows} | {r[0].to_participant_id for r in rows})))
        ).all()
    }

    out_limit = Decimal("0")
    out_used = Decimal("0")
    in_limit = Decimal("0")
    in_used = Decimal("0")

    bottlenecks: list[AdminParticipantCapacityBottleneck] = []

    thr = float(threshold) if threshold is not None else None

    for trustline, used_amt in rows:
        used = used_amt or Decimal("0")
        available = (trustline.limit or Decimal("0")) - used

        from_pid = str(pids_by_id.get(trustline.from_participant_id, trustline.from_participant_id))
        to_pid = str(pids_by_id.get(trustline.to_participant_id, trustline.to_participant_id))

        t_schema = TrustLineSchema.model_validate(
            {
                "id": trustline.id,
                "from_pid": from_pid,
                "to_pid": to_pid,
                "from_display_name": None,
                "to_display_name": None,
                "equivalent_code": eq.code,
                "limit": trustline.limit or Decimal("0"),
                "used": used,
                "available": available,
                "status": trustline.status,
                "created_at": trustline.created_at,
                "updated_at": trustline.updated_at,
                "policy": trustline.policy,
            }
        )

        if trustline.from_participant_id == participant_id:
            out_limit += trustline.limit or Decimal("0")
            out_used += used
            if thr is not None and trustline.status == "active" and (trustline.limit or Decimal("0")) > 0:
                ratio = float(available / (trustline.limit or Decimal("0")))
                if ratio < thr:
                    bottlenecks.append(
                        AdminParticipantCapacityBottleneck(dir="out", other=to_pid, trustline=t_schema)
                    )
        if trustline.to_participant_id == participant_id:
            in_limit += trustline.limit or Decimal("0")
            in_used += used
            if thr is not None and trustline.status == "active" and (trustline.limit or Decimal("0")) > 0:
                ratio = float(available / (trustline.limit or Decimal("0")))
                if ratio < thr:
                    bottlenecks.append(
                        AdminParticipantCapacityBottleneck(dir="in", other=from_pid, trustline=t_schema)
                    )

    out_pct = float(out_used / out_limit) if out_limit > 0 else 0.0
    in_pct = float(in_used / in_limit) if in_limit > 0 else 0.0

    bottlenecks.sort(key=lambda b: (b.dir, b.other, str(b.trustline.id)))

    return AdminParticipantCapacity(
        eq=eq.code,
        out=AdminParticipantCapacitySide(limit=out_limit, used=out_used, pct=out_pct),
        inc=AdminParticipantCapacitySide(limit=in_limit, used=in_used, pct=in_pct),
        bottlenecks=bottlenecks,
    )


async def _compute_activity(
    db: AsyncSession,
    *,
    participant_id: Any,
    participant_pid: str,
    eq_code: str,
) -> AdminParticipantActivity:
    windows = [7, 30, 90]
    trustline_created = {w: 0 for w in windows}
    trustline_closed = {w: 0 for w in windows}
    incident_count = {w: 0 for w in windows}
    participant_ops = {w: 0 for w in windows}
    payment_committed = {w: 0 for w in windows}
    clearing_committed = {w: 0 for w in windows}

    # Pick a stable "now": the max timestamp among relevant rows (if any), else utc_now.
    now_candidates: list[datetime] = []

    tl_times = (
        await db.execute(
            select(func.max(TrustLine.created_at), func.max(TrustLine.updated_at)).where(
                (TrustLine.from_participant_id == participant_id) | (TrustLine.to_participant_id == participant_id)
            )
        )
    ).one()
    for t in tl_times:
        if isinstance(t, datetime):
            now_candidates.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))

    al_max = (
        await db.execute(
            select(func.max(AuditLog.timestamp)).where(AuditLog.object_id == participant_pid)
        )
    ).scalar_one_or_none()
    if isinstance(al_max, datetime):
        now_candidates.append(al_max if al_max.tzinfo else al_max.replace(tzinfo=timezone.utc))

    tx_max = (
        await db.execute(
            select(func.max(Transaction.updated_at), func.max(Transaction.created_at))
        )
    ).one()
    for t in tx_max:
        if isinstance(t, datetime):
            now_candidates.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))

    now = max(now_candidates) if now_candidates else _utc_now()

    def cutoff_days(w: int) -> datetime:
        return now - timedelta(days=int(w))

    # Trustline created/closed.
    tls = (
        await db.execute(
            select(TrustLine.created_at, TrustLine.updated_at, TrustLine.status)
            .where((TrustLine.from_participant_id == participant_id) | (TrustLine.to_participant_id == participant_id))
        )
    ).all()

    for created_at, updated_at, status in tls:
        ca = created_at if created_at and created_at.tzinfo else (created_at.replace(tzinfo=timezone.utc) if created_at else None)
        ua = updated_at if updated_at and updated_at.tzinfo else (updated_at.replace(tzinfo=timezone.utc) if updated_at else None)
        for w in windows:
            if ca is not None and ca >= cutoff_days(w):
                trustline_created[w] += 1
            if str(status or "").lower() == "closed" and ua is not None and ua >= cutoff_days(w):
                trustline_closed[w] += 1

    # Participant ops from audit log.
    audit = (
        await db.execute(
            select(AuditLog.timestamp, AuditLog.action)
            .where(AuditLog.object_id == participant_pid)
        )
    ).all()
    for ts, action in audit:
        if not isinstance(ts, datetime):
            continue
        ts2 = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        a = str(action or "")
        if not (a.startswith("PARTICIPANT_") or a.startswith("admin.participants.")):
            continue
        for w in windows:
            if ts2 >= cutoff_days(w):
                participant_ops[w] += 1

    # Incidents: "stuck" payment tx by initiator, created_at within window.
    sla_seconds = int(getattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120) or 120)
    cutoff = now - timedelta(seconds=sla_seconds)

    stuck = (
        await db.execute(
            select(Transaction.created_at)
            .where(
                Transaction.type == "PAYMENT",
                Transaction.initiator_id == participant_id,
                Transaction.state.in_({"NEW", "ROUTED", "PREPARE_IN_PROGRESS", "PREPARED", "PROPOSED", "WAITING"}),
                Transaction.updated_at < cutoff,
            )
        )
    ).all()
    for (created_at,) in stuck:
        if not isinstance(created_at, datetime):
            continue
        ca = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        for w in windows:
            if ca >= cutoff_days(w):
                incident_count[w] += 1

    # Committed tx activity.
    tx_rows = (
        await db.execute(
            select(Transaction.type, Transaction.payload, Transaction.state, Transaction.created_at, Transaction.updated_at, Transaction.initiator_id)
            .where(
                Transaction.type.in_({"PAYMENT", "CLEARING"}),
                Transaction.state == "COMMITTED",
                Transaction.updated_at >= cutoff_days(max(windows)),
            )
        )
    ).all()

    has_transactions = len(tx_rows) > 0

    for t_type, payload, state, created_at, updated_at, initiator_id in tx_rows:
        # payload is dict (JSON)
        pl = payload or {}
        payload_eq = pl.get("equivalent") if isinstance(pl, dict) else None
        if payload_eq is not None and _norm_eq(str(payload_eq)) != eq_code:
            continue

        involved = False
        if str(t_type) == "PAYMENT":
            if isinstance(pl, dict):
                from_pid = str(pl.get("from") or "")
                to_pid = str(pl.get("to") or "")
                involved = from_pid == participant_pid or to_pid == participant_pid
        else:
            # CLEARING: payload.edges[] has debtor/creditor PIDs.
            if initiator_id == participant_id:
                involved = True
            if not involved and isinstance(pl, dict):
                edges = pl.get("edges")
                if isinstance(edges, list):
                    for e in edges:
                        if not isinstance(e, dict):
                            continue
                        if str(e.get("debtor") or "") == participant_pid or str(e.get("creditor") or "") == participant_pid:
                            involved = True
                            break

        if not involved:
            continue

        anchor = updated_at or created_at
        if not isinstance(anchor, datetime):
            continue
        ts = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
        for w in windows:
            if ts >= cutoff_days(w):
                if str(t_type) == "PAYMENT":
                    payment_committed[w] += 1
                if str(t_type) == "CLEARING":
                    clearing_committed[w] += 1

    return AdminParticipantActivity(
        windows=windows,
        trustline_created=trustline_created,
        trustline_closed=trustline_closed,
        incident_count=incident_count,
        participant_ops=participant_ops,
        payment_committed=payment_committed,
        clearing_committed=clearing_committed,
        has_transactions=has_transactions,
    )
