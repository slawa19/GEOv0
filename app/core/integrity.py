from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.integrity_checkpoint import IntegrityCheckpoint
from app.db.models.trustline import TrustLine
from app.schemas.integrity import ZERO_SUM_WITHDRAWN

logger = logging.getLogger(__name__)


async def compute_integrity_checkpoint_for_equivalent(
    session: AsyncSession,
    *,
    equivalent_id,
) -> IntegrityCheckpoint:
    debts = (
        await session.execute(
            select(Debt.debtor_id, Debt.creditor_id, Debt.amount)
            .where(Debt.equivalent_id == equivalent_id)
            .order_by(Debt.debtor_id.asc(), Debt.creditor_id.asc())
        )
    ).all()

    trustlines = (
        await session.execute(
            select(TrustLine.from_participant_id, TrustLine.to_participant_id, TrustLine.limit, TrustLine.status)
            .where(TrustLine.equivalent_id == equivalent_id)
            # `id` is a tie-breaker, not decoration: since migration 019 a closed
            # incarnation may share (from, to) with the live one, and ordering by the pair
            # alone leaves their relative position -- and therefore the checksum -- up to
            # the planner.  A non-deterministic integrity checksum is worse than no
            # checksum: it turns a stable state into a false alarm.
            .order_by(
                TrustLine.from_participant_id.asc(),
                TrustLine.to_participant_id.asc(),
                TrustLine.id.asc(),
            )
        )
    ).all()

    sha = hashlib.sha256()
    debt_negative = 0
    for debtor_id, creditor_id, amount in debts:
        if amount is not None and amount < 0:
            debt_negative += 1
        sha.update(f"debt|{debtor_id}|{creditor_id}|{amount}\n".encode("utf-8"))

    for from_id, to_id, limit, status in trustlines:
        sha.update(f"trustline|{from_id}|{to_id}|{limit}|{status}\n".encode("utf-8"))

    invariants_status = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "debts_count": len(debts),
        "trustlines_count": len(trustlines),
        "debts_non_negative": debt_negative == 0,
        "debts_negative_count": debt_negative,
    }

    # FIX-010: protocol-aligned invariant checks recorded in the checkpoint.
    # Expected invariant violations are recorded for operators. An unavailable
    # checker is not a successful verification and must fail the owning UoW.
    from app.core.invariants import InvariantChecker
    from app.utils.exceptions import IntegrityViolationException

    checker = InvariantChecker(session)
    checks: dict[str, dict] = {}
    alerts: list[str] = []

    overall_status = "healthy"

    # zero-sum: WITHDRAWN by T1402 of programme 014, not evaluated.
    #
    # `check_zero_sum` sums the same `Debt` rows twice - grouped by creditor and grouped by
    # debtor - and returns the difference, so it telescopes to zero for any row set. It cannot
    # fail on data corruption, and the `passed: True` written here was a claim about integrity
    # that the call could not support. It is no longer called, and no longer contributes to
    # `overall_status`, to `alerts` or to `passed`: an unverified check must not be able to make
    # the summary healthier, and must not be able to make it worse either.
    #
    # Building a real zero-sum check is programme 015. The key stays, and says what it is.
    checks["zero_sum"] = dict(ZERO_SUM_WITHDRAWN)
    unverified = ["zero_sum"]

    # trust limits (critical)
    try:
        await checker.check_trust_limits(equivalent_id=equivalent_id)
        checks["trust_limits"] = {"passed": True, "violations": 0}
    except IntegrityViolationException as exc:
        violations = (exc.details or {}).get("violations") or []
        checks["trust_limits"] = {
            "passed": False,
            "violations": len(violations),
            "details": exc.details,
        }
        overall_status = "critical"
        alerts.append("trust_limits")

    # debt symmetry (warning)
    try:
        await checker.check_debt_symmetry(equivalent_id=equivalent_id)
        checks["debt_symmetry"] = {"passed": True, "violations": 0}
    except IntegrityViolationException as exc:
        violations = (exc.details or {}).get("violations") or []
        checks["debt_symmetry"] = {
            "passed": False,
            "violations": len(violations),
            "details": exc.details,
        }
        if overall_status == "healthy":
            overall_status = "warning"
        alerts.append("debt_symmetry")

    invariants_status["status"] = overall_status
    invariants_status["checks"] = checks
    invariants_status["alerts"] = alerts
    # Scoped to the checks that were actually evaluated. `unverified` is what keeps that scoping
    # visible to a reader instead of implied by absence.
    invariants_status["passed"] = overall_status == "healthy"
    invariants_status["unverified"] = list(unverified)

    return IntegrityCheckpoint(
        equivalent_id=equivalent_id,
        checksum=sha.hexdigest(),
        invariants_status=invariants_status,
    )


async def compute_and_store_integrity_checkpoints(session: AsyncSession) -> int:
    equivalents = (await session.execute(select(Equivalent.id))).scalars().all()
    if not equivalents:
        return 0

    try:
        created = 0
        for eq_id in equivalents:
            cp = await compute_integrity_checkpoint_for_equivalent(session, equivalent_id=eq_id)
            session.add(cp)
            created += 1
        await session.commit()
        return created
    except BaseException:
        await session.rollback()
        raise
