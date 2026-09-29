from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.invariants import InvariantChecker
from app.core.ledger.reconciliation import FAILED, UNVERIFIABLE
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.reconciliation_tables import debt_reconciliation_results
from app.schemas.integrity import (
    EquivalentIntegrityStatus,
    InvariantOutcome,
    InvariantWithdrawn,
    IntegrityAuditLogItem,
    IntegrityAuditLogResponse,
    IntegrityChecksumResponse,
    IntegrityStatusResponse,
    IntegrityVerifyRequest,
    IntegrityVerifyResponse,
    InvariantResult,
    TrustLimitsResult,
)
from app.utils.exceptions import (
    IntegrityViolationException,
    NotFoundException,
)
from app.utils.validation import validate_equivalent_code

router = APIRouter()


async def _trust_limits(checker: InvariantChecker, equivalent_id) -> TrustLimitsResult:
    """026 `T2601`: only a structural violation fails; an over-limit debt is listed as allowed."""

    try:
        allowed = await checker.check_trust_limits(equivalent_id=equivalent_id)
    except IntegrityViolationException as exc:
        violations = (exc.details or {}).get("violations") or []
        allowed = getattr(exc, "over_limit_allowed", [])
        return TrustLimitsResult(
            passed=False, violations=len(violations), details=exc.details, over_limit_allowed=allowed
        )
    return TrustLimitsResult(passed=True, violations=0, over_limit_allowed=allowed)


def _unverified_names(invariants: dict[str, InvariantOutcome]) -> list[str]:
    """Names carrying no verdict, derived from the values rather than hard-coded.

    Hard-coding `["zero_sum"]` would keep saying "not verified" after 015 restores a real check.
    """
    return sorted(k for k, v in invariants.items() if isinstance(v, InvariantWithdrawn))


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


_SEVERITY = {"healthy": 0, "warning": 1, "critical": 2}


def _worse(a: str, b: str) -> str:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


async def _latest_reconciliation_results(db: AsyncSession) -> dict:
    """The stored latest result per equivalent, by the `is_latest` marker (one row each, partial unique
    index). ONE bounded read of stored rows: nothing is reconciled here."""

    columns = debt_reconciliation_results.c
    rows = (
        await db.execute(
            select(columns.equivalent_id, columns.id, columns.status, columns.detail).where(
                columns.is_latest.is_(True)
            )
        )
    ).all()
    return {row.equivalent_id: row for row in rows}


def _reconciliation_view(eq: Equivalent, latest) -> tuple[str, list[str]]:
    """What the integrity hold and the latest stored reconciliation result add to one equivalent's status.

    024 `T2412.1`, mapping decided by the Sh2 consultation (2026-09-29). The hold and the verdict are
    reported independently: a later PASSED does not cancel a hold (only an admin clears it), and a FAILED
    without a hold does not claim that money is refused. No row at all is a gap of the check - a verifier
    error leaves none - so it is a warning, never a pass and never a stored verdict.
    """

    severity = "healthy"
    alerts: list[str] = []
    hold_id = eq.integrity_hold_result_id
    if hold_id is not None:
        severity = "critical"
        alerts.append(
            f"Integrity hold on {eq.code}: money movements are refused until an admin clears it "
            f"(hold_result_id={hold_id})"
        )
    if latest is None:
        return _worse(severity, "warning"), alerts + [f"Debt reconciliation result missing for {eq.code}"]

    if latest.status == FAILED:
        severity = "critical"
        alerts.append(f"Debt reconciliation FAILED in {eq.code} (result_id={latest.id})")
    elif latest.status == UNVERIFIABLE:
        detail = latest.detail if isinstance(latest.detail, dict) else json.loads(latest.detail or "{}")
        missing = ",".join(str(item) for item in detail.get("missing_evidence") or [])
        severity = _worse(severity, "warning")
        alerts.append(
            f"Debt reconciliation UNVERIFIABLE in {eq.code} (result_id={latest.id}, missing_evidence={missing})"
        )
    elif hold_id is not None:
        alerts.append(
            f"Debt reconciliation PASSED in {eq.code} (result_id={latest.id}) after the integrity hold; "
            f"an admin may clear it"
        )
    return severity, alerts


