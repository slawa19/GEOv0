import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from app.core.payments.engine import PaymentEngine
from app.core.recovery import (
    abort_stale_payment_transactions,
    cleanup_expired_prepare_locks,
    run_recovery_once,
)
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction


@pytest.mark.asyncio
async def test_recovery_stops_before_stale_phase_when_session_cannot_rollback(
    monkeypatch,
    caplog,
):
    stale_phase_called = False

    class _BrokenSession:
        async def rollback(self):
            raise RuntimeError("rollback still unavailable")

    async def _cleanup_fails(_session):
        raise RuntimeError("cleanup failed")

    async def _stale_phase(_session):
        nonlocal stale_phase_called
        stale_phase_called = True
        return None

    monkeypatch.setattr(
        "app.core.recovery.cleanup_expired_prepare_locks",
        _cleanup_fails,
    )
    monkeypatch.setattr(
        "app.core.recovery.abort_stale_payment_transactions",
        _stale_phase,
    )
    caplog.set_level("ERROR", logger="app.core.recovery")

    assert await run_recovery_once(_BrokenSession()) is False
    assert stale_phase_called is False
    assert "cleanup_expired_prepare_locks_failed" in caplog.text
    assert "cleanup_expired_prepare_locks_session_unusable" in caplog.text


@pytest.mark.asyncio
async def test_cleanup_expired_prepare_locks_aborts_related_tx_and_deletes_locks(db_session):
    tx_id = str(uuid.uuid4())

    tx = Transaction(
        id=uuid.uuid4(),
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=uuid.uuid4(),
        payload={
            "from": "A",
            "to": "B",
            "amount": "1",
            "equivalent": "USD",
            "path": ["A", "B"],
        },
        state="PREPARED",
        error=None,
    )
    db_session.add(tx)

    lock = PrepareLock(
        tx_id=tx_id,
        participant_id=uuid.uuid4(),
        effects={
            "flows": [
                {
                    "from": str(uuid.uuid4()),
                    "to": str(uuid.uuid4()),
                    "amount": str(Decimal("1.00")),
                    "equivalent": str(uuid.uuid4()),
                }
            ]
        },
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db_session.add(lock)
    await db_session.commit()

    result = await cleanup_expired_prepare_locks(db_session)
    assert result.expired_lock_ids_resolved == 1
    assert result.transactions_aborted == 1
    assert result.terminal_transactions_seen == 0
    assert result.item_failures == 0

    # Locks deleted
    remaining_locks = (
        await db_session.execute(select(func.count()).select_from(PrepareLock).where(PrepareLock.tx_id == tx_id))
    ).scalar_one()
    assert remaining_locks == 0

    # Tx aborted
    await db_session.refresh(tx)
    assert tx.state == "ABORTED"
    assert (tx.error or {}).get("message") == "Prepare lock expired"


@pytest.mark.asyncio
async def test_abort_stale_payment_transactions_aborts_old_active_tx(db_session):
    tx_id = str(uuid.uuid4())

    stale_updated_at = datetime.now(timezone.utc) - timedelta(seconds=3600)
    tx = Transaction(
        id=uuid.uuid4(),
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=uuid.uuid4(),
        payload={
            "from": "A",
            "to": "B",
            "amount": "1",
            "equivalent": "USD",
            "path": ["A", "B"],
        },
        state="PREPARED",
        error=None,
        updated_at=stale_updated_at,
    )
    db_session.add(tx)

    lock = PrepareLock(
        tx_id=tx_id,
        participant_id=uuid.uuid4(),
        effects={},
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=3600),
    )
    db_session.add(lock)
    await db_session.commit()

    result = await abort_stale_payment_transactions(db_session)
    assert result.transactions_aborted == 1
    assert result.terminal_transactions_seen == 0
    assert result.abort_failures == 0

    await db_session.refresh(tx)
    assert tx.state == "ABORTED"
    assert (tx.error or {}).get("message") == "Recovered stale payment transaction"

    remaining_locks = (
        await db_session.execute(select(func.count()).select_from(PrepareLock).where(PrepareLock.tx_id == tx_id))
    ).scalar_one()
    assert remaining_locks == 0


def _payment_transaction(
    tx_id: str, *, updated_at: datetime | None = None
) -> Transaction:
    values = {
        "id": uuid.uuid4(),
        "tx_id": tx_id,
        "type": "PAYMENT",
        "initiator_id": uuid.uuid4(),
        "payload": {
            "from": "A",
            "to": "B",
            "amount": "1",
            "equivalent": "USD",
            "path": ["A", "B"],
        },
        "state": "PREPARED",
        "error": None,
    }
    if updated_at is not None:
        values["updated_at"] = updated_at
    return Transaction(**values)


