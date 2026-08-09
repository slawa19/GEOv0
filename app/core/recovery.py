from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ExpiredLockCleanupResult:
    expired_lock_ids_resolved: int = 0
    transactions_aborted: int = 0
    terminal_transactions_seen: int = 0
    item_failures: int = 0

    @property
    def succeeded(self) -> bool:
        return self.item_failures == 0


@dataclass(frozen=True)
class StalePaymentAbortResult:
    transactions_aborted: int = 0
    terminal_transactions_seen: int = 0
    abort_failures: int = 0

    @property
    def succeeded(self) -> bool:
        return self.abort_failures == 0


async def _rollback_failed_recovery_item(
    session: AsyncSession, *, operation: str, tx_id: str
) -> None:
    try:
        await session.rollback()
    except Exception:
        logger.exception("recovery.%s_rollback_failed tx_id=%s", operation, tx_id)
        raise


def _classify_abort_outcome(outcome: object) -> tuple[int, int]:
    if outcome == "success":
        return 1, 0
    if outcome in {"already_aborted", "already_committed"}:
        return 0, 1
    raise RuntimeError(f"unexpected payment abort outcome: {outcome!r}")


async def cleanup_expired_prepare_locks(
    session: AsyncSession,
) -> ExpiredLockCleanupResult:
    try:
        # Best-effort metrics (avoid new metric names if registry isn't available).
        RECOVERY_EVENTS_TOTAL.labels(event="cleanup_expired_prepare_locks", result="start").inc()
    except Exception:
        pass

    expired_lock_rows = (
        await session.execute(
            select(PrepareLock.id, PrepareLock.tx_id)
            .where(PrepareLock.expires_at <= func.now())
            .order_by(PrepareLock.tx_id.asc(), PrepareLock.id.asc())
        )
    ).all()

    if not expired_lock_rows:
        try:
            RECOVERY_EVENTS_TOTAL.labels(event="cleanup_expired_prepare_locks", result="noop").inc()
        except Exception:
            pass
        return ExpiredLockCleanupResult()

    expired_lock_ids_by_tx: dict[str, list[object]] = {}
    for lock_id, tx_id in expired_lock_rows:
        expired_lock_ids_by_tx.setdefault(str(tx_id), []).append(lock_id)

    engine = PaymentEngine(session)
    expired_lock_ids_resolved = 0
    transactions_aborted = 0
    terminal_transactions_seen = 0
    item_failures = 0
    for tx_id, expired_lock_ids in expired_lock_ids_by_tx.items():
        try:
            outcome = await engine.abort(
                tx_id,
                reason="Prepare lock expired",
                error_code=ErrorCode.E007,
                return_outcome=True,
            )
            newly_aborted, terminal_seen = _classify_abort_outcome(outcome)
        except Exception:
            item_failures += 1
            logger.exception("recovery.abort_expired_prepare_lock_tx_failed tx_id=%s", tx_id)
            await _rollback_failed_recovery_item(
                session,
                operation="abort_expired_prepare_lock_tx",
                tx_id=tx_id,
            )
            continue

        # PaymentEngine removes locks durably for a new/already-aborted outcome.
        # An already-committed transaction returns before that cleanup, so delete
        # only the exact expired IDs observed for this terminal transaction.
        transactions_aborted += newly_aborted
        terminal_transactions_seen += terminal_seen
        if outcome == "already_committed":
            try:
                await session.execute(
                    delete(PrepareLock).where(PrepareLock.id.in_(expired_lock_ids))
                )
                await session.commit()
            except Exception:
                item_failures += 1
                logger.exception(
                    "recovery.cleanup_terminal_prepare_lock_tx_failed tx_id=%s",
                    tx_id,
                )
                await _rollback_failed_recovery_item(
                    session,
                    operation="cleanup_terminal_prepare_lock_tx",
                    tx_id=tx_id,
                )
                continue
        expired_lock_ids_resolved += len(expired_lock_ids)

    try:
        RECOVERY_EVENTS_TOTAL.labels(
            event="cleanup_expired_prepare_locks",
            result="partial_error" if item_failures else "success",
        ).inc()
    except Exception:
        pass

    if item_failures:
        logger.warning(
            "recovery.cleanup_expired_prepare_locks_partial "
            "expired_lock_ids_resolved=%s transactions_aborted=%s "
            "terminal_transactions_seen=%s item_failures=%s",
            expired_lock_ids_resolved,
            transactions_aborted,
            terminal_transactions_seen,
            item_failures,
        )

    return ExpiredLockCleanupResult(
        expired_lock_ids_resolved=expired_lock_ids_resolved,
        transactions_aborted=transactions_aborted,
        terminal_transactions_seen=terminal_transactions_seen,
        item_failures=item_failures,
    )


async def abort_stale_payment_transactions(
    session: AsyncSession,
) -> StalePaymentAbortResult:
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
        return StalePaymentAbortResult()

    engine = PaymentEngine(session)
    aborted = 0
    terminal_transactions_seen = 0
    abort_failures = 0
    for tx_id in tx_ids:
        try:
            outcome = await engine.abort(
                tx_id,
                reason="Recovered stale payment transaction",
                error_code=ErrorCode.E007,
                return_outcome=True,
            )
            newly_aborted, terminal_seen = _classify_abort_outcome(outcome)
            aborted += newly_aborted
            terminal_transactions_seen += terminal_seen
        except Exception:
            abort_failures += 1
            logger.exception("recovery.abort_failed tx_id=%s", tx_id)
            await _rollback_failed_recovery_item(
                session,
                operation="abort_stale_payment_transaction",
                tx_id=tx_id,
            )

    try:
        RECOVERY_EVENTS_TOTAL.labels(
            event="abort_stale_payment_transactions",
            result="partial_error" if abort_failures else "success",
        ).inc()
    except Exception:
        pass

    if abort_failures:
        logger.warning(
            "recovery.abort_stale_payment_transactions_partial "
            "transactions_aborted=%s terminal_transactions_seen=%s abort_failures=%s",
            aborted,
            terminal_transactions_seen,
            abort_failures,
        )

    return StalePaymentAbortResult(
        transactions_aborted=aborted,
        terminal_transactions_seen=terminal_transactions_seen,
        abort_failures=abort_failures,
    )


async def run_recovery_once(session: AsyncSession) -> bool:
    expired_result = ExpiredLockCleanupResult()
    stale_result = StalePaymentAbortResult()
    succeeded = True
    try:
        expired_result = await cleanup_expired_prepare_locks(session)
        succeeded = succeeded and expired_result.succeeded
    except Exception:
        succeeded = False
        logger.exception("recovery.cleanup_expired_prepare_locks_failed")

    try:
        stale_result = await abort_stale_payment_transactions(session)
        succeeded = succeeded and stale_result.succeeded
    except Exception:
        succeeded = False
        logger.exception("recovery.abort_stale_payment_transactions_failed")

    if expired_result.expired_lock_ids_resolved or stale_result.transactions_aborted:
        logger.info(
            "recovery.done expired_lock_ids_resolved=%s stale_payments_aborted=%s",
            expired_result.expired_lock_ids_resolved,
            stale_result.transactions_aborted,
        )
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
