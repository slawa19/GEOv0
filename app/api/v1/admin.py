from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, TypeAdapter, ValidationError, WithJsonSchema
from sqlalchemy import String, cast, desc, func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.audit import add_audit_entry, audited
from app.config import Settings, settings
from app.db.models.audit_log import AuditLog
from app.db.models.equivalent import Equivalent as EquivalentModel
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.admin import (
    AdminAuditLogListResponse,
    AdminConfigPatchRequest,
    AdminConfigPatchResponse,
    AdminConfigResponse,
    AdminEquivalentCreateRequest,
    AdminEquivalentDeleteRequest,
    AdminEquivalentIntegrityHoldClearRequest,
    AdminEquivalentUpdateRequest,
    AdminEquivalentUsageResponse,
    AdminDeleteResponse,
    AdminMigrationsStatus,
    AdminParticipantActionRequest,
    AdminParticipantStatusResponse,
    AdminParticipantsListResponse,
    AdminLiquiditySummaryResponse,
    AdminParticipantsStatsResponse,
    AdminTrustLinesListResponse,
)
from app.schemas.equivalents import Equivalent as EquivalentSchema
from app.schemas.equivalents import EquivalentsList, StoredEquivalent
from app.schemas.common import ErrorEnvelope
from app.schemas.graph import (
    AdminGraphEgoResponse,
    AdminGraphSnapshotResponse,
)
from app.core.payments.router import PaymentRouter
from app.core.admin.graph import (
    ego_participant_ids,
    load_graph,
    trustline_page_statements,
    trustline_schema,
)
from app.core.admin.metrics import compute_participant_metrics
from app.core.participants.service import ParticipantService
from app.core import equivalents as equivalents_core
from app.utils.exceptions import (
    BadRequestException,
    NotFoundException,
)
from app.utils.validation import validate_equivalent_code

from app.schemas.metrics import AdminParticipantMetricsResponse



router = APIRouter(prefix="/admin", dependencies=[Depends(deps.require_admin)])

logger = logging.getLogger(__name__)

_runtime_config_lock = asyncio.Lock()


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

    # A DB value (or anything else) filters on itself; an unknown value matches nothing.
    return [v]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _runtime_config_items() -> list[tuple[str, bool]]:
    # (key, mutable). 029 `F-029-4`: a key is mutable only if changing it at runtime does what its name says.
    # LOG_LEVEL and the integrity job's switch and period are taken once at start. The three recovery keys, which had
    # no reader since programme 019, were removed with the incidents surface (032 S5, A-4).
    return [
        ("LOG_LEVEL", False),
        ("RATE_LIMIT_ENABLED", True),
        ("ROUTING_MAX_HOPS", True),
        ("ROUTING_MAX_PATHS", True),
        ("INTEGRITY_CHECKPOINT_ENABLED", False),
        ("INTEGRITY_CHECKPOINT_INTERVAL_SECONDS", False),
        ("FEATURE_FLAGS_MULTIPATH_ENABLED", True),
        ("FEATURE_FLAGS_FULL_MULTIPATH_ENABLED", True),
        ("CLEARING_ENABLED", True),
    ]


