from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction


@dataclass
class AuditResult:
    ok: bool
    tick_index: int
    drifts: list[dict[str, Any]] = field(default_factory=list)
    total_drift: Decimal = Decimal("0")
    tick_volume: Decimal = Decimal("0")


def _net_positions_from_snapshot(
    *, snapshot: dict[tuple[str, str, str], Decimal], equivalent_code: str
) -> dict[str, Decimal]:
    eq_upper = str(equivalent_code or "").strip().upper()
    if not eq_upper:
        return {}

    incoming: dict[str, Decimal] = {}
    outgoing: dict[str, Decimal] = {}

    for (debtor_pid, creditor_pid, eq), amount in (snapshot or {}).items():
        if str(eq).strip().upper() != eq_upper:
            continue
        amt = Decimal(str(amount or 0))
        if amt == 0:
            continue
        outgoing[str(debtor_pid)] = outgoing.get(str(debtor_pid), Decimal("0")) + amt
        incoming[str(creditor_pid)] = incoming.get(str(creditor_pid), Decimal("0")) + amt

    all_pids = set(incoming.keys()) | set(outgoing.keys())
    out: dict[str, Decimal] = {}
    for pid in all_pids:
        out[pid] = incoming.get(pid, Decimal("0")) - outgoing.get(pid, Decimal("0"))
    return out


def _extract_planned_action_fields(action: Any) -> tuple[int, str, str, str, str] | None:
    """Returns (seq, equivalent, sender_pid, receiver_pid, amount_str)."""

    try:
        if isinstance(action, dict):
            seq = int(action.get("seq"))
            equivalent = str(action.get("equivalent") or "")
            sender_pid = str(action.get("sender_pid") or "")
            receiver_pid = str(action.get("receiver_pid") or "")
            amount = str(action.get("amount") or "")
        else:
            seq = int(getattr(action, "seq"))
            equivalent = str(getattr(action, "equivalent"))
            sender_pid = str(getattr(action, "sender_pid"))
            receiver_pid = str(getattr(action, "receiver_pid"))
            amount = str(getattr(action, "amount"))

        if not equivalent.strip() or not sender_pid.strip() or not receiver_pid.strip() or not amount.strip():
            return None

        return seq, equivalent, sender_pid, receiver_pid, amount
    except Exception:
        return None


