from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.invariants import InvariantChecker
from app.db.models.audit_log import AuditLog, IntegrityAuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.schemas.integrity import (
    EquivalentIntegrityStatus,
    IntegrityAuditLogItem,
    IntegrityAuditLogResponse,
    IntegrityChecksumResponse,
    IntegrityStatusResponse,
    IntegrityVerifyRequest,
    IntegrityVerifyResponse,
    InvariantResult,
)
from app.utils.exceptions import IntegrityViolationException, NotFoundException
from app.utils.validation import validate_equivalent_code

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _latest_checkpoint(db: AsyncSession, *, equivalent_id) -> IntegrityCheckpoint | None:
    return (
        await db.execute(
            select(IntegrityCheckpoint)
            .where(IntegrityCheckpoint.equivalent_id == equivalent_id)
            .order_by(desc(IntegrityCheckpoint.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("/status", response_model=IntegrityStatusResponse)
async def get_integrity_status(
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
) -> IntegrityStatusResponse:
    checker = InvariantChecker(db)

    equivalents = (await db.execute(select(Equivalent))).scalars().all()
    equivalents_status: dict[str, EquivalentIntegrityStatus] = {}

    overall_status = "healthy"
    alerts: list[str] = []

    for eq in equivalents:
        status = "healthy"
        invariants: dict[str, InvariantResult] = {}

        checkpoint = await _latest_checkpoint(db, equivalent_id=eq.id)
        checksum = checkpoint.checksum if checkpoint else ""
        last_verified = checkpoint.created_at if checkpoint else None

        try:
            await checker.check_zero_sum(equivalent_id=eq.id)
            invariants["zero_sum"] = InvariantResult(passed=True, value="0")
        except IntegrityViolationException as exc:
            invariants["zero_sum"] = InvariantResult(passed=False, details=exc.details)
            status = "critical"
            overall_status = "critical"
            alerts.append(f"Zero-sum violation in {eq.code}")

        try:
            await checker.check_trust_limits(equivalent_id=eq.id)
            invariants["trust_limits"] = InvariantResult(passed=True, violations=0)
        except IntegrityViolationException as exc:
            violations = (exc.details or {}).get("violations") or []
            invariants["trust_limits"] = InvariantResult(
                passed=False,
                violations=len(violations),
                details=exc.details,
            )
            status = "critical"
            overall_status = "critical"
            alerts.append(f"Trust limit violations in {eq.code}: {len(violations)}")

        try:
            await checker.check_debt_symmetry(equivalent_id=eq.id)
            invariants["debt_symmetry"] = InvariantResult(passed=True, violations=0)
        except IntegrityViolationException as exc:
            violations = (exc.details or {}).get("violations") or []
            invariants["debt_symmetry"] = InvariantResult(
                passed=False,
                violations=len(violations),
                details=exc.details,
            )
            if status == "healthy":
                status = "warning"
            if overall_status == "healthy":
                overall_status = "warning"
            alerts.append(f"Debt symmetry violations in {eq.code}: {len(violations)}")

        equivalents_status[eq.code] = EquivalentIntegrityStatus(
            status=status,
            checksum=checksum,
            last_verified=last_verified,
            invariants=invariants,
        )

    return IntegrityStatusResponse(
        status=overall_status,
        last_check=_now(),
        equivalents=equivalents_status,
        alerts=alerts,
    )


@router.get("/checksum/{equivalent}", response_model=IntegrityChecksumResponse)
async def get_integrity_checksum(
    equivalent: str,
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
) -> IntegrityChecksumResponse:
    validate_equivalent_code(equivalent)

    eq = (await db.execute(select(Equivalent).where(Equivalent.code == equivalent))).scalar_one_or_none()
    if eq is None:
        raise NotFoundException(f"Equivalent {equivalent} not found")

    checkpoint = await _latest_checkpoint(db, equivalent_id=eq.id)
    if checkpoint is None:
        raise NotFoundException(f"Integrity checkpoint for {equivalent} not found")

    return IntegrityChecksumResponse(
        equivalent=equivalent,
        checksum=checkpoint.checksum,
        created_at=checkpoint.created_at,
        invariants_status=checkpoint.invariants_status or {},
    )


@router.post("/verify", response_model=IntegrityVerifyResponse)
async def verify_integrity(
    body: IntegrityVerifyRequest,
    db: AsyncSession = Depends(deps.get_db),
    current_participant=Depends(deps.get_current_participant),
) -> IntegrityVerifyResponse:
    checker = InvariantChecker(db)

    equivalents_query = select(Equivalent)
    if body.equivalent:
        validate_equivalent_code(body.equivalent)
        equivalents_query = equivalents_query.where(Equivalent.code == body.equivalent)

    equivalents = (await db.execute(equivalents_query)).scalars().all()
    if body.equivalent and not equivalents:
        raise NotFoundException(f"Equivalent {body.equivalent} not found")

    equivalents_status: dict[str, EquivalentIntegrityStatus] = {}
    overall_status = "healthy"
    alerts: list[str] = []

    checked_at = _now()

    for eq in equivalents:
        status = "healthy"
        invariants: dict[str, InvariantResult] = {}

        try:
            await checker.check_zero_sum(equivalent_id=eq.id)
            invariants["zero_sum"] = InvariantResult(passed=True, value="0")
        except IntegrityViolationException as exc:
            invariants["zero_sum"] = InvariantResult(passed=False, details=exc.details)
            status = "critical"
            overall_status = "critical"
            alerts.append(f"Zero-sum violation in {eq.code}")

        try:
            await checker.check_trust_limits(equivalent_id=eq.id)
            invariants["trust_limits"] = InvariantResult(passed=True, violations=0)
        except IntegrityViolationException as exc:
            violations = (exc.details or {}).get("violations") or []
            invariants["trust_limits"] = InvariantResult(
                passed=False,
                violations=len(violations),
                details=exc.details,
            )
            status = "critical"
            overall_status = "critical"
            alerts.append(f"Trust limit violations in {eq.code}: {len(violations)}")

        try:
            await checker.check_debt_symmetry(equivalent_id=eq.id)
            invariants["debt_symmetry"] = InvariantResult(passed=True, violations=0)
        except IntegrityViolationException as exc:
            violations = (exc.details or {}).get("violations") or []
            invariants["debt_symmetry"] = InvariantResult(
                passed=False,
                violations=len(violations),
                details=exc.details,
            )
            if status == "healthy":
                status = "warning"
            if overall_status == "healthy":
                overall_status = "warning"
            alerts.append(f"Debt symmetry violations in {eq.code}: {len(violations)}")

        checkpoint = await _latest_checkpoint(db, equivalent_id=eq.id)
        equivalents_status[eq.code] = EquivalentIntegrityStatus(
            status=status,
            checksum=checkpoint.checksum if checkpoint else "",
            last_verified=checkpoint.created_at if checkpoint else None,
            invariants=invariants,
        )

        # FIX-014: integrity audit trail entry (verify operation).
        try:
            computed = await compute_integrity_checkpoint_for_equivalent(db, equivalent_id=eq.id)
            checksum = computed.checksum
        except Exception:
            checksum = checkpoint.checksum if checkpoint else ""

        try:
            passed = status == "healthy"
            db.add(
                IntegrityAuditLog(
                    operation_type="INTEGRITY_VERIFY",
                    tx_id=None,
                    equivalent_code=eq.code,
                    state_checksum_before=checksum,
                    state_checksum_after=checksum,
                    affected_participants={},
                    invariants_checked={k: v.model_dump() for k, v in invariants.items()},
                    verification_passed=passed,
                    error_details=None
                    if passed
                    else {
                        "status": status,
                        "checked_at": checked_at.isoformat(),
                        "invariants": {k: v.model_dump() for k, v in invariants.items()},
                    },
                )
            )
        except Exception:
            pass

    await db.commit()

    return IntegrityVerifyResponse(
        status=overall_status,
        checked_at=checked_at,
        equivalents=equivalents_status,
        alerts=alerts,
    )


@router.get("/audit-log", response_model=IntegrityAuditLogResponse)
async def get_integrity_audit_log(
    page: int = 1,
    per_page: int = 20,
    db: AsyncSession = Depends(deps.get_db),
    _current_participant=Depends(deps.get_current_participant),
) -> IntegrityAuditLogResponse:
    page = max(1, int(page))
    per_page = max(1, min(200, int(per_page)))
    offset = (page - 1) * per_page

    stmt = (
        select(IntegrityAuditLog)
        .order_by(desc(IntegrityAuditLog.timestamp))
        .limit(per_page)
        .offset(offset)
    )

    rows = (await db.execute(stmt)).scalars().all()

    items = []
    for row in rows:
        action = (
            "integrity.verify"
            if row.operation_type == "INTEGRITY_VERIFY"
            else f"integrity.{str(row.operation_type).lower()}"
        )
        items.append(
            IntegrityAuditLogItem(
                timestamp=row.timestamp,
                actor_id=None,
                action=action,
                object_type="equivalent",
                object_id=row.equivalent_code,
                after_state={
                    "operation_type": row.operation_type,
                    "tx_id": row.tx_id,
                    "equivalent": row.equivalent_code,
                    "state_checksum_before": row.state_checksum_before,
                    "state_checksum_after": row.state_checksum_after,
                    "affected_participants": row.affected_participants,
                    "invariants_checked": row.invariants_checked,
                    "verification_passed": row.verification_passed,
                    "error_details": row.error_details,
                },
            )
        )

    return IntegrityAuditLogResponse(items=items)
