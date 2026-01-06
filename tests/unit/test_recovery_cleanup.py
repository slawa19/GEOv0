import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, func

from app.core.recovery import abort_stale_payment_transactions, cleanup_expired_prepare_locks
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
