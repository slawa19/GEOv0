from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import String, cast, desc, func, select, and_
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
    AdminTrustLinesListResponse,
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

    settings.FEATURE_FLAGS_MULTIPATH_ENABLED = body.multipath_enabled
    settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED = body.full_multipath_enabled
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
async def admin_list_equivalents(db: AsyncSession = Depends(deps.get_db)) -> EquivalentsList:
    items = (
        await db.execute(select(EquivalentModel).order_by(EquivalentModel.code.asc()))
    ).scalars().all()
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

    return AdminGraphSnapshotResponse(
        participants=participants,
        trustlines=trustlines,
        incidents=[],
        equivalents=equivalents,
        debts=debts,
        audit_log=[],
        transactions=[],
    )


@router.get("/graph/ego", response_model=AdminGraphEgoResponse)
async def admin_graph_ego(
    pid: str = Query(..., description="Root participant PID"),
    depth: int = Query(1, ge=1, le=2, description="Neighborhood depth (1–2)"),
    equivalent: str | None = Query(None, description="Optional equivalent code filter"),
    status: list[str] | None = Query(None, description="Optional trustline statuses filter (repeatable)"),
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

    return AdminGraphEgoResponse(
        root_pid=root_pid,
        participants=participants,
        trustlines=trustlines,
        equivalents=equivalents,
        debts=debts,
        incidents=[],
        audit_log=[],
        transactions=[],
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
