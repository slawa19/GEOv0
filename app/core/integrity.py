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
            .order_by(TrustLine.from_participant_id.asc(), TrustLine.to_participant_id.asc())
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
    # We record failures rather than raising, so operators can inspect history.
    try:
        from app.core.invariants import InvariantChecker
        from app.utils.exceptions import IntegrityViolationException

        checker = InvariantChecker(session)
        checks: dict[str, dict] = {}
        alerts: list[str] = []

        overall_status = "healthy"

        # zero-sum (critical)
        try:
            await checker.check_zero_sum(equivalent_id=equivalent_id)
            checks["zero_sum"] = {"passed": True}
        except IntegrityViolationException as exc:
            checks["zero_sum"] = {"passed": False, "details": exc.details}
            overall_status = "critical"
            alerts.append("zero_sum")

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
        invariants_status["passed"] = overall_status == "healthy"
    except Exception:
        # Best-effort: checkpointing should not fail hard due to optional checks.
        pass

    return IntegrityCheckpoint(
        equivalent_id=equivalent_id,
        checksum=sha.hexdigest(),
        invariants_status=invariants_status,
    )


async def compute_and_store_integrity_checkpoints(session: AsyncSession) -> int:
    equivalents = (await session.execute(select(Equivalent.id))).scalars().all()
    if not equivalents:
        return 0

    created = 0
    for eq_id in equivalents:
        try:
            cp = await compute_integrity_checkpoint_for_equivalent(session, equivalent_id=eq_id)
            session.add(cp)
            created += 1
        except Exception:
            logger.exception("integrity.checkpoint_failed equivalent_id=%s", eq_id)

    await session.commit()
    return created
