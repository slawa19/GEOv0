from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import String, cast, desc, func, select, and_, case, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api import deps
from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent as EquivalentModel
from app.db.models.debt import Debt
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.models.transaction import Transaction
from app.schemas.admin import (
    AdminAuditLogItem,
    AdminAuditLogResponse,
    AdminAuditLogListResponse,
    AdminAbortTxRequest,
    AdminAbortTxResponse,
    AdminConfigPatchRequest,
    AdminConfigPatchResponse,
    AdminConfigResponse,
    AdminEquivalentCreateRequest,
    AdminEquivalentDeleteRequest,
    AdminEquivalentUpdateRequest,
    AdminEquivalentUsageResponse,
    AdminDeleteResponse,
    AdminFeatureFlags,
    AdminFeatureFlagsPatchRequest,
    AdminMigrationsStatus,
    AdminParticipantActionRequest,
    AdminParticipantsListResponse,
    AdminIncidentsListResponse,
    AdminLiquiditySummaryResponse,
    AdminLiquidityNetRow,
    AdminParticipantsStatsResponse,
    AdminTrustLinesBottlenecksResponse,
    AdminTrustLinesListResponse,
    AdminWhoAmIResponse,
)
from app.schemas.equivalents import Equivalent as EquivalentSchema
from app.schemas.equivalents import EquivalentsList
from app.schemas.graph import (
    AdminClearingCycleEdge,
    AdminClearingCyclesForEquivalent,
    AdminClearingCyclesResponse,
    AdminGraphDebt,
    AdminGraphEgoResponse,
    AdminGraphParticipant,
    AdminGraphSnapshotResponse,
)
from app.schemas.trustline import TrustLine as TrustLineSchema
from app.core.clearing.service import ClearingService
from app.core.admin.metrics import compute_participant_metrics
from app.core.trustlines.service import TrustLineService
from app.core.payments.engine import PaymentEngine
from app.utils.exceptions import BadRequestException, ConflictException, NotFoundException
from app.utils.validation import validate_equivalent_code

from app.schemas.metrics import AdminParticipantMetricsResponse


router = APIRouter(prefix="/admin", dependencies=[Depends(deps.require_admin)])


_ACTIVE_PAYMENT_TX_STATES: set[str] = {
    "NEW",
    "ROUTED",
    "PREPARE_IN_PROGRESS",
    "PREPARED",
    "PROPOSED",
    "WAITING",
}


def _participant_status_db_values_for_filter(status: str | None) -> list[str] | None:
    """Map UI status vocabulary to DB values (and accept legacy aliases).

    UI vocabulary: active/frozen/banned
    DB vocabulary: active/suspended/left/deleted
    """

    if status is None:
        return None
    v = str(status).strip().lower()
    if not v:
        return None

    # UI → DB
    if v == "frozen":
        return ["suspended"]
    if v == "banned":
        # Treat "left" as banned for UI compatibility.
        return ["deleted", "left"]

    # DB aliases (backward compatibility)
    if v in {"active", "suspended", "left", "deleted"}:
        return [v]

    return [v]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_include_csv(value: str | None) -> set[str]:
    if value is None:
        return set()
    raw = str(value).strip()
    if not raw:
        return set()
    out: set[str] = set()
    for part in raw.split(","):
        p = part.strip().lower()
        if p:
            out.add(p)
    return out