def _prepare_lock(tx_id: str, *, expires_at: datetime) -> PrepareLock:
    return PrepareLock(
        tx_id=tx_id,
        participant_id=uuid.uuid4(),
        effects={},
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_recovery_iteration_preserves_expired_lock_progress_when_one_abort_fails(
    db_session,
    monkeypatch,
    caplog,
):
    successful_tx_id = "TX_EXPIRED_SUCCESS"
    failed_tx_id = "TX_EXPIRED_FAILURE"
    successful_tx = _payment_transaction(successful_tx_id)
    failed_tx = _payment_transaction(failed_tx_id)
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.add_all(
        [
            successful_tx,
            failed_tx,
            _prepare_lock(successful_tx_id, expires_at=expired_at),
            _prepare_lock(failed_tx_id, expires_at=expired_at),
        ]
    )
    await db_session.commit()

    original_abort = PaymentEngine.abort

    async def abort_with_one_failure(self, tx_id, *args, **kwargs):
        if tx_id == failed_tx_id:
            raise RuntimeError("simulated item abort failure")
        return await original_abort(self, tx_id, *args, **kwargs)

    monkeypatch.setattr(PaymentEngine, "abort", abort_with_one_failure)
    caplog.set_level("INFO", logger="app.core.recovery")

    assert await run_recovery_once(db_session) is False

    await db_session.refresh(successful_tx)
    await db_session.refresh(failed_tx)
    assert successful_tx.state == "ABORTED"
    assert failed_tx.state == "PREPARED"
    remaining_tx_ids = set(
        (await db_session.execute(select(PrepareLock.tx_id))).scalars().all()
    )
    assert remaining_tx_ids == {failed_tx_id}
    assert "expired_lock_ids_resolved=1 stale_payments_aborted=0" in caplog.text
    assert (
        "expired_lock_ids_resolved=1 transactions_aborted=1 "
        "terminal_transactions_seen=0 item_failures=1"
    ) in caplog.text

    monkeypatch.setattr(PaymentEngine, "abort", original_abort)
    assert await run_recovery_once(db_session) is True
    await db_session.refresh(failed_tx)
    assert failed_tx.state == "ABORTED"
    remaining_locks = (
        await db_session.execute(select(func.count()).select_from(PrepareLock))
    ).scalar_one()
    assert remaining_locks == 0


@pytest.mark.asyncio
async def test_recovery_iteration_preserves_stale_abort_progress_when_one_abort_fails(
    db_session,
    monkeypatch,
    caplog,
):
    failed_tx_id = "TX_STALE_FAILURE"
    successful_tx_id = "TX_STALE_SUCCESS"
    failed_tx = _payment_transaction(
        failed_tx_id,
        updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    successful_tx = _payment_transaction(
        successful_tx_id,
        updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    unexpired_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.add_all(
        [
            successful_tx,
            failed_tx,
            _prepare_lock(successful_tx_id, expires_at=unexpired_at),
            _prepare_lock(failed_tx_id, expires_at=unexpired_at),
        ]
    )
    await db_session.commit()

    original_abort = PaymentEngine.abort

    rollback_calls = 0
    original_rollback = db_session.rollback

    async def tracked_rollback():
        nonlocal rollback_calls
        rollback_calls += 1
        await original_rollback()

    async def abort_with_one_failure(self, tx_id, *args, **kwargs):
        if tx_id == failed_tx_id:
            self.session.add(_payment_transaction(failed_tx_id))
            await self.session.flush()
        return await original_abort(self, tx_id, *args, **kwargs)

    monkeypatch.setattr(db_session, "rollback", tracked_rollback)
    monkeypatch.setattr(PaymentEngine, "abort", abort_with_one_failure)
    caplog.set_level("INFO", logger="app.core.recovery")

    assert await run_recovery_once(db_session) is False
    assert rollback_calls == 1

    await db_session.refresh(successful_tx)
    await db_session.refresh(failed_tx)
    assert successful_tx.state == "ABORTED"
    assert failed_tx.state == "PREPARED"
    remaining_tx_ids = set(
        (
            await db_session.execute(
                select(PrepareLock.tx_id).where(
                    PrepareLock.tx_id.in_([successful_tx_id, failed_tx_id])
                )
            )
        )
        .scalars()
        .all()
    )
    assert remaining_tx_ids == {failed_tx_id}
    assert "expired_lock_ids_resolved=0 stale_payments_aborted=1" in caplog.text
    assert (
        "transactions_aborted=1 terminal_transactions_seen=0 abort_failures=1"
    ) in caplog.text

    monkeypatch.setattr(PaymentEngine, "abort", original_abort)
    assert await run_recovery_once(db_session) is True
    await db_session.refresh(failed_tx)
    assert failed_tx.state == "ABORTED"


@pytest.mark.asyncio
async def test_stale_recovery_counts_only_new_abort_outcomes(db_session, monkeypatch):
    first_tx_id = "TX_STALE_OUTCOME_SUCCESS"
    terminal_tx_id = "TX_STALE_OUTCOME_TERMINAL"
    stale_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.add_all(
        [
            _payment_transaction(first_tx_id, updated_at=stale_at),
            _payment_transaction(terminal_tx_id, updated_at=stale_at),
        ]
    )
    await db_session.commit()

    async def abort_with_outcomes(self, tx_id, *args, **kwargs):
        assert kwargs["return_outcome"] is True
        if tx_id == first_tx_id:
            return "success"
        if tx_id == terminal_tx_id:
            return "already_committed"
        raise AssertionError(f"unexpected tx_id {tx_id}")

    monkeypatch.setattr(PaymentEngine, "abort", abort_with_outcomes)

    result = await abort_stale_payment_transactions(db_session)

    assert result.transactions_aborted == 1
    assert result.terminal_transactions_seen == 1
    assert result.abort_failures == 0


@pytest.mark.asyncio
async def test_expired_lock_cleanup_counts_observed_resolved_ids_for_terminal_outcomes(
    db_session,
    monkeypatch,
):
    success_tx_id = "TX_EXPIRED_OUTCOME_SUCCESS"
    terminal_tx_id = "TX_EXPIRED_OUTCOME_TERMINAL"
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.add_all(
        [
            _payment_transaction(success_tx_id),
            _payment_transaction(terminal_tx_id),
            _prepare_lock(success_tx_id, expires_at=expired_at),
            _prepare_lock(terminal_tx_id, expires_at=expired_at),
        ]
    )
    await db_session.commit()

    real_execute = db_session.execute
    prepare_lock_delete_calls = 0

    async def count_prepare_lock_deletes(statement, *args, **kwargs):
        nonlocal prepare_lock_delete_calls
        if getattr(statement, "is_delete", False) and getattr(
            getattr(statement, "table", None), "name", None
        ) == PrepareLock.__tablename__:
            prepare_lock_delete_calls += 1
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", count_prepare_lock_deletes)

    async def abort_with_outcomes(self, tx_id, *args, **kwargs):
        assert kwargs["return_outcome"] is True
        if tx_id in {success_tx_id, terminal_tx_id}:
            await self.session.execute(
                delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
            )
            await self.session.commit()
            return "success" if tx_id == success_tx_id else "already_committed"
        raise AssertionError(f"unexpected tx_id {tx_id}")

    monkeypatch.setattr(PaymentEngine, "abort", abort_with_outcomes)

    result = await cleanup_expired_prepare_locks(db_session)

    assert result.expired_lock_ids_resolved == 2
    assert result.transactions_aborted == 1
    assert result.terminal_transactions_seen == 1
    assert result.item_failures == 0
    assert prepare_lock_delete_calls == 2
    remaining_locks = (
        await db_session.execute(select(func.count()).select_from(PrepareLock))
    ).scalar_one()
    assert remaining_locks == 0


@pytest.mark.asyncio
async def test_recovery_item_rollback_failure_escalates_the_batch(
    db_session,
    monkeypatch,
):
    tx_id = "TX_STALE_ROLLBACK_FAILURE"
    db_session.add(
        _payment_transaction(
            tx_id,
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    await db_session.commit()

    async def fail_abort(self, candidate_tx_id, *args, **kwargs):
        assert candidate_tx_id == tx_id
        raise RuntimeError("simulated abort failure")

    async def fail_rollback():
        raise RuntimeError("simulated rollback failure")

    monkeypatch.setattr(PaymentEngine, "abort", fail_abort)
    monkeypatch.setattr(db_session, "rollback", fail_rollback)

    with pytest.raises(RuntimeError, match="simulated rollback failure"):
        await abort_stale_payment_transactions(db_session)
