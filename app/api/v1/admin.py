from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.config import settings
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent as EquivalentModel
from app.db.models.participant import Participant
from app.schemas.admin import (
    AdminAuditLogResponse,
    AdminConfigPatchRequest,
    AdminConfigPatchResponse,
    AdminConfigResponse,
    AdminEquivalentCreateRequest,
    AdminEquivalentUpdateRequest,
    AdminFeatureFlags,
    AdminFeatureFlagsPatchRequest,
    AdminMigrationsStatus,
    AdminParticipantActionRequest,
)
from app.schemas.equivalents import Equivalent as EquivalentSchema
from app.schemas.equivalents import EquivalentsList
from app.utils.exceptions import BadRequestException, NotFoundException


router = APIRouter(prefix="/admin", dependencies=[Depends(deps.require_admin)])


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
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
):
    stmt = select(Participant).order_by(Participant.id.asc())
    if q:
        stmt = stmt.where(
            (Participant.pid.ilike(f"%{q}%")) | (Participant.display_name.ilike(f"%{q}%"))
        )
    if status:
        stmt = stmt.where(Participant.status == status)
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    items = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "pid": p.pid,
                "display_name": p.display_name,
                "type": p.type,
                "status": p.status,
                "verification_level": p.verification_level,
                "created_at": p.created_at,
            }
            for p in items
        ]
    }


async def _set_participant_status(
    *,
    pid: str,
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
        action=f"admin.participants.{status_value}",
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
        pid=pid, status_value="suspended", body=body, request=request, db=db
    )


@router.post("/participants/{pid}/unfreeze")
async def unfreeze_participant(
    pid: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    return await _set_participant_status(
        pid=pid, status_value="active", body=body, request=request, db=db
    )


@router.post("/participants/{pid}/ban")
async def ban_participant(
    pid: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    return await _set_participant_status(
        pid=pid, status_value="deleted", body=body, request=request, db=db
    )


@router.post("/participants/{pid}/unban")
async def unban_participant(
    pid: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
):
    return await _set_participant_status(
        pid=pid, status_value="active", body=body, request=request, db=db
    )


@router.get("/audit-log", response_model=AdminAuditLogResponse)
async def list_audit_log(
    action: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminAuditLogResponse:
    stmt = select(AuditLog).order_by(desc(AuditLog.timestamp))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if object_type:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditLog.object_id == object_id)
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    items = (await db.execute(stmt)).scalars().all()
    return AdminAuditLogResponse(items=items)


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
