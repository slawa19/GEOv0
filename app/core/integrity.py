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
