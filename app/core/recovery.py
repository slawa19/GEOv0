from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.payments.engine import PaymentEngine
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.utils.error_codes import ErrorCode
from app.utils.metrics import RECOVERY_EVENTS_TOTAL

logger = logging.getLogger(__name__)

RecoveryIterationObserver = Callable[[str, BaseException | None], None]

_ACTIVE_TX_STATES: set[str] = {
    "NEW",
    "ROUTED",
    "PREPARE_IN_PROGRESS",
    "PREPARED",
    "PROPOSED",
    "WAITING",
}


async def cleanup_expired_prepare_locks(session: AsyncSession) -> int:
    try:
        # Best-effort metrics (avoid new metric names if registry isn't available).
        RECOVERY_EVENTS_TOTAL.labels(event="cleanup_expired_prepare_locks", result="start").inc()
    except Exception:
        pass

    # Count first to avoid relying on DBAPI rowcount semantics.
    expired_count = (
        await session.execute(
            select(func.count()).select_from(PrepareLock).where(PrepareLock.expires_at <= func.now())
        )
    ).scalar_one()

    if not expired_count:
        try:
            RECOVERY_EVENTS_TOTAL.labels(event="cleanup_expired_prepare_locks", result="noop").inc()
        except Exception:
            pass
        return 0

    # Abort related transactions first to ensure we don't leave "active" tx without locks.
    tx_ids = (
        await session.execute(
            select(PrepareLock.tx_id)
            .where(PrepareLock.expires_at <= func.now())
            .distinct()
        )
    ).scalars().all()

    engine = PaymentEngine(session)
    abort_failures = 0
    for tx_id in tx_ids:
        try:
            await engine.abort(tx_id, reason="Prepare lock expired", error_code=ErrorCode.E007)
        except Exception:
            abort_failures += 1
            logger.exception("recovery.abort_expired_prepare_lock_tx_failed tx_id=%s", tx_id)

    # Best-effort cleanup for any remaining expired rows (e.g., if abort failed part-way).
    await session.execute(delete(PrepareLock).where(PrepareLock.expires_at <= func.now()))
    await session.commit()

    try:
        RECOVERY_EVENTS_TOTAL.labels(
            event="cleanup_expired_prepare_locks",
            result="partial_error" if abort_failures else "success",
        ).inc()
    except Exception:
        pass

    if abort_failures:
        raise RuntimeError(
            f"failed to abort {abort_failures} transaction(s) with expired prepare locks"
        )

    return int(expired_count)


async def abort_stale_payment_transactions(session: AsyncSession) -> int:
    timeout_seconds = int(getattr(settings, "PAYMENT_TX_STUCK_TIMEOUT_SECONDS", 120) or 120)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)

    try:
        RECOVERY_EVENTS_TOTAL.labels(event="abort_stale_payment_transactions", result="start").inc()
    except Exception:
        pass

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
        try:
            RECOVERY_EVENTS_TOTAL.labels(event="abort_stale_payment_transactions", result="noop").inc()
        except Exception:
            pass
        return 0

    engine = PaymentEngine(session)
    aborted = 0
    abort_failures = 0
    for tx_id in tx_ids:
        try:
            await engine.abort(
                tx_id,
                reason="Recovered stale payment transaction",
                error_code=ErrorCode.E007,
            )
            aborted += 1
        except Exception:
            abort_failures += 1
            logger.exception("recovery.abort_failed tx_id=%s", tx_id)

    try:
        RECOVERY_EVENTS_TOTAL.labels(
            event="abort_stale_payment_transactions",
            result="partial_error" if abort_failures else "success",
        ).inc()
    except Exception:
        pass

    if abort_failures:
        raise RuntimeError(f"failed to abort {abort_failures} stale payment transaction(s)")

    return aborted


async def run_recovery_once(session: AsyncSession) -> bool:
    deleted = 0
    aborted = 0
    succeeded = True
    try:
        deleted = await cleanup_expired_prepare_locks(session)
    except Exception:
        succeeded = False
        logger.exception("recovery.cleanup_expired_prepare_locks_failed")

    try:
        aborted = await abort_stale_payment_transactions(session)
    except Exception:
        succeeded = False
        logger.exception("recovery.abort_stale_payment_transactions_failed")

    if deleted or aborted:
        logger.info("recovery.done expired_locks_deleted=%s stale_payments_aborted=%s", deleted, aborted)
    return succeeded


async def _run_recovery_iteration(
    *,
    session_factory,
    reason: str,
    on_iteration: RecoveryIterationObserver | None,
) -> None:
    error: BaseException | None = None
    try:
        async with session_factory() as session:
            if not await run_recovery_once(session):
                error = RuntimeError("recovery iteration did not complete successfully")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = exc
        logger.exception("recovery.%s_failed", reason)

    if on_iteration is not None:
        on_iteration(reason, error)


async def recovery_loop(
    *,
    session_factory,
    stop_event: asyncio.Event,
    on_iteration: RecoveryIterationObserver | None = None,
) -> None:
    interval = int(getattr(settings, "RECOVERY_INTERVAL_SECONDS", 60) or 60)

    # Run once at startup.
    await _run_recovery_iteration(
        session_factory=session_factory,
        reason="startup",
        on_iteration=on_iteration,
    )

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        await _run_recovery_iteration(
            session_factory=session_factory,
            reason="periodic",
            on_iteration=on_iteration,
        )