async def _graph_fetch_incidents(db: AsyncSession, *, limit: int) -> list[dict[str, Any]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []

    sla_seconds = int(getattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120) or 120)
    cutoff = _utc_now() - timedelta(seconds=sla_seconds)

    stmt = (
        select(Transaction, Participant.pid)
        .join(Participant, Transaction.initiator_id == Participant.id)
        .where(
            Transaction.type == "PAYMENT",
            Transaction.state.in_(_ACTIVE_PAYMENT_TX_STATES),
            Transaction.updated_at < cutoff,
        )
        .order_by(Transaction.updated_at.asc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    now = _utc_now()
    items: list[dict[str, Any]] = []
    for tx, initiator_pid in rows:
        payload = tx.payload or {}
        equivalent = str(payload.get("equivalent") or "")
        anchor = (tx.updated_at or tx.created_at) or now
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((now - anchor).total_seconds()))
        items.append(
            {
                "tx_id": tx.tx_id,
                "state": tx.state,
                "initiator_pid": str(initiator_pid),
                "equivalent": equivalent,
                "age_seconds": age_seconds,
                "sla_seconds": sla_seconds,
                "created_at": tx.created_at,
            }
        )
    return items


async def _graph_fetch_audit_log(db: AsyncSession, *, limit: int) -> list[dict[str, Any]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    stmt = select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit)
    items = (await db.execute(stmt)).scalars().all()
    return [AdminAuditLogItem.model_validate(x).model_dump() for x in items]


async def _graph_fetch_transactions(db: AsyncSession, *, limit: int) -> list[dict[str, Any]]:
    limit = max(0, int(limit))
    if limit <= 0:
        return []
    stmt = (
        select(Transaction, Participant.pid)
        .join(Participant, Transaction.initiator_id == Participant.id)
        .order_by(desc(Transaction.updated_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: list[dict[str, Any]] = []
    for tx, initiator_pid in rows:
        payload = tx.payload or {}
        out.append(
            {
                "tx_id": tx.tx_id,
                "type": tx.type,
                "state": tx.state,
                "initiator_pid": str(initiator_pid),
                "created_at": tx.created_at,
                "updated_at": tx.updated_at,
                "equivalent": payload.get("equivalent"),
                "error": tx.error,
            }
        )
    return out


def _runtime_config_items() -> list[tuple[str, bool]]:
    # (key, mutable)
    return [
        ("LOG_LEVEL", True),
        ("RATE_LIMIT_ENABLED", True),
        ("ROUTING_MAX_HOPS", True),
        ("ROUTING_MAX_PATHS", True),
        ("INTEGRITY_CHECKPOINT_ENABLED", True),
        ("INTEGRITY_CHECKPOINT_INTERVAL_SECONDS", True),
        ("RECOVERY_ENABLED", True),
        ("RECOVERY_INTERVAL_SECONDS", True),
        ("PAYMENT_TX_STUCK_TIMEOUT_SECONDS", True),
        ("FEATURE_FLAGS_MULTIPATH_ENABLED", True),
        ("FEATURE_FLAGS_FULL_MULTIPATH_ENABLED", True),
        ("CLEARING_ENABLED", True),
    ]


async def _audit(
    db: AsyncSession,
    *,
    request: Request,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    reason: str | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    try:
        rid = request.headers.get("X-Request-ID")
        ip = (request.client.host if request.client else None) or None
        ua = request.headers.get("user-agent")
        db.add(
            AuditLog(
                actor_id=None,
                actor_role="admin",
                action=action,
                object_type=object_type,
                object_id=object_id,
                reason=reason,
                before_state=before_state,
                after_state=after_state,
                request_id=rid,
                ip_address=ip,
                user_agent=ua,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()


@router.get("/config", response_model=AdminConfigResponse, dependencies=[])
async def get_admin_config() -> AdminConfigResponse:
    items = []
    for key, mutable in _runtime_config_items():
        items.append({"key": key, "value": getattr(settings, key), "mutable": mutable})
    return AdminConfigResponse(items=items)


@router.patch("/config", response_model=AdminConfigPatchResponse)
async def patch_admin_config(
    body: AdminConfigPatchRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> AdminConfigPatchResponse:
    allowed = {k for k, mutable in _runtime_config_items() if mutable}
    updated: list[str] = []
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for key, value in (body.updates or {}).items():
        if key not in allowed:
            raise BadRequestException(f"Config key not mutable: {key}")
        before[key] = getattr(settings, key)
        setattr(settings, key, value)
        after[key] = getattr(settings, key)
        updated.append(key)

    await _audit(
        db,
        request=request,
        action="admin.config.patch",
        object_type="config",
        object_id=None,
        reason=body.reason,
        before_state=before or None,
        after_state=after or None,
    )
    return AdminConfigPatchResponse(updated=updated)


@router.get("/whoami", response_model=AdminWhoAmIResponse, dependencies=[])
async def admin_whoami() -> AdminWhoAmIResponse:
    # For now, require_admin implies role=admin.
    return AdminWhoAmIResponse(role="admin")


@router.get("/feature-flags", response_model=AdminFeatureFlags, dependencies=[])
async def get_feature_flags() -> AdminFeatureFlags:
    return AdminFeatureFlags(
        multipath_enabled=settings.FEATURE_FLAGS_MULTIPATH_ENABLED,
        full_multipath_enabled=settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED,
        clearing_enabled=settings.CLEARING_ENABLED,
    )


@router.patch("/feature-flags", response_model=AdminFeatureFlags)
async def patch_feature_flags(
    body: AdminFeatureFlagsPatchRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> AdminFeatureFlags:
    before = {
        "multipath_enabled": settings.FEATURE_FLAGS_MULTIPATH_ENABLED,
        "full_multipath_enabled": settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED,
        "clearing_enabled": settings.CLEARING_ENABLED,
    }

    if body.multipath_enabled is not None:
        settings.FEATURE_FLAGS_MULTIPATH_ENABLED = body.multipath_enabled
    if body.full_multipath_enabled is not None:
        settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED = body.full_multipath_enabled
    if body.clearing_enabled is not None:
        settings.CLEARING_ENABLED = body.clearing_enabled

    after = {
        "multipath_enabled": settings.FEATURE_FLAGS_MULTIPATH_ENABLED,
        "full_multipath_enabled": settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED,
        "clearing_enabled": settings.CLEARING_ENABLED,
    }

    await _audit(
        db,
        request=request,
        action="admin.feature_flags.patch",
        object_type="feature_flags",
        object_id=None,
        reason=body.reason,
        before_state=before,
        after_state=after,
    )

    return AdminFeatureFlags(**after)


@router.get("/participants")
async def list_admin_participants(
    q: str | None = None,
    status: str | None = None,
    type: Literal["person", "business", "hub"] | None = Query(None, description="Participant type"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminParticipantsListResponse:
    base = select(Participant)

    if q:
        needle = f"%{q}%"
        base = base.where(
            (Participant.pid.ilike(needle))
            | (Participant.display_name.ilike(needle))
        )
    status_db_values = _participant_status_db_values_for_filter(status)
    if status_db_values:
        if len(status_db_values) == 1:
            base = base.where(Participant.status == status_db_values[0])
        else:
            base = base.where(Participant.status.in_(status_db_values))
    if type:
        base = base.where(Participant.type == type)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    stmt = base.order_by(Participant.id.asc()).limit(per_page).offset((page - 1) * per_page)
    items = (await db.execute(stmt)).scalars().all()

    return AdminParticipantsListResponse(
        items=[
            {
                "pid": p.pid,
                "display_name": p.display_name,
                "type": p.type,
                "status": p.status,
                "verification_level": p.verification_level,
                "created_at": p.created_at,
            }
            for p in items
        ],
        page=page,
        per_page=per_page,
        total=int(total),
    )


@router.get("/participants/stats", response_model=AdminParticipantsStatsResponse)
async def admin_participants_stats(
    db: AsyncSession = Depends(deps.get_db),
) -> AdminParticipantsStatsResponse:
    status_rows = (
        await db.execute(
            select(Participant.status, func.count())
            .group_by(Participant.status)
        )
    ).all()
    type_rows = (
        await db.execute(
            select(Participant.type, func.count())
            .group_by(Participant.type)
        )
    ).all()

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}

    for status, n in status_rows:
        k = str(status or "").strip().lower() or "unknown"
        by_status[k] = int(n or 0)
    for type_, n in type_rows:
        k = str(type_ or "").strip().lower() or "unknown"
        by_type[k] = int(n or 0)

    total = sum(by_status.values())
    return AdminParticipantsStatsResponse(
        participants_by_status=by_status,
        participants_by_type=by_type,
        total_participants=int(total),
    )


@router.get("/trustlines/bottlenecks", response_model=AdminTrustLinesBottlenecksResponse)
async def admin_trustlines_bottlenecks(
    threshold: float = Query(0.10, ge=0.0, le=1.0),
    limit: int = Query(10, ge=1, le=50),
    equivalent: str | None = Query(None, description="Equivalent code (optional)"),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminTrustLinesBottlenecksResponse:
    eq_code = str(equivalent or "").strip().upper() or None

    p_from = aliased(Participant)
    p_to = aliased(Participant)
    used_expr = func.coalesce(Debt.amount, 0)
    available_expr = TrustLine.limit - used_expr

    stmt = (
        select(
            TrustLine.id,
            TrustLine.limit,
            TrustLine.status,
            TrustLine.created_at,
            TrustLine.updated_at,
            TrustLine.policy,
            EquivalentModel.code.label("equivalent"),
            p_from.pid.label("from_pid"),
            p_from.display_name.label("from_display_name"),
            p_to.pid.label("to_pid"),
            p_to.display_name.label("to_display_name"),
            used_expr.label("used"),
            available_expr.label("available"),
        )
        .select_from(TrustLine)
        .join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id)
        .join(p_from, TrustLine.from_participant_id == p_from.id)
        .join(p_to, TrustLine.to_participant_id == p_to.id)
        .outerjoin(
            Debt,
            and_(
                Debt.debtor_id == TrustLine.to_participant_id,
                Debt.creditor_id == TrustLine.from_participant_id,
                Debt.equivalent_id == TrustLine.equivalent_id,
            ),
        )
        .where(
            TrustLine.status == "active",
            TrustLine.limit > 0,
            (available_expr / TrustLine.limit) < float(threshold),
        )
        .order_by(available_expr.asc(), TrustLine.created_at.asc())
        .limit(limit)
    )

    if eq_code:
        stmt = stmt.where(EquivalentModel.code == eq_code)

    rows = (await db.execute(stmt)).all()
    items: list[TrustLineSchema] = []
    for (
        tl_id,
        limit_value,
        status,
        created_at,
        updated_at,
        policy,
        equivalent_code,
        from_pid,
        from_display_name,
        to_pid,
        to_display_name,
        used,
        available,
    ) in rows:
        items.append(
            TrustLineSchema.model_validate(
                {
                    "id": tl_id,
                    "from_pid": from_pid,
                    "to_pid": to_pid,
                    "from_display_name": from_display_name,
                    "to_display_name": to_display_name,
                    "equivalent_code": equivalent_code,
                    "limit": limit_value,
                    "used": used,
                    "available": available,
                    "status": status,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "policy": policy,
                }
            )
        )

    return AdminTrustLinesBottlenecksResponse(threshold=float(threshold), items=items)


@router.get("/liquidity/summary", response_model=AdminLiquiditySummaryResponse)
async def admin_liquidity_summary(
    equivalent: str | None = Query(None, description="Equivalent code (optional; omit for ALL)"),
    threshold: float = Query(0.10, ge=0.0, le=1.0),
    limit: int = Query(10, ge=1, le=50, description="Top-N size for ranked lists"),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminLiquiditySummaryResponse:
    eq_code = str(equivalent or "").strip().upper() or None
    now = _utc_now()

    used_expr = func.coalesce(Debt.amount, 0)
    available_expr = TrustLine.limit - used_expr

    totals_stmt = (
        select(
            func.count().label("active_trustlines"),
            func.coalesce(func.sum(TrustLine.limit), 0).label("total_limit"),
            func.coalesce(func.sum(used_expr), 0).label("total_used"),
            func.coalesce(func.sum(available_expr), 0).label("total_available"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(TrustLine.limit > 0, (available_expr / TrustLine.limit) < float(threshold)),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("bottlenecks"),
        )
        .select_from(TrustLine)
        .join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id)
        .outerjoin(
            Debt,
            and_(
                Debt.debtor_id == TrustLine.to_participant_id,
                Debt.creditor_id == TrustLine.from_participant_id,
                Debt.equivalent_id == TrustLine.equivalent_id,
            ),
        )
        .where(TrustLine.status == "active")
    )
    if eq_code:
        totals_stmt = totals_stmt.where(EquivalentModel.code == eq_code)

    totals = (await db.execute(totals_stmt)).one()
    active_trustlines = int(totals.active_trustlines or 0)
    total_limit = totals.total_limit
    total_used = totals.total_used
    total_available = totals.total_available
    bottlenecks = int(totals.bottlenecks or 0)

    # Incidents over SLA ("stuck" payments). Filter by equivalent in Python to keep it portable.
    sla_seconds = int(getattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120) or 120)
    cutoff = now - timedelta(seconds=sla_seconds)
    inc_payloads = (
        await db.execute(
            select(Transaction.payload)
            .where(
                Transaction.type == "PAYMENT",
                Transaction.state.in_(_ACTIVE_PAYMENT_TX_STATES),
                Transaction.updated_at < cutoff,
            )
        )
    ).scalars().all()
    if eq_code:
        incidents_over_sla = sum(
            1
            for p in inc_payloads
            if str((p or {}).get("equivalent") or "").strip().upper() == eq_code
        )
    else:
        incidents_over_sla = len(inc_payloads)

    # Net positions (Debt direction: debtor -> creditor).
    debt_base = (
        select(Debt)
        .join(EquivalentModel, Debt.equivalent_id == EquivalentModel.id)
        .where(Debt.amount > 0)
    )
    if eq_code:
        debt_base = debt_base.where(EquivalentModel.code == eq_code)
    debt_subq = debt_base.subquery()

    pos = select(debt_subq.c.creditor_id.label("participant_id"), debt_subq.c.amount.label("delta"))
    neg = select(debt_subq.c.debtor_id.label("participant_id"), (-debt_subq.c.amount).label("delta"))
    delta = union_all(pos, neg).subquery()

    net_stmt = (
        select(
            Participant.pid.label("pid"),
            Participant.display_name.label("display_name"),
            func.coalesce(func.sum(delta.c.delta), 0).label("net"),
        )
        .select_from(delta)
        .join(Participant, Participant.id == delta.c.participant_id)
        .group_by(Participant.pid, Participant.display_name)
    )

    net = net_stmt.subquery()
    net_base = select(net.c.pid, net.c.display_name, net.c.net)

    top_creditors_rows = (
        await db.execute(net_base.where(net.c.net > 0).order_by(net.c.net.desc()).limit(limit))
    ).all()
    top_debtors_rows = (
        await db.execute(net_base.where(net.c.net < 0).order_by(net.c.net.asc()).limit(limit))
    ).all()
    top_abs_rows = (
        await db.execute(net_base.order_by(func.abs(net.c.net).desc()).limit(limit))
    ).all()

    top_creditors = [AdminLiquidityNetRow(pid=pid, display_name=dn, net=netv) for pid, dn, netv in top_creditors_rows]
    top_debtors = [AdminLiquidityNetRow(pid=pid, display_name=dn, net=netv) for pid, dn, netv in top_debtors_rows]
    top_by_abs_net = [AdminLiquidityNetRow(pid=pid, display_name=dn, net=netv) for pid, dn, netv in top_abs_rows]

    # Top bottleneck edges with computed used/available.
    bottlenecks_env = await admin_trustlines_bottlenecks(
        threshold=threshold,
        limit=limit,
        equivalent=eq_code,
        db=db,
    )

    return AdminLiquiditySummaryResponse(
        equivalent=eq_code,
        threshold=float(threshold),
        updated_at=now,
        active_trustlines=active_trustlines,
        bottlenecks=bottlenecks,
        incidents_over_sla=int(incidents_over_sla),
        total_limit=total_limit,
        total_used=total_used,
        total_available=total_available,
        top_creditors=top_creditors,
        top_debtors=top_debtors,
        top_by_abs_net=top_by_abs_net,
        top_bottleneck_edges=bottlenecks_env.items,
    )


async def _set_participant_status(
    *,
    pid: str,
    audit_action: str,
    status_value: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession,
) -> dict:
    participant = (
        await db.execute(select(Participant).where(Participant.pid == pid))
    ).scalar_one_or_none()
    if participant is None:
        raise NotFoundException(f"Participant {pid} not found")

    before = {"status": participant.status}
    participant.status = status_value
    await db.commit()
    await db.refresh(participant)

    await _audit(
        db,
        request=request,
        action=audit_action,
        object_type="participant",
        object_id=pid,
        reason=body.reason,
        before_state=before,
        after_state={"status": participant.status},
    )

    return {"pid": participant.pid, "status": participant.status}


@router.post("/participants/{pid}/freeze")
async def freeze_participant(
    pid: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    return await _set_participant_status(
        pid=pid,
        audit_action="admin.participants.freeze",
        status_value="suspended",
        body=body,
        request=request,
        db=db,
    )


@router.post("/participants/{pid}/unfreeze")
async def unfreeze_participant(
    pid: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    return await _set_participant_status(
        pid=pid,
        audit_action="admin.participants.unfreeze",
        status_value="active",
        body=body,
        request=request,
        db=db,
    )


@router.post("/participants/{pid}/ban")
async def ban_participant(
    pid: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    return await _set_participant_status(
        pid=pid,
        audit_action="admin.participants.ban",
        status_value="deleted",
        body=body,
        request=request,
        db=db,
    )


@router.post("/participants/{pid}/unban")
async def unban_participant(
    pid: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    return await _set_participant_status(
        pid=pid,
        audit_action="admin.participants.unban",
        status_value="active",
        body=body,
        request=request,
        db=db,
    )


@router.get("/audit-log", response_model=AdminAuditLogListResponse)
async def list_audit_log(
    q: str | None = None,
    action: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminAuditLogListResponse:
    base = select(AuditLog)

    if q:
        needle = f"%{q}%"
        base = base.where(
            func.coalesce(cast(AuditLog.id, String), "").ilike(needle)
            | func.coalesce(cast(AuditLog.actor_id, String), "").ilike(needle)
            | func.coalesce(AuditLog.actor_role, "").ilike(needle)
            | func.coalesce(AuditLog.action, "").ilike(needle)
            | func.coalesce(AuditLog.object_type, "").ilike(needle)
            | func.coalesce(AuditLog.object_id, "").ilike(needle)
            | func.coalesce(AuditLog.reason, "").ilike(needle)
            | func.coalesce(AuditLog.request_id, "").ilike(needle)
            | func.coalesce(AuditLog.ip_address, "").ilike(needle)
        )

    if action:
        base = base.where(AuditLog.action == action)
    if object_type:
        base = base.where(AuditLog.object_type == object_type)
    if object_id:
        base = base.where(AuditLog.object_id == object_id)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    stmt = base.order_by(desc(AuditLog.timestamp)).limit(per_page).offset((page - 1) * per_page)
    items = (await db.execute(stmt)).scalars().all()
    return AdminAuditLogListResponse(items=items, page=page, per_page=per_page, total=int(total))


@router.get("/incidents", response_model=AdminIncidentsListResponse)
async def list_incidents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminIncidentsListResponse:
    """List "stuck" payment transactions (over SLA) for operator intervention."""

    sla_seconds = int(getattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120) or 120)
    cutoff = _utc_now() - timedelta(seconds=sla_seconds)

    base = (
        select(Transaction, Participant.pid)
        .join(Participant, Transaction.initiator_id == Participant.id)
        .where(
            Transaction.type == "PAYMENT",
            Transaction.state.in_(_ACTIVE_PAYMENT_TX_STATES),
            Transaction.updated_at < cutoff,
        )
    )

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    stmt = (
        base.order_by(Transaction.updated_at.asc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    rows = (await db.execute(stmt)).all()

    now = _utc_now()
    items: list[dict[str, Any]] = []
    for tx, initiator_pid in rows:
        payload = tx.payload or {}
        equivalent = str(payload.get("equivalent") or "")
        # Prefer updated_at for "stuck" age; fall back to created_at.
        anchor = (tx.updated_at or tx.created_at) or now
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((now - anchor).total_seconds()))
        items.append(
            {
                "tx_id": tx.tx_id,
                "state": tx.state,
                "initiator_pid": str(initiator_pid),
                "equivalent": equivalent,
                "age_seconds": age_seconds,
                "sla_seconds": sla_seconds,
                "created_at": tx.created_at,
            }
        )

    return AdminIncidentsListResponse(items=items, page=page, per_page=per_page, total=int(total))


@router.post("/transactions/{tx_id}/abort", response_model=AdminAbortTxResponse)
async def abort_transaction(
    tx_id: str,
    body: AdminAbortTxRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> AdminAbortTxResponse:
    tx = (
        await db.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one_or_none()
    if tx is None:
        raise NotFoundException(f"Transaction {tx_id} not found")

    before = {"state": tx.state, "error": tx.error}

    engine = PaymentEngine(db)
    await engine.abort(tx_id, reason=body.reason)

    tx2 = (
        await db.execute(select(Transaction).where(Transaction.tx_id == tx_id))
    ).scalar_one()

    await _audit(
        db,
        request=request,
        action="admin.transactions.abort",
        object_type="transaction",
        object_id=tx_id,
        reason=body.reason,
        before_state=before,
        after_state={"state": tx2.state, "error": tx2.error},
    )

    return AdminAbortTxResponse(tx_id=tx_id, status="aborted")


@router.get("/equivalents", response_model=EquivalentsList)
async def admin_list_equivalents(
    include_inactive: bool = Query(False, description="Include inactive equivalents"),
    db: AsyncSession = Depends(deps.get_db),
) -> EquivalentsList:
    stmt = select(EquivalentModel)
    if not include_inactive:
        stmt = stmt.where(EquivalentModel.is_active.is_(True))
    items = (await db.execute(stmt.order_by(EquivalentModel.code.asc()))).scalars().all()
    return EquivalentsList(items=[EquivalentSchema.model_validate(x) for x in items])


@router.post("/equivalents", response_model=EquivalentSchema)
async def admin_create_equivalent(
    body: AdminEquivalentCreateRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> EquivalentSchema:
    eq = EquivalentModel(
        code=body.code,
        symbol=body.symbol,
        description=body.description,
        precision=body.precision,
        metadata_=body.metadata,
        is_active=body.is_active,
    )
    db.add(eq)
    await db.commit()
    await db.refresh(eq)
    await _audit(
        db,
        request=request,
        action="admin.equivalents.create",
        object_type="equivalent",
        object_id=eq.code,
        reason=body.reason,
        before_state=None,
        after_state={"code": eq.code, "is_active": eq.is_active},
    )
    return EquivalentSchema.model_validate(eq)


@router.patch("/equivalents/{code}", response_model=EquivalentSchema)
async def admin_update_equivalent(
    code: str,
    body: AdminEquivalentUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> EquivalentSchema:
    eq = (
        await db.execute(select(EquivalentModel).where(EquivalentModel.code == code))
    ).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {code} not found")

    before = {
        "symbol": eq.symbol,
        "description": eq.description,
        "precision": eq.precision,
        "metadata": eq.metadata_,
        "is_active": eq.is_active,
    }
    if body.symbol is not None:
        eq.symbol = body.symbol
    if body.description is not None:
        eq.description = body.description
    if body.precision is not None:
        eq.precision = body.precision
    if body.metadata is not None:
        eq.metadata_ = body.metadata
    if body.is_active is not None:
        eq.is_active = body.is_active

    await db.commit()
    await db.refresh(eq)

    after = {
        "symbol": eq.symbol,
        "description": eq.description,
        "precision": eq.precision,
        "metadata": eq.metadata_,
        "is_active": eq.is_active,
    }
    await _audit(
        db,
        request=request,
        action="admin.equivalents.patch",
        object_type="equivalent",
        object_id=eq.code,
        reason=body.reason,
        before_state=before,
        after_state=after,
    )

    return EquivalentSchema.model_validate(eq)


async def _equivalent_usage_counts(db: AsyncSession, *, equivalent_id) -> dict[str, int]:
    trustlines = (
        await db.execute(select(func.count()).select_from(TrustLine).where(TrustLine.equivalent_id == equivalent_id))
    ).scalar_one()
    debts = (await db.execute(select(func.count()).select_from(Debt).where(Debt.equivalent_id == equivalent_id))).scalar_one()
    integrity_checkpoints = (
        await db.execute(
            select(func.count())
            .select_from(IntegrityCheckpoint)
            .where(IntegrityCheckpoint.equivalent_id == equivalent_id)
        )
    ).scalar_one()

    return {
        "trustlines": int(trustlines or 0),
        "debts": int(debts or 0),
        "integrity_checkpoints": int(integrity_checkpoints or 0),
    }


@router.get("/equivalents/{code}/usage", response_model=AdminEquivalentUsageResponse)
async def admin_equivalent_usage(
    code: str,
    db: AsyncSession = Depends(deps.get_db),
) -> AdminEquivalentUsageResponse:
    normalized = str(code or "").strip().upper()
    validate_equivalent_code(normalized)

    eq = (
        await db.execute(select(EquivalentModel).where(EquivalentModel.code == normalized))
    ).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {normalized} not found")

    counts = await _equivalent_usage_counts(db, equivalent_id=eq.id)
    return AdminEquivalentUsageResponse(code=eq.code, **counts)


@router.delete("/equivalents/{code}", response_model=AdminDeleteResponse)
async def admin_delete_equivalent(
    code: str,
    body: AdminEquivalentDeleteRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> AdminDeleteResponse:
    normalized = str(code or "").strip().upper()
    validate_equivalent_code(normalized)

    eq = (
        await db.execute(select(EquivalentModel).where(EquivalentModel.code == normalized))
    ).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {normalized} not found")

    if eq.is_active:
        raise ConflictException("Deactivate equivalent before delete")

    counts = await _equivalent_usage_counts(db, equivalent_id=eq.id)
    if any(v > 0 for v in counts.values()):
        raise ConflictException("Equivalent is in use", details=counts)

    before = {
        "code": eq.code,
        "symbol": eq.symbol,
        "description": eq.description,
        "precision": eq.precision,
        "metadata": eq.metadata_,
        "is_active": eq.is_active,
    }

    await db.delete(eq)
    await db.commit()

    await _audit(
        db,
        request=request,
        action="admin.equivalents.delete",
        object_type="equivalent",
        object_id=normalized,
        reason=body.reason,
        before_state=before,
        after_state=None,
    )

    return AdminDeleteResponse(deleted=normalized)


@router.get("/migrations", response_model=AdminMigrationsStatus, dependencies=[])
async def migrations_status() -> AdminMigrationsStatus:
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        repo_root = Path(__file__).resolve().parents[3]
        alembic_ini = repo_root / "migrations" / "alembic.ini"
        cfg = Config(str(alembic_ini))
        cfg.set_main_option("script_location", str(repo_root / "migrations"))

        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()

        from app.db.session import engine

        with engine.sync_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current = ctx.get_current_revision()

        return AdminMigrationsStatus(
            current_revision=current,
            head_revision=head,
            is_up_to_date=(current == head and current is not None),
        )
    except Exception:
        return AdminMigrationsStatus(current_revision=None, head_revision=None, is_up_to_date=False)


@router.get("/trustlines", response_model=AdminTrustLinesListResponse)
async def admin_list_trustlines(
    equivalent: str | None = None,
    creditor: str | None = Query(None, description="Creditor PID (trustline 'from')"),
    debtor: str | None = Query(None, description="Debtor PID (trustline 'to')"),
    status: Literal["active", "frozen", "closed"] | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminTrustLinesListResponse:
    service = TrustLineService(db)
    offset = (page - 1) * per_page

    total = await service.count_all(
        equivalent=equivalent,
        creditor_pid=creditor,
        debtor_pid=debtor,
        status=status,
    )

    items = await service.list_all(
        equivalent=equivalent,
        creditor_pid=creditor,
        debtor_pid=debtor,
        status=status,
        limit=per_page,
        offset=offset,
    )
    return AdminTrustLinesListResponse(items=items, page=page, per_page=per_page, total=int(total))


@router.get("/graph/snapshot", response_model=AdminGraphSnapshotResponse)
async def admin_graph_snapshot(
    include: str | None = Query(
        None,
        description="Optional extras to include (comma-separated): incidents,audit_log,transactions",
    ),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminGraphSnapshotResponse:
    """Return a GraphPage-compatible snapshot.

    Guardrail: TrustLine direction in output is from→to = creditor→debtor.
    """

    # Participants
    participants_rows = (
        await db.execute(
            select(Participant.pid, Participant.display_name, Participant.type, Participant.status)
            .order_by(Participant.pid.asc())
        )
    ).all()
    participants = [
        AdminGraphParticipant(
            pid=pid,
            display_name=display_name,
            type=type_,
            status=str(status or "").strip().lower(),
        )
        for pid, display_name, type_, status in participants_rows
    ]

    # Equivalents
    eq_models = (
        await db.execute(select(EquivalentModel).order_by(EquivalentModel.code.asc()))
    ).scalars().all()
    equivalents = [EquivalentSchema.model_validate(e) for e in eq_models]

    # Trustlines + used/available (no N+1)
    p_from = aliased(Participant)
    p_to = aliased(Participant)
    tl_stmt = (
        select(
            TrustLine.id,
            TrustLine.limit,
            TrustLine.status,
            TrustLine.created_at,
            TrustLine.updated_at,
            TrustLine.policy,
            EquivalentModel.code.label("equivalent"),
            p_from.pid.label("from_pid"),
            p_from.display_name.label("from_display_name"),
            p_to.pid.label("to_pid"),
            p_to.display_name.label("to_display_name"),
            func.coalesce(Debt.amount, 0).label("used"),
        )
        .select_from(TrustLine)
        .join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id)
        .join(p_from, TrustLine.from_participant_id == p_from.id)
        .join(p_to, TrustLine.to_participant_id == p_to.id)
        .outerjoin(
            Debt,
            and_(
                Debt.debtor_id == TrustLine.to_participant_id,
                Debt.creditor_id == TrustLine.from_participant_id,
                Debt.equivalent_id == TrustLine.equivalent_id,
            ),
        )
        .order_by(EquivalentModel.code.asc(), p_from.pid.asc(), p_to.pid.asc())
    )

    tl_rows = (await db.execute(tl_stmt)).all()
    trustlines: list[TrustLineSchema] = []
    for (
        tl_id,
        limit,
        status,
        created_at,
        updated_at,
        policy,
        equivalent_code,
        from_pid,
        from_display_name,
        to_pid,
        to_display_name,
        used,
    ) in tl_rows:
        used_dec = used
        available = limit - used_dec
        trustlines.append(
            TrustLineSchema.model_validate(
                {
                    "id": tl_id,
                    "from_pid": from_pid,
                    "to_pid": to_pid,
                    "from_display_name": from_display_name,
                    "to_display_name": to_display_name,
                    "equivalent_code": equivalent_code,
                    "limit": limit,
                    "used": used_dec,
                    "available": available,
                    "status": status,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "policy": policy,
                }
            )
        )

    # Debts
    p_debtor = aliased(Participant)
    p_creditor = aliased(Participant)
    debt_stmt = (
        select(
            EquivalentModel.code.label("equivalent"),
            p_debtor.pid.label("debtor"),
            p_creditor.pid.label("creditor"),
            Debt.amount,
        )
        .select_from(Debt)
        .join(EquivalentModel, Debt.equivalent_id == EquivalentModel.id)
        .join(p_debtor, Debt.debtor_id == p_debtor.id)
        .join(p_creditor, Debt.creditor_id == p_creditor.id)
        .where(Debt.amount > 0)
        .order_by(EquivalentModel.code.asc(), p_debtor.pid.asc(), p_creditor.pid.asc())
    )
    debt_rows = (await db.execute(debt_stmt)).all()
    debts = [
        AdminGraphDebt(equivalent=eq, debtor=debtor, creditor=creditor, amount=amount)
        for eq, debtor, creditor, amount in debt_rows
    ]

    include_set = _parse_include_csv(include)
    incidents: list[Any] = []
    audit_log: list[Any] = []
    transactions: list[Any] = []
    if include_set:
        if "incidents" in include_set:
            incidents = await _graph_fetch_incidents(
                db,
                limit=int(getattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_INCIDENTS", 50) or 50),
            )
        if "audit_log" in include_set:
            audit_log = await _graph_fetch_audit_log(
                db,
                limit=int(getattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_AUDIT_EVENTS", 50) or 50),
            )
        if "transactions" in include_set:
            transactions = await _graph_fetch_transactions(
                db,
                limit=int(getattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS", 50) or 50),
            )

    return AdminGraphSnapshotResponse(
        participants=participants,
        trustlines=trustlines,
        incidents=incidents,
        equivalents=equivalents,
        debts=debts,
        audit_log=audit_log,
        transactions=transactions,
    )


@router.get("/graph/ego", response_model=AdminGraphEgoResponse)
async def admin_graph_ego(
    pid: str = Query(..., description="Root participant PID"),
    depth: int = Query(1, ge=1, le=2, description="Neighborhood depth (1–2)"),
    equivalent: str | None = Query(None, description="Optional equivalent code filter"),
    status: list[str] | None = Query(None, description="Optional trustline statuses filter (repeatable)"),
    include: str | None = Query(
        None,
        description="Optional extras to include (comma-separated): incidents,audit_log,transactions",
    ),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminGraphEgoResponse:
    """Return a GraphPage-compatible ego snapshot around one participant.

    Notes:
    - Neighborhood is computed on the trustline graph as an undirected graph.
    - Returned trustlines and debts are restricted to the ego participant set.
    """

    root_pid = str(pid or "").strip()
    if not root_pid:
        raise BadRequestException("pid is required")

    if equivalent is not None:
        validate_equivalent_code(equivalent)

    root = (
        await db.execute(select(Participant).where(Participant.pid == root_pid))
    ).scalar_one_or_none()
    if not root:
        raise NotFoundException("Participant not found")

    visited_ids: set[Any] = {root.id}
    frontier_ids: set[Any] = {root.id}

    for _ in range(int(depth)):
        if not frontier_ids:
            break

        tl_pairs_stmt = (
            select(TrustLine.from_participant_id, TrustLine.to_participant_id)
            .select_from(TrustLine)
            .where(
                (TrustLine.from_participant_id.in_(list(frontier_ids)))
                | (TrustLine.to_participant_id.in_(list(frontier_ids)))
            )
        )

        if equivalent:
            tl_pairs_stmt = tl_pairs_stmt.join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id).where(
                EquivalentModel.code == equivalent
            )
        if status:
            tl_pairs_stmt = tl_pairs_stmt.where(TrustLine.status.in_(list(status)))

        tl_pairs = (await db.execute(tl_pairs_stmt)).all()

        next_frontier: set[Any] = set()
        for from_id, to_id in tl_pairs:
            if from_id in frontier_ids and to_id not in visited_ids:
                next_frontier.add(to_id)
            if to_id in frontier_ids and from_id not in visited_ids:
                next_frontier.add(from_id)

        visited_ids |= next_frontier
        frontier_ids = next_frontier

    # Participants
    participants_rows = (
        await db.execute(
            select(Participant.pid, Participant.display_name, Participant.type, Participant.status)
            .where(Participant.id.in_(list(visited_ids)))
            .order_by(Participant.pid.asc())
        )
    ).all()
    participants = [
        AdminGraphParticipant(
            pid=pid,
            display_name=display_name,
            type=type_,
            status=str(status or "").strip().lower(),
        )
        for pid, display_name, type_, status in participants_rows
    ]

    # Equivalents (keep full list for UI dropdown)
    eq_models = (
        await db.execute(select(EquivalentModel).order_by(EquivalentModel.code.asc()))
    ).scalars().all()
    equivalents = [EquivalentSchema.model_validate(e) for e in eq_models]

    # Trustlines + used/available (no N+1)
    p_from = aliased(Participant)
    p_to = aliased(Participant)
    tl_stmt = (
        select(
            TrustLine.id,
            TrustLine.limit,
            TrustLine.status,
            TrustLine.created_at,
            TrustLine.updated_at,
            TrustLine.policy,
            EquivalentModel.code.label("equivalent"),
            p_from.pid.label("from_pid"),
            p_from.display_name.label("from_display_name"),
            p_to.pid.label("to_pid"),
            p_to.display_name.label("to_display_name"),
            func.coalesce(Debt.amount, 0).label("used"),
        )
        .select_from(TrustLine)
        .join(EquivalentModel, TrustLine.equivalent_id == EquivalentModel.id)
        .join(p_from, TrustLine.from_participant_id == p_from.id)
        .join(p_to, TrustLine.to_participant_id == p_to.id)
        .outerjoin(
            Debt,
            and_(
                Debt.debtor_id == TrustLine.to_participant_id,
                Debt.creditor_id == TrustLine.from_participant_id,
                Debt.equivalent_id == TrustLine.equivalent_id,
            ),
        )
        .where(
            TrustLine.from_participant_id.in_(list(visited_ids)),
            TrustLine.to_participant_id.in_(list(visited_ids)),
        )
        .where(EquivalentModel.code == equivalent if equivalent else True)
        .where(TrustLine.status.in_(list(status)) if status else True)
        .order_by(EquivalentModel.code.asc(), p_from.pid.asc(), p_to.pid.asc())
    )
    tl_rows = (await db.execute(tl_stmt)).all()
    trustlines: list[TrustLineSchema] = []
    for (
        tl_id,
        limit,
        status,
        created_at,
        updated_at,
        policy,
        equivalent_code,
        from_pid,
        from_display_name,
        to_pid,
        to_display_name,
        used,
    ) in tl_rows:
        available = limit - used
        trustlines.append(
            TrustLineSchema.model_validate(
                {
                    "id": tl_id,
                    "from_pid": from_pid,
                    "to_pid": to_pid,
                    "from_display_name": from_display_name,
                    "to_display_name": to_display_name,
                    "equivalent_code": equivalent_code,
                    "limit": limit,
                    "used": used,
                    "available": available,
                    "status": status,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "policy": policy,
                }
            )
        )

    # Debts
    p_debtor = aliased(Participant)
    p_creditor = aliased(Participant)
    debt_stmt = (
        select(
            EquivalentModel.code.label("equivalent"),
            p_debtor.pid.label("debtor"),
            p_creditor.pid.label("creditor"),
            Debt.amount,
        )
        .select_from(Debt)
        .join(EquivalentModel, Debt.equivalent_id == EquivalentModel.id)
        .join(p_debtor, Debt.debtor_id == p_debtor.id)
        .join(p_creditor, Debt.creditor_id == p_creditor.id)
        .where(
            Debt.amount > 0,
            Debt.debtor_id.in_(list(visited_ids)),
            Debt.creditor_id.in_(list(visited_ids)),
        )
        .where(EquivalentModel.code == equivalent if equivalent else True)
        .order_by(EquivalentModel.code.asc(), p_debtor.pid.asc(), p_creditor.pid.asc())
    )
    debt_rows = (await db.execute(debt_stmt)).all()
    debts = [
        AdminGraphDebt(equivalent=eq, debtor=debtor, creditor=creditor, amount=amount)
        for eq, debtor, creditor, amount in debt_rows
    ]

    include_set = _parse_include_csv(include)
    incidents: list[Any] = []
    audit_log: list[Any] = []
    transactions: list[Any] = []
    if include_set:
        if "incidents" in include_set:
            incidents = await _graph_fetch_incidents(
                db,
                limit=int(getattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_INCIDENTS", 50) or 50),
            )
        if "audit_log" in include_set:
            audit_log = await _graph_fetch_audit_log(
                db,
                limit=int(getattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_AUDIT_EVENTS", 50) or 50),
            )
        if "transactions" in include_set:
            transactions = await _graph_fetch_transactions(
                db,
                limit=int(getattr(settings, "ADMIN_GRAPH_INCLUDE_MAX_TRANSACTIONS", 50) or 50),
            )

    return AdminGraphEgoResponse(
        root_pid=root_pid,
        participants=participants,
        trustlines=trustlines,
        equivalents=equivalents,
        debts=debts,
        incidents=incidents,
        audit_log=audit_log,
        transactions=transactions,
    )


@router.get("/clearing/cycles", response_model=AdminClearingCyclesResponse)
async def admin_clearing_cycles(
    participant_pid: str | None = Query(
        None,
        description="Optional PID filter: return only cycles that touch this participant",
    ),
    equivalent: str | None = Query(
        None,
        description="Optional equivalent code (if omitted, returns cycles for all equivalents)",
    ),
    max_depth: int = Query(6, ge=3, le=10),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminClearingCyclesResponse:
    if equivalent is not None:
        validate_equivalent_code(equivalent)

    codes: list[str]
    if equivalent:
        codes = [equivalent]
    else:
        codes = (
            await db.execute(select(EquivalentModel.code).order_by(EquivalentModel.code.asc()))
        ).scalars().all()

    service = ClearingService(db)
    pid_filter = str(participant_pid or "").strip() or None

    out: dict[str, AdminClearingCyclesForEquivalent] = {}
    for code in codes:
        try:
            raw_cycles = await service.find_cycles(code, max_depth=max_depth)
        except Exception:
            raw_cycles = []

        cycles: list[list[AdminClearingCycleEdge]] = []
        for cycle in raw_cycles:
            edges: list[AdminClearingCycleEdge] = []
            for e in cycle:
                debtor = str((e or {}).get("debtor") or "")
                creditor = str((e or {}).get("creditor") or "")
                amount_raw = (e or {}).get("amount")
                try:
                    amount = amount_raw if hasattr(amount_raw, "as_tuple") else str(amount_raw)
                except Exception:
                    amount = str(amount_raw)

                edges.append(
                    AdminClearingCycleEdge(
                        equivalent=code,
                        debtor=debtor,
                        creditor=creditor,
                        amount=amount,
                    )
                )

            if pid_filter and not any(
                (edge.debtor == pid_filter or edge.creditor == pid_filter) for edge in edges
            ):
                continue
            cycles.append(edges)

        out[code] = AdminClearingCyclesForEquivalent(cycles=cycles)

    # If the user requested a single equivalent, keep output deterministic but complete.
    # (GraphPage fixture shape includes other equivalents too.)
    if equivalent and not pid_filter:
        # Optionally include other equivalents as empty cycles for UI parity.
        other_codes = (
            await db.execute(select(EquivalentModel.code).order_by(EquivalentModel.code.asc()))
        ).scalars().all()
        for code in other_codes:
            out.setdefault(code, AdminClearingCyclesForEquivalent(cycles=[]))

    return AdminClearingCyclesResponse(equivalents=out)


@router.get("/participants/{pid}/metrics", response_model=AdminParticipantMetricsResponse)
async def admin_participant_metrics(
    pid: str,
    equivalent: str | None = Query(default=None),
    threshold: float | None = Query(default=None, ge=0.0, le=1.0),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminParticipantMetricsResponse:
    return await compute_participant_metrics(db, pid=pid, equivalent=equivalent, threshold=threshold)
