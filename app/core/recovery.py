from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.payments.engine import PaymentEngine
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction

logger = logging.getLogger(__name__)

_ACTIVE_TX_STATES: set[str] = {
    "NEW",
    "ROUTED",
    "PREPARE_IN_PROGRESS",
    "PREPARED",
    "PROPOSED",
    "WAITING",
}


async def cleanup_expired_prepare_locks(session: AsyncSession) -> int:
    result = await session.execute(
        delete(PrepareLock).where(PrepareLock.expires_at <= func.now())
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def abort_stale_payment_transactions(session: AsyncSession) -> int:
    timeout_seconds = int(getattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120) or 120)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)

    tx_ids = (
        await session.execute(
            select(Transaction.tx_id)
            .where(
                Transaction.type == "PAYMENT",
                Transaction.state.in_(_ACTIVE_TX_STATES),
                Transaction.updated_at < cutoff,
            )
            .order_by(Transaction.updated_at.asc())
        )
    ).scalars().all()

    if not tx_ids:
        return 0

    engine = PaymentEngine(session)
    aborted = 0
    for tx_id in tx_ids:
        try:
            await engine.abort(tx_id, reason="Recovered stale payment transaction")
            aborted += 1
        except Exception:
            logger.exception("recovery.abort_failed tx_id=%s", tx_id)

    return aborted


async def run_recovery_once(session: AsyncSession) -> None:
    deleted = 0
    aborted = 0
    try:
        deleted = await cleanup_expired_prepare_locks(session)
    except Exception:
        logger.exception("recovery.cleanup_expired_prepare_locks_failed")

    try:
        aborted = await abort_stale_payment_transactions(session)
    except Exception:
        logger.exception("recovery.abort_stale_payment_transactions_failed")

    if deleted or aborted:
        logger.info("recovery.done expired_locks_deleted=%s stale_payments_aborted=%s", deleted, aborted)


async def recovery_loop(*, session_factory, stop_event: asyncio.Event) -> None:
    interval = int(getattr(settings, "RECOVERY_INTERVAL_SECONDS", 60) or 60)

    # Run once at startup.
    try:
        async with session_factory() as session:
            await run_recovery_once(session)
    except Exception:
        logger.exception("recovery.startup_failed")

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        try:
            async with session_factory() as session:
                await run_recovery_once(session)
        except Exception:
            logger.exception("recovery.periodic_failed")