async def audit_tick_balance(
    *,
    session: Any,
    equivalent_code: str,
    tick_index: int,
    payments_result: Any,
    clearing_volume_by_eq: dict[str, Any] | None = None,
    run_id: str | None = None,
    sim_idempotency_key: Callable[..., str] | None = None,
) -> AuditResult:
    """Best-effort post-tick audit for participant net balance deltas.

    Compares expected per-participant net position changes from committed PAYMENT
    transactions of the tick vs actual net position changes observed in the `debts`
    table.

    Notes
    -----
    - Clearing is expected to preserve per-participant net positions (cycle reduction),
      so it's not applied to expected deltas.
    - If `run_id` or `sim_idempotency_key` is missing, the audit is skipped (ok=True).
    """

    # Clearing does not change per-participant net positions (cycle reduction), so it's
    # not applied to expected deltas. But clearing volume is useful for severity
    # heuristics (drift as % of total tick activity).

    eq_upper = str(equivalent_code or "").strip().upper()
    if not eq_upper:
        return AuditResult(ok=True, tick_index=int(tick_index))

    if not run_id or sim_idempotency_key is None:
        return AuditResult(ok=True, tick_index=int(tick_index))

    planned = list(getattr(payments_result, "planned", None) or [])

    planned_tx_ids: list[str] = []
    for action in planned:
        fields = _extract_planned_action_fields(action)
        if fields is None:
            continue
        seq, eq, sender_pid, receiver_pid, amount_str = fields
        if str(eq).strip().upper() != eq_upper:
            continue

        tx_id = sim_idempotency_key(
            run_id=str(run_id),
            tick_ms=int(tick_index),
            sender_pid=str(sender_pid),
            receiver_pid=str(receiver_pid),
            equivalent=str(eq_upper),
            amount=str(amount_str),
            seq=int(seq),
        )
        planned_tx_ids.append(str(tx_id))

    if not planned_tx_ids:
        return AuditResult(ok=True, tick_index=int(tick_index))

    expected_delta: dict[str, Decimal] = {}
    tick_volume = Decimal("0")

    # SQLite has a limited max number of SQL variables; chunk to be safe.
    chunk_size = 200
    for i in range(0, len(planned_tx_ids), chunk_size):
        chunk = planned_tx_ids[i : i + chunk_size]
        rows = (
            await session.execute(
                select(Transaction)
                .where(
                    Transaction.type == "PAYMENT",
                    Transaction.state == "COMMITTED",
                    Transaction.tx_id.in_(chunk),
                )
                .execution_options(populate_existing=True)
            )
        ).scalars().all()

        for tx in rows:
            payload = tx.payload or {}
            if str(payload.get("equivalent") or "").strip().upper() != eq_upper:
                continue

            from_pid = str(payload.get("from") or "").strip()
            to_pid = str(payload.get("to") or "").strip()
            amt = Decimal(str(payload.get("amount") or "0"))

            if not from_pid or not to_pid or amt == 0:
                continue

            expected_delta[from_pid] = expected_delta.get(from_pid, Decimal("0")) - amt
            expected_delta[to_pid] = expected_delta.get(to_pid, Decimal("0")) + amt
            tick_volume += abs(amt)

    # Include clearing volume in tick activity volume (best-effort).
    try:
        if clearing_volume_by_eq is not None:
            raw = clearing_volume_by_eq.get(eq_upper)
            if raw is not None:
                tick_volume += abs(Decimal(str(raw)))
    except Exception:
        pass

    if not expected_delta:
        return AuditResult(ok=True, tick_index=int(tick_index))

    debt_snapshot_before: dict[tuple[str, str, str], Decimal] = getattr(
        payments_result, "debt_snapshot", None
    ) or {}
    net_before = _net_positions_from_snapshot(snapshot=debt_snapshot_before, equivalent_code=eq_upper)

    pids = set(net_before.keys()) | set(expected_delta.keys())
    if not pids:
        return AuditResult(ok=True, tick_index=int(tick_index))

    # Equivalent UUID.
    eq_id = (
        await session.execute(select(Equivalent.id).where(Equivalent.code == eq_upper))
    ).scalar_one_or_none()
    if eq_id is None:
        return AuditResult(ok=True, tick_index=int(tick_index))

    debtor = aliased(Participant)
    creditor = aliased(Participant)

    debt_rows = (
        await session.execute(
            select(
                debtor.pid.label("debtor_pid"),
                creditor.pid.label("creditor_pid"),
                func.sum(Debt.amount).label("total"),
            )
            .select_from(Debt)
            .join(debtor, debtor.id == Debt.debtor_id)
            .join(creditor, creditor.id == Debt.creditor_id)
            .where(
                Debt.equivalent_id == eq_id,
                or_(
                    debtor.pid.in_(pids),
                    creditor.pid.in_(pids),
                ),
            )
            .group_by(debtor.pid, creditor.pid)
        )
    ).all()

    incoming_after: dict[str, Decimal] = {}
    outgoing_after: dict[str, Decimal] = {}

    for row in debt_rows:
        debtor_pid = str(row.debtor_pid)
        creditor_pid = str(row.creditor_pid)
        amt = Decimal(str(row.total or 0))
        if amt == 0:
            continue
        outgoing_after[debtor_pid] = outgoing_after.get(debtor_pid, Decimal("0")) + amt
        incoming_after[creditor_pid] = incoming_after.get(creditor_pid, Decimal("0")) + amt

    net_after: dict[str, Decimal] = {}
    all_after = set(incoming_after.keys()) | set(outgoing_after.keys()) | set(pids)
    for pid in all_after:
        net_after[pid] = incoming_after.get(pid, Decimal("0")) - outgoing_after.get(
            pid, Decimal("0")
        )

    tolerance = Decimal("0.00000001")
    drifts: list[dict[str, Any]] = []

    for pid, expected in expected_delta.items():
        actual = net_after.get(pid, Decimal("0")) - net_before.get(pid, Decimal("0"))
        drift = actual - expected
        if abs(drift) > tolerance:
            drifts.append(
                {
                    "participant_id": str(pid),
                    "expected_delta": str(expected),
                    "actual_delta": str(actual),
                    "drift": str(drift),
                }
            )

    total_drift = Decimal("0")
    if drifts:
        total_drift = sum(abs(Decimal(str(d.get("drift") or "0"))) for d in drifts) / Decimal("2")

    return AuditResult(
        ok=not drifts,
        tick_index=int(tick_index),
        drifts=drifts,
        total_drift=total_drift,
        tick_volume=tick_volume,
    )