@router.get("/status", response_model=IntegrityStatusResponse)
async def get_integrity_status(
    db: AsyncSession = Depends(deps.get_db),
    _actor=Depends(deps.require_participant_or_admin),
) -> IntegrityStatusResponse:
    checker = InvariantChecker(db)

    equivalents = (await db.execute(select(Equivalent))).scalars().all()
    latest_results = await _latest_reconciliation_results(db)
    equivalents_status: dict[str, EquivalentIntegrityStatus] = {}

    overall_status = "healthy"
    alerts: list[str] = []

    for eq in equivalents:
        status = "healthy"
        invariants: dict[str, InvariantOutcome] = {}

        checkpoint = await _latest_checkpoint(db, equivalent_id=eq.id)
        checksum = checkpoint.checksum if checkpoint else ""
        last_verified = checkpoint.created_at if checkpoint else None

        # zero-sum: WITHDRAWN by T1402 of programme 014. Not called, no verdict published.
        # `check_zero_sum` (removed, 024 `T2411`) telescoped to zero for any `Debt` rows, so could not fail on
        # corruption; `passed=True, value="0"` was a measurement of nothing. The key stays so the
        # response keeps naming every protocol invariant, and `unverified` below keeps the gap
        # visible in the summary rather than implied by a missing key.
        invariants["zero_sum"] = InvariantWithdrawn()

        invariants["trust_limits"] = trust = await _trust_limits(checker, eq.id)
        if not trust.passed:
            status = "critical"
            overall_status = "critical"
            alerts.append(f"Trust limit violations in {eq.code}: {trust.violations}")

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

        reconciliation_severity, reconciliation_alerts = _reconciliation_view(eq, latest_results.get(eq.id))
        status = _worse(status, reconciliation_severity)
        overall_status = _worse(overall_status, status)
        alerts.extend(reconciliation_alerts)

        equivalents_status[eq.code] = EquivalentIntegrityStatus(
            status=status,
            checksum=checksum,
            last_verified=last_verified,
            invariants=invariants,
            unverified=_unverified_names(invariants),
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
    _actor=Depends(deps.require_participant_or_admin),
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
    _actor=Depends(deps.require_participant_or_admin),
) -> IntegrityVerifyResponse:
    """Re-run the invariant checks (trust limits, debt symmetry) now and record an audit row.

    It does NOT run the debt reconciliation and does not report its hold or verdict (024 `T2412.1`,
    Sh2 consultation 2026-09-29): the reconciliation runs only in the scheduled integrity job, and its
    stored result and the hold are what `GET /integrity/status` reports.
    """

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
        invariants: dict[str, InvariantOutcome] = {}

        # zero-sum: WITHDRAWN by T1402 of programme 014. Not called, no verdict published.
        # `check_zero_sum` (removed, 024 `T2411`) telescoped to zero for any `Debt` rows, so could not fail on
        # corruption; `passed=True, value="0"` was a measurement of nothing. The key stays so the
        # response keeps naming every protocol invariant, and `unverified` below keeps the gap
        # visible in the summary rather than implied by a missing key.
        invariants["zero_sum"] = InvariantWithdrawn()

        invariants["trust_limits"] = trust = await _trust_limits(checker, eq.id)
        if not trust.passed:
            status = "critical"
            overall_status = "critical"
            alerts.append(f"Trust limit violations in {eq.code}: {trust.violations}")

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
            unverified=_unverified_names(invariants),
        )

        # FIX-014: integrity audit trail entry (verify operation).
        computed = await compute_integrity_checkpoint_for_equivalent(
            db,
            equivalent_id=eq.id,
        )
        checksum = computed.checksum

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
    _actor=Depends(deps.require_participant_or_admin),
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
