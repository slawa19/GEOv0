import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.payments.engine import PaymentEngine
from app.db.models.transaction import Transaction
from app.db.models.prepare_lock import PrepareLock


@pytest.mark.asyncio
async def test_commit_rejects_expired_locks(db_session):
    tx_id = str(uuid.uuid4())
    tx = Transaction(
        id=uuid.uuid4(),
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=uuid.uuid4(),
        payload={"from": "A", "to": "B", "amount": "1", "equivalent": "USD", "path": ["A", "B"]},
        state="PREPARED",
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

    engine = PaymentEngine(db_session)
    with pytest.raises(Exception):
        await engine.commit(tx_id)

    await db_session.refresh(tx)
    assert tx.state == "ABORTED"


@pytest.mark.asyncio
async def test_commit_is_idempotent_when_already_committed(db_session):
    tx_id = str(uuid.uuid4())
    tx = Transaction(
        id=uuid.uuid4(),
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=uuid.uuid4(),
        payload={"from": "A", "to": "B", "amount": "1", "equivalent": "USD", "path": ["A", "B"]},
        state="COMMITTED",
    )
    db_session.add(tx)
    await db_session.commit()

    engine = PaymentEngine(db_session)
    assert await engine.commit(tx_id) is True


@pytest.mark.asyncio
async def test_abort_is_noop_when_already_committed(db_session):
    tx_id = str(uuid.uuid4())
    tx = Transaction(
        id=uuid.uuid4(),
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=uuid.uuid4(),
        payload={"from": "A", "to": "B", "amount": "1", "equivalent": "USD", "path": ["A", "B"]},
        state="COMMITTED",
    )
    db_session.add(tx)
    await db_session.commit()

    engine = PaymentEngine(db_session)
    assert await engine.abort(tx_id, reason="should-not-abort") is True

    await db_session.refresh(tx)
    assert tx.state == "COMMITTED"
