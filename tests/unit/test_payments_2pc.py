import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.payments.engine import PaymentEngine
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
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


@pytest.mark.asyncio
async def test_commit_updates_transaction_updated_at(db_session):
    """Regression test for P1.3: Core UPDATEs must bump Transaction.updated_at.

    Without explicit `updated_at=func.now()` in the Core UPDATE, SQLite/Postgres may
    keep `updated_at` unchanged, breaking committed_at diagnostics.
    """

    alice_id = uuid.uuid4()
    bob_id = uuid.uuid4()
    eq_id = uuid.uuid4()

    alice = Participant(
        id=alice_id,
        pid="ALICE",
        display_name="Alice",
        public_key="a" * 64,
    )
    bob = Participant(
        id=bob_id,
        pid="BOB",
        display_name="Bob",
        public_key="b" * 64,
    )
    eq = Equivalent(id=eq_id, code="USD", description="USD", precision=2)
    db_session.add_all([alice, bob, eq])

    # Bob must trust Alice for Alice -> Bob payments (trustline from creditor to debtor).
    tl = TrustLine(
        id=uuid.uuid4(),
        from_participant_id=bob_id,
        to_participant_id=alice_id,
        equivalent_id=eq_id,
        limit=Decimal("100.00"),
        status="active",
    )
    db_session.add(tl)

    tx_id = str(uuid.uuid4())
    updated_at_before = datetime(2000, 1, 1, tzinfo=timezone.utc)
    tx = Transaction(
        id=uuid.uuid4(),
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=alice_id,
        payload={
            "from": alice.pid,
            "to": bob.pid,
            "amount": "1.00",
            "equivalent": eq.code,
            "path": [alice.pid, bob.pid],
        },
        state="PREPARED",
        created_at=updated_at_before,
        updated_at=updated_at_before,
    )
    db_session.add(tx)

    lock = PrepareLock(
        tx_id=tx_id,
        participant_id=alice_id,
        effects={
            "flows": [
                {
                    "from": str(alice_id),
                    "to": str(bob_id),
                    "amount": "1.00",
                    "equivalent": str(eq_id),
                }
            ]
        },
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    db_session.add(lock)
    await db_session.commit()

    engine = PaymentEngine(db_session)
    assert await engine.commit(tx_id) is True

    await db_session.refresh(tx)
    assert tx.state == "COMMITTED"
    assert tx.updated_at is not None

    # SQLite may return naive datetimes even for timezone=True columns.
    before_cmp = updated_at_before
    after_cmp = tx.updated_at
    if before_cmp.tzinfo is None:
        before_cmp = before_cmp.replace(tzinfo=timezone.utc)
    if after_cmp.tzinfo is None:
        after_cmp = after_cmp.replace(tzinfo=timezone.utc)

    assert after_cmp > before_cmp
