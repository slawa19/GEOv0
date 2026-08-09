import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, func

from app.core.payments.engine import PaymentEngine
from app.core.recovery import (
    abort_stale_payment_transactions,
    cleanup_expired_prepare_locks,
    run_recovery_once,
)
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction


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

    deleted = await cleanup_expired_prepare_locks(db_session)
    assert deleted == 1

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

    aborted = await abort_stale_payment_transactions(db_session)
    assert aborted == 1

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

    assert await run_recovery_once(db_session) is True

    await db_session.refresh(successful_tx)
    await db_session.refresh(failed_tx)
    assert successful_tx.state == "ABORTED"
    assert failed_tx.state == "PREPARED"
    remaining_locks = (
        await db_session.execute(select(func.count()).select_from(PrepareLock))
    ).scalar_one()
    assert remaining_locks == 0
    assert "expired_locks_deleted=2 stale_payments_aborted=0" in caplog.text
    assert "transactions_aborted=1 abort_failures=1" in caplog.text


@pytest.mark.asyncio
async def test_recovery_iteration_preserves_stale_abort_progress_when_one_abort_fails(
    db_session,
    monkeypatch,
    caplog,
):
    successful_tx_id = "TX_STALE_SUCCESS"
    failed_tx_id = "TX_STALE_FAILURE"
    stale_at = datetime.now(timezone.utc) - timedelta(hours=1)
    successful_tx = _payment_transaction(successful_tx_id, updated_at=stale_at)
    failed_tx = _payment_transaction(failed_tx_id, updated_at=stale_at)
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

    async def abort_with_one_failure(self, tx_id, *args, **kwargs):
        if tx_id == failed_tx_id:
            raise RuntimeError("simulated item abort failure")
        return await original_abort(self, tx_id, *args, **kwargs)

    monkeypatch.setattr(PaymentEngine, "abort", abort_with_one_failure)
    caplog.set_level("INFO", logger="app.core.recovery")

    assert await run_recovery_once(db_session) is True

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
    assert "expired_locks_deleted=0 stale_payments_aborted=1" in caplog.text
    assert "transactions_aborted=1 abort_failures=1" in caplog.text
