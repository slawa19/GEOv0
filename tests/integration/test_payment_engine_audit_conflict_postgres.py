from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from tests.debt_setup import purge_test_ledger


pytestmark = pytest.mark.postgres

#: How long the competitor may wait for its row. A healthy run commits it in milliseconds; a wait
#: this long means it is queued behind a lock the payment itself holds (T1544 retarget).
_COMPETITOR_TIMEOUT_S = 10.0


@pytest.mark.asyncio
async def test_audit_serialization_failure_retries_before_transaction_is_poisoned(
    db_session, monkeypatch, caplog
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
    competitor_timed_out = False
    sender_id = sender.id

    async def _competitor_updates_the_contended_row() -> None:
        async with TestingSessionLocal() as competitor:
            await competitor.execute(
                update(Participant)
                .where(Participant.id == sender_id)
                .values(display_name="competitor")
            )
            await competitor.commit()

    async def conflicting_checkpoint(session, *, equivalent_id):
        nonlocal checkpoint_calls, competitor_timed_out
        checkpoint_calls += 1

        # The first call captures the pre-payment checkpoint. On the second call,
        # establish the SERIALIZABLE snapshot, commit a concurrent row update, and
        # then update the same row from the payment transaction. PostgreSQL itself
        # raises 40001; no DBAPI exception is fabricated by the test.
        #
        # THE CONTENDED ROW IS THE SENDER'S PARTICIPANT ROW, not the equivalent - retargeted by
        # T1544, 2026-09-14. The payment commit now holds `FOR SHARE` on the equivalent row
        # (`PaymentEngine.refuse_inactive_equivalents`), so a competitor updating THAT row waits for
        # the payment while the payment waits here for the competitor: the gate hung, it did not
        # fail. The participant row is read and written by the payment transaction below and is not
        # row-locked against a non-key update, so the conflict is the same genuine 40001 at the same
        # point. The competitor's wait is BOUNDED: if a future change locks this row too, the test
        # goes red on `competitor_timed_out` instead of hanging the gate.
        if checkpoint_calls == 2:
            await session.execute(
                select(Participant.display_name).where(Participant.id == sender_id)
            )
            competitor_task = asyncio.create_task(_competitor_updates_the_contended_row())
            done, _pending = await asyncio.wait(
                {competitor_task}, timeout=_COMPETITOR_TIMEOUT_S
            )
            if not done:
                # Raised into the payment's best-effort audit block, which swallows it; the flag is
                # what the test asserts.
                competitor_timed_out = True
                competitor_task.cancel()
                await asyncio.wait({competitor_task}, timeout=5.0)
                raise AssertionError("the competitor waited on a lock the payment holds")
            competitor_task.result()
            await session.execute(
                update(Participant)
                .where(Participant.id == sender_id)
                .values(display_name="payment")
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
                with caplog.at_level(logging.WARNING):
                    committed = await engine.commit(tx_id)
            except DBAPIError as exc:
                # Before T401, the audit block swallows the real 40001 and the
                # following DELETE reports only the poisoned-session symptom.
                assert engine._get_pgcode(exc) == "25P02"
                raise

            assert not competitor_timed_out, (
                f"the competitor could not update its row within {_COMPETITOR_TIMEOUT_S} s: it is "
                "queued behind a lock the payment commit holds, so no serialization failure was "
                "produced and this test measured nothing"
            )
            # PREMISE: the retry was a genuine 40001, not a pass with no conflict at all.
            retries = [
                record.getMessage()
                for record in caplog.records
                if "event=payment.uow_retry op=commit" in record.getMessage()
            ]
            assert any("pgcode=40001" in message for message in retries), retries
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
            # The debts AND the journal rows that describe them, through the driver and BEFORE the
            # deletes below: `session.execute(delete(Debt))` is Core DML the write guard refuses
            # (that is `C2`), and `debt_operations.tx_id` RESTRICTs `transactions.tx_id`, so an
            # envelope still standing would block the transaction delete above it.
            await purge_test_ledger(cleanup, equivalent_ids=[equivalent.id])
            await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id == tx_id))
            await cleanup.execute(delete(Transaction).where(Transaction.tx_id == tx_id))
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