def _validate_runtime_config_updates(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = {key for key, mutable in _runtime_config_items() if mutable}
    validated: dict[str, Any] = {}

    for key, value in updates.items():
        if key not in allowed:
            raise BadRequestException(f"Config key not mutable: {key}")

        field = Settings.model_fields[key]
        try:
            validated[key] = TypeAdapter(field.annotation).validate_python(
                value,
                strict=True,
            )
        except ValidationError as exc:
            raise BadRequestException(
                f"Invalid value for config key: {key}",
                details={"key": key},
            ) from exc

    return validated


async def _required_audit_and_publish(
    db: AsyncSession,
    *,
    publish: Callable[[], None],
    request: Request,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    reason: str | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    async def _operation() -> None:
        add_audit_entry(
            db,
            request=request,
            action=action,
            object_type=object_type,
            object_id=object_id,
            reason=reason,
            before_state=before_state,
            after_state=after_state,
        )
        await db.commit()
        publish()

    task = asyncio.create_task(_operation())
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.done():
                continue
            current = asyncio.current_task()
            if current is None or not current.cancelling():
                continue
            if cancellation is not None:
                # Keep the request session and runtime-config lock alive until
                # the cancelled database operation has reached a terminal
                # state. Detaching here would race dependency cleanup and let
                # an older publish overwrite a newer patch.
                task.cancel("repeated caller cancellation")
                continue
            cancellation = exc
        except Exception:
            pass

    operation_error: BaseException | None = None
    try:
        task.result()
    except BaseException as exc:
        operation_error = exc

    if operation_error is not None:
        await db.rollback()
    if cancellation is not None:
        raise cancellation
    if operation_error is not None:
        raise operation_error


@router.get("/config", response_model=AdminConfigResponse)
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
    async with _runtime_config_lock:
        validated = _validate_runtime_config_updates(body.updates or {})
        before = {key: getattr(settings, key) for key in validated}
        after = dict(validated)

        def _publish() -> None:
            try:
                for key, value in validated.items():
                    setattr(settings, key, value)
            except BaseException:
                for key, value in before.items():
                    setattr(settings, key, value)
                raise

        await _required_audit_and_publish(
            db,
            publish=_publish,
            request=request,
            action="admin.config.patch",
            object_type="config",
            object_id=None,
            reason=body.reason,
            before_state=before or None,
            after_state=after or None,
        )

        return AdminConfigPatchResponse(updated=list(validated))


def _ilike_contains(column, needle: str):
    """`column ILIKE '%needle%'` with the needle taken literally (032 A-11): `%` and `_` typed by the operator are
    characters to find, not wildcards. `\\` is the escape, so it is escaped first."""

    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


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
        base = base.where(_ilike_contains(Participant.pid, q) | _ilike_contains(Participant.display_name, q))
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


@router.get("/liquidity/summary", response_model=AdminLiquiditySummaryResponse)
async def admin_liquidity_summary(
    equivalent: str | None = Query(None, description="Equivalent code (optional; omitted: counters only, money null)"),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminLiquiditySummaryResponse:
    """The Dashboard's row of one equivalent: its active lines and their money (032 S5, F-2, F-3).

    Narrowed by the owner's decision of 2026-10-07: the ranked lists, the bottleneck count and edges and the
    incidents counter left with the Liquidity screen. The equivalent is not required to be active - a stopped
    equivalent still shows its own sums.
    """

    eq_code = str(equivalent or "").strip().upper() or None
    now = _utc_now()

    used_expr = func.coalesce(Debt.amount, 0)
    available_expr = TrustLine.limit - used_expr

    # 028 F-028-37 (owner В-3): money is summed only within one equivalent. Without one only the lines are
    # counted - no money SUM runs at all (§15 review of E5) - and the totals are null.
    money_sums = (
        [
            func.coalesce(func.sum(TrustLine.limit), 0).label("total_limit"),
            func.coalesce(func.sum(used_expr), 0).label("total_used"),
            func.coalesce(func.sum(available_expr), 0).label("total_available"),
        ]
        if eq_code
        else []
    )
    totals_stmt = (
        select(func.count().label("active_trustlines"), *money_sums)
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

    return AdminLiquiditySummaryResponse(
        equivalent=eq_code,
        updated_at=now,
        active_trustlines=int(totals.active_trustlines or 0),
        total_limit=totals.total_limit if eq_code else None,
        total_used=totals.total_used if eq_code else None,
        total_available=totals.total_available if eq_code else None,
    )


#: 032 A-5: the operator's status matrix - only freeze and unfreeze since the ban was removed (F-5). The key is the
#: status a command sets, the value the statuses it may start from; anything else is a 409, a repeat included.
_OPERATOR_STATUS_SOURCES: dict[str, tuple[str, ...]] = {
    "suspended": ("active",),  # freeze
    "active": ("suspended",),  # unfreeze
}

_STATUS_TRANSITION_REFUSED = {
    409: {
        "model": ErrorEnvelope,
        "description": "The participant's status is not the one this command starts from "
        "(status_transition_not_allowed); nothing changed",
    }
}


async def _set_participant_status(
    *,
    pid: str,
    audit_action: str,
    status_value: str,
    body: AdminParticipantActionRequest,
    request: Request,
    db: AsyncSession,
) -> dict:
    # The lock, the matrix check and the mutation are the core's (`ParticipantService.set_status`, 030 S3b, 032 A-5);
    # the audit and the commit here.
    async with audited(db, request=request, action=audit_action, object_type="participant", object_id=pid,
                       reason=body.reason) as audit:
        participant, before_status = await ParticipantService(db).set_status(
            pid, status_value, from_statuses=_OPERATOR_STATUS_SOURCES[status_value])
        result = {"pid": participant.pid, "status": participant.status}
        audit.before_state = {"status": before_status}
        audit.after_state = {"status": participant.status}
    # The route graphs skip a suspended participant's lines (a hint; the core's refusal is the boundary).
    PaymentRouter.invalidate_cache()

    return result


@router.post(
    "/participants/{pid}/freeze",
    response_model=AdminParticipantStatusResponse,
    responses=_STATUS_TRANSITION_REFUSED,
)
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


@router.post(
    "/participants/{pid}/unfreeze",
    response_model=AdminParticipantStatusResponse,
    responses=_STATUS_TRANSITION_REFUSED,
)
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
        base = base.where(
            _ilike_contains(func.coalesce(cast(AuditLog.id, String), ""), q)
            | _ilike_contains(func.coalesce(cast(AuditLog.actor_id, String), ""), q)
            | _ilike_contains(func.coalesce(AuditLog.actor_role, ""), q)
            | _ilike_contains(func.coalesce(AuditLog.action, ""), q)
            | _ilike_contains(func.coalesce(AuditLog.object_type, ""), q)
            | _ilike_contains(func.coalesce(AuditLog.object_id, ""), q)
            | _ilike_contains(func.coalesce(AuditLog.reason, ""), q)
            | _ilike_contains(func.coalesce(AuditLog.request_id, ""), q)
            | _ilike_contains(func.coalesce(AuditLog.ip_address, ""), q)
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

    # `id` breaks the ties of one timestamp (032 A-11): without it rows of one instant page in planner order, and an
    # operator paging through them can see one twice and another never.
    stmt = (
        base.order_by(desc(AuditLog.timestamp), desc(AuditLog.id))
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    items = (await db.execute(stmt)).scalars().all()
    return AdminAuditLogListResponse(items=items, page=page, per_page=per_page, total=int(total))


@router.get("/equivalents", response_model=EquivalentsList)
async def admin_list_equivalents(
    include_inactive: bool = Query(False, description="Include inactive equivalents"),
    db: AsyncSession = Depends(deps.get_db),
) -> EquivalentsList:
    stmt = select(EquivalentModel)
    if not include_inactive:
        stmt = stmt.where(EquivalentModel.is_active.is_(True))
    items = (await db.execute(stmt.order_by(EquivalentModel.code.asc()))).scalars().all()
    return EquivalentsList(items=[StoredEquivalent.model_validate(x) for x in items])


#: 032 A-11: PATCH, DELETE and usage normalise the path code (`equivalents_core.canonical_code`); one that cannot
#: exist after that is a 400, declared on both halves of the contract.
_CODE_CANNOT_EXIST = {400: {"model": ErrorEnvelope, "description": "Not an equivalent code after normalisation"}}


@router.post(
    "/equivalents",
    response_model=EquivalentSchema,
    responses={409: {"model": ErrorEnvelope, "description": "The code is taken (code_exists); nothing created"}},
)
async def admin_create_equivalent(
    body: AdminEquivalentCreateRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> EquivalentSchema:
    # 024 `T2412.2`, 030 F-030-9: the row and its baseline in this transaction, by the one core function.
    async with audited(db, request=request, action="admin.equivalents.create", object_type="equivalent",
                       object_id=body.code, reason=body.reason) as audit:
        eq = await equivalents_core.create_equivalent(
            db,
            code=body.code,
            symbol=body.symbol,
            description=body.description,
            precision=body.precision,
            metadata_=body.metadata,
            is_active=body.is_active,
        )
        await db.refresh(eq)
        result = EquivalentSchema.model_validate(eq)
        audit.object_id = eq.code
        audit.after_state = {"code": eq.code, "is_active": eq.is_active}
    return result


@router.patch(
    "/equivalents/{code}",
    response_model=EquivalentSchema,
    responses={
        **_CODE_CANNOT_EXIST,
        409: {
            "model": ErrorEnvelope,
            "description": "Stored equivalent requires an explicit legacy-data repair, or a lower precision under "
            "stored lines, debts or journal entries (precision_in_use)",
        }
    },
)
async def admin_update_equivalent(
    code: str,
    body: AdminEquivalentUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> EquivalentSchema:
    # The stop and the step are the core's (`app/core/equivalents.py`, 032 A-6): its row lock, its checks.
    normalized = equivalents_core.canonical_code(code)
    async with audited(db, request=request, action="admin.equivalents.patch", object_type="equivalent",
                       object_id=normalized, reason=body.reason) as audit:
        eq, audit.before_state, audit.after_state = await equivalents_core.update_equivalent(
            db,
            normalized,
            symbol=body.symbol,
            description=body.description,
            precision=body.precision,
            metadata=body.metadata,
            is_active=body.is_active,
        )
        result = EquivalentSchema.model_validate(eq)
    return result


_ResultStatus = Literal["PASSED", "FAILED", "UNVERIFIABLE"]
# The null is IN the enum, as the canon requires beside `nullable` (`test_p011_nullable_needs_a_sibling_type`).
_NullableResultStatus = Annotated[
    _ResultStatus | None,
    WithJsonSchema({"type": "string", "nullable": True, "enum": ["PASSED", "FAILED", "UNVERIFIABLE", None]}),
]


class IntegrityHoldClearRefusalDetails(BaseModel):
    """Documentation only (030 F-030-10): the `details` of the clear's 409, as `clear_integrity_hold` writes them."""

    reason: Literal["no_integrity_hold", "no_later_passed_reconciliation_result"]
    latest_status: _NullableResultStatus = None
    recheck_status: _NullableResultStatus = None


class IntegrityHoldClearRefusalError(BaseModel):
    code: str
    message: str
    details: IntegrityHoldClearRefusalDetails  # both refusals below carry it
    request_id: str | None = None


class IntegrityHoldClearRefusal(BaseModel):
    error: IntegrityHoldClearRefusalError


@router.post(
    "/equivalents/{code}/integrity-hold/clear",
    response_model=EquivalentSchema,
    responses={
        404: {"model": ErrorEnvelope, "description": "Equivalent not found"},
        409: {
            "model": IntegrityHoldClearRefusal,
            "description": "Not held, or no PASSED reconciliation result later than the hold's, or the re-verification is not PASSED",
        },
    },
)
async def admin_clear_equivalent_integrity_hold(
    # The canon's `EquivalentCode`, stated on the generated side too, so this new operation enters no
    # parameter-drift ledger. A malformed code is a 422 (declared), not a lookup.
    code: Annotated[str, PathParam(pattern=r"^[A-Z0-9_]{1,16}$")],
    body: AdminEquivalentIntegrityHoldClearRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> EquivalentSchema:
    """Programme 015 step 5c (`T1546`): lift an integrity hold, explicitly, with audit.

    The predicate and its row locks are the core's (`app/core/equivalents.py::clear_integrity_hold`, 032 A-6): the
    hold is set by the core and lifted by it. A refusal leaves nothing behind - the frame rolls back, so the row
    locks end with the refusal, not when the request's session is torn down after the response was sent."""

    async with audited(db, request=request, action="admin.equivalents.integrity_hold.clear",
                       object_type="equivalent", object_id=code, reason=body.reason) as audit:
        eq, hold_result_id, cleared_on = await equivalents_core.clear_integrity_hold(db, code)
        result = EquivalentSchema.model_validate(eq)
        audit.before_state = {"integrity_hold_result_id": str(hold_result_id)}
        audit.after_state = {
            "integrity_hold_result_id": None,
            "cleared_on_reconciliation_result_id": str(cleared_on),
        }
    return result


@router.get("/equivalents/{code}/usage", response_model=AdminEquivalentUsageResponse, responses=_CODE_CANNOT_EXIST)
async def admin_equivalent_usage(
    code: str,
    db: AsyncSession = Depends(deps.get_db),
) -> AdminEquivalentUsageResponse:
    normalized = equivalents_core.canonical_code(code)

    eq = (
        await db.execute(select(EquivalentModel).where(EquivalentModel.code == normalized))
    ).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {normalized} not found")

    counts = await equivalents_core.equivalent_usage_counts(db, equivalent_id=eq.id)
    return AdminEquivalentUsageResponse(code=eq.code, **counts)


@router.delete(
    "/equivalents/{code}",
    response_model=AdminDeleteResponse,
    responses={
        **_CODE_CANNOT_EXIST,
        409: {
            "model": ErrorEnvelope,
            "description": "Active, in use, or named by rows it must not outlive; nothing deleted",
        }
    },
)
async def admin_delete_equivalent(
    code: str,
    body: AdminEquivalentDeleteRequest,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
) -> AdminDeleteResponse:
    normalized = equivalents_core.canonical_code(code)
    # The lock, the refusals (active, in use, referenced by rows it must not outlive) and the delete are the core's.
    async with audited(db, request=request, action="admin.equivalents.delete", object_type="equivalent",
                       object_id=normalized, reason=body.reason) as audit:
        audit.before_state = await equivalents_core.delete_equivalent(db, normalized)
    return AdminDeleteResponse(deleted=normalized)


@router.get("/migrations", response_model=AdminMigrationsStatus)
async def migrations_status() -> AdminMigrationsStatus:
    """The database's Alembic revision against the repository's head.

    032 A-1: the application's engine is ASYNC, and `engine.sync_engine.connect()` from a coroutine raises
    `MissingGreenlet` every time - which the old `except Exception` turned into "not up to date" on every database.
    The revision is read on an async connection through `run_sync` now, and a failure is LOGGED with its traceback
    before the degraded answer (both revisions `null`, `is_up_to_date: false`) - never swallowed silently."""

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

        async with engine.connect() as conn:
            current = await conn.run_sync(
                lambda sync_conn: MigrationContext.configure(sync_conn).get_current_revision()
            )

        return AdminMigrationsStatus(
            current_revision=current,
            head_revision=head,
            is_up_to_date=(current == head and current is not None),
        )
    except Exception:
        logger.error("admin.migrations.status_failed", exc_info=True)
        return AdminMigrationsStatus(current_revision=None, head_revision=None, is_up_to_date=False)


@router.get("/trustlines", response_model=AdminTrustLinesListResponse)
async def admin_list_trustlines(
    equivalent: str | None = None,
    creditor: str | None = Query(None, description="Creditor PID (trustline 'from')"),
    debtor: str | None = Query(None, description="Debtor PID (trustline 'to')"),
    status: Literal["active", "closed"] | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminTrustLinesListResponse:
    # 029 F-029-5, matrix row 9: the Admin API keeps the stored scale of every amount (`trustline_schema`).
    page_stmt, count_stmt = trustline_page_statements(
        equivalent=equivalent,
        creditor=creditor,
        debtor=debtor,
        status=status,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    total = (await db.execute(count_stmt)).scalar_one()
    items = [trustline_schema(row) for row in (await db.execute(page_stmt)).all()]
    return AdminTrustLinesListResponse(items=items, page=page, per_page=per_page, total=int(total))


@router.get(
    "/graph/snapshot",
    response_model=AdminGraphSnapshotResponse,
    responses={400: {"model": ErrorEnvelope, "description": "`equivalent` is not a valid equivalent code"}},
)
async def admin_graph_snapshot(
    equivalent: str | None = Query(None, description="Optional equivalent code for net visualization"),
    include: str | None = Query(
        None,
        description="Optional extras to include (comma-separated): incidents,audit_log,transactions",
    ),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminGraphSnapshotResponse:
    """Return a GraphPage-compatible snapshot: the whole network, every line and every debt.

    The same read as the ego route with no scope and no filters (`app/core/admin/graph.py`).
    Guardrail: TrustLine direction in output is from→to = creditor→debtor.
    """

    if equivalent is not None:
        validate_equivalent_code(equivalent)

    return AdminGraphSnapshotResponse(**await load_graph(db, net_equivalent=equivalent, include=include))


@router.get(
    "/graph/ego",
    response_model=AdminGraphEgoResponse,
    responses={
        400: {"model": ErrorEnvelope, "description": "`pid` is blank, or `equivalent` is not a valid equivalent code"},
        404: {"model": ErrorEnvelope, "description": "No participant has this `pid`"},
    },
)
async def admin_graph_ego(
    pid: str = Query(..., description="Root participant PID"),
    depth: int = Query(1, ge=1, le=2, description="Neighborhood depth (1–2)"),
    equivalent: str | None = Query(None, description="Optional equivalent code filter"),
    status: list[Literal["active", "closed"]] | None = Query(
        None, description="Optional trustline statuses filter (repeatable)"
    ),
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

    root = (await db.execute(select(Participant).where(Participant.pid == root_pid))).scalar_one_or_none()
    if not root:
        raise NotFoundException("Participant not found")

    scope_ids = await ego_participant_ids(db, root.id, depth=depth, equivalent=equivalent, statuses=status)
    data = await load_graph(
        db,
        net_equivalent=equivalent,
        include=include,
        scope_ids=scope_ids,
        line_equivalent=equivalent,
        statuses=status,
    )
    return AdminGraphEgoResponse(root_pid=root_pid, **data)



@router.get(
    "/participants/{pid}/metrics",
    response_model=AdminParticipantMetricsResponse,
    responses={
        400: {"model": ErrorEnvelope, "description": "`equivalent` is not a valid equivalent code"},
        404: {"model": ErrorEnvelope, "description": "No participant has this `pid`, or no equivalent has this code"},
    },
)
async def admin_participant_metrics(
    pid: str,
    equivalent: str | None = Query(default=None),
    db: AsyncSession = Depends(deps.get_db),
) -> AdminParticipantMetricsResponse:
    """The participant's balance rows per equivalent - the graph drawer's «Баланс» table (032 S5, F-1).

    Narrowed by the owner's decision of 2026-10-07: the rank, distribution, concentration, counterparties,
    capacity and activity analytics were removed.
    """

    return await compute_participant_metrics(db, pid=pid, equivalent=equivalent)
