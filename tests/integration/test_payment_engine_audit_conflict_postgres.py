from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_audit_serialization_failure_retries_before_transaction_is_poisoned(
    db_session, monkeypatch
):
    """A real 40001 inside the audit block must not degrade into 25P02."""

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates a real SERIALIZABLE conflict schedule")

    from tests.conftest import TestingSessionLocal

    import app.core.payments.engine as engine_module
    from app.core.payments.engine import PaymentEngine
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine

    nonce = uuid.uuid4().hex[:10]
    equivalent_code = f"AUD{nonce}".upper()
    tx_id = str(uuid.uuid4())

    async with TestingSessionLocal() as setup:
        equivalent = Equivalent(
            code=equivalent_code,
            symbol=equivalent_code,
            description="initial",
            precision=2,
        )
        sender = Participant(
            pid=f"A_AUD_{nonce}",
            display_name="A",
            public_key=f"pk_A_{nonce}",
            type="person",
            status="active",
            profile={},
        )
        receiver = Participant(
            pid=f"B_AUD_{nonce}",
            display_name="B",
            public_key=f"pk_B_{nonce}",
            type="person",
            status="active",
            profile={},
        )
        setup.add_all([equivalent, sender, receiver])
        await setup.commit()
        await setup.refresh(equivalent)
        await setup.refresh(sender)
        await setup.refresh(receiver)

        setup.add(
            TrustLine(
                from_participant_id=receiver.id,
                to_participant_id=sender.id,
                equivalent_id=equivalent.id,
                limit=Decimal("100.00"),
                status="active",
            )
        )
        setup.add(
            Transaction(
                id=uuid.uuid4(),
                tx_id=tx_id,
                type="PAYMENT",
                initiator_id=sender.id,
                payload={
                    "from": sender.pid,
                    "to": receiver.pid,
                    "routes": [],
                },
                state="PREPARED",
            )
        )
        await setup.flush()
        setup.add(
            PrepareLock(
                tx_id=tx_id,
                participant_id=sender.id,
                effects={
                    "flows": [
                        {
                            "from": str(sender.id),
                            "to": str(receiver.id),
                            "amount": "7.00",
                            "equivalent": str(equivalent.id),
                        }
                    ]
                },
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await setup.commit()

    checkpoint_calls = 0

    async def conflicting_checkpoint(session, *, equivalent_id):
        nonlocal checkpoint_calls
        checkpoint_calls += 1

        # The first call captures the pre-payment checkpoint. On the second call,
        # establish the SERIALIZABLE snapshot, commit a concurrent row update, and
        # then update the same row from the payment transaction. PostgreSQL itself
        # raises 40001; no DBAPI exception is fabricated by the test.
        if checkpoint_calls == 2:
            await session.execute(
                select(Equivalent.description).where(Equivalent.id == equivalent_id)
            )
            async with TestingSessionLocal() as competitor:
                await competitor.execute(
                    update(Equivalent)
                    .where(Equivalent.id == equivalent_id)
                    .values(description="competitor")
                )
                await competitor.commit()
            await session.execute(
                update(Equivalent)
                .where(Equivalent.id == equivalent_id)
                .values(description="payment")
            )

        # Countercheck: after the database retry, a non-database diagnostics
        # failure remains best-effort and must not fail the payment.
        if checkpoint_calls == 3:
            raise ValueError("non-database audit diagnostics failure")

        return SimpleNamespace(
            checksum="",
            invariants_status={"passed": True, "checks": []},
        )

    monkeypatch.setattr(
        engine_module,
        "compute_integrity_checkpoint_for_equivalent",
        conflicting_checkpoint,
    )

    try:
        async with TestingSessionLocal() as session:
            await session.connection(
                execution_options={"isolation_level": "SERIALIZABLE"}
            )
            engine = PaymentEngine(session)
            engine._retry_attempts = 2
            engine._retry_base_delay_s = 0.0
            engine._retry_max_delay_s = 0.0

            try:
                committed = await engine.commit(tx_id)
            except DBAPIError as exc:
                # Before T401, the audit block swallows the real 40001 and the
                # following DELETE reports only the poisoned-session symptom.
                assert engine._get_pgcode(exc) == "25P02"
                raise

            assert committed is True
            state = (
                await session.execute(
                    select(Transaction.state).where(Transaction.tx_id == tx_id)
                )
            ).scalar_one()
            assert state == "COMMITTED"
            assert checkpoint_calls >= 4
    finally:
        async with TestingSessionLocal() as cleanup:
            await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id == tx_id))
            await cleanup.execute(delete(Transaction).where(Transaction.tx_id == tx_id))
            await cleanup.execute(delete(Debt).where(Debt.equivalent_id == equivalent.id))
            await cleanup.execute(
                delete(TrustLine).where(TrustLine.equivalent_id == equivalent.id)
            )
            await cleanup.execute(
                delete(Participant).where(
                    Participant.pid.in_([sender.pid, receiver.pid])
                )
            )
            await cleanup.execute(
                delete(Equivalent).where(Equivalent.id == equivalent.id)
            )
            await cleanup.commit()
