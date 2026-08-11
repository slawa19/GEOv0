"""PostgreSQL clearing commit-confirmation and deterministic replay coverage."""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_post_commit_cancellation_replays_once_and_new_cycle_still_executes_postgres(
    db_session,
    monkeypatch,
):
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: durable clearing commit confirmation")

    from app.core.clearing.service import (
        ClearingCommittedAfterCancellation,
        ClearingService,
    )
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CR{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]
    replacement_debt_id = uuid.uuid4()
    replacement_cycle = [
        {"debt_id": str(debt_ids[0])},
        {"debt_id": str(replacement_debt_id)},
        {"debt_id": str(debt_ids[2])},
    ]

    service_session = None
    replay_session = None
    replacement_session = None
    service_task = None
    release_commit_ack = asyncio.Event()
    commit_completed = asyncio.Event()

    try:
        async with TestingSessionLocal() as setup:
            setup.add(
                Equivalent(
                    id=equivalent_id,
                    code=equivalent_code,
                    description="Clearing commit replay test",
                    precision=2,
                )
            )
            setup.add_all(
                [
                    Participant(
                        id=participant_id,
                        pid=f"{label}_CR_{nonce}",
                        display_name=label,
                        public_key=f"pk_{label}_{nonce}",
                        type="person",
                        status="active",
                    )
                    for participant_id, label in zip(
                        participant_ids,
                        ("A", "B", "C"),
                        strict=True,
                    )
                ]
            )
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=creditor_id,
                        to_participant_id=debtor_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        policy={"auto_clearing": True},
                        status="active",
                    )
                    for debtor_id, creditor_id in (
                        (a_id, b_id),
                        (b_id, c_id),
                        (c_id, a_id),
                    )
                ]
            )
            setup.add_all(
                [
                    Debt(
                        id=debt_id,
                        debtor_id=debtor_id,
                        creditor_id=creditor_id,
                        equivalent_id=equivalent_id,
                        amount=Decimal(amount),
                    )
                    for debt_id, debtor_id, creditor_id, amount in (
                        (debt_ids[0], a_id, b_id, "100.00"),
                        (debt_ids[1], b_id, c_id, "30.00"),
                        (debt_ids[2], c_id, a_id, "40.00"),
                    )
                ]
            )
            await setup.commit()

        service_session = TestingSessionLocal()
        await service_session.connection(
            execution_options={"isolation_level": "READ COMMITTED"}
        )
        service = ClearingService(service_session)
        real_commit = service_session.commit

        async def _commit_then_delay_ack():
            await real_commit()
            commit_completed.set()
            await release_commit_ack.wait()

        monkeypatch.setattr(service_session, "commit", _commit_then_delay_ack)

        service_task = asyncio.create_task(
            service.execute_clearing_with_amount(cycle),
            name="clearing-post-commit-cancellation",
        )
        await asyncio.wait_for(commit_completed.wait(), timeout=5.0)
        service_task.cancel()
        await asyncio.sleep(0)
        release_commit_ack.set()

        with pytest.raises(ClearingCommittedAfterCancellation) as cancellation:
            await asyncio.wait_for(service_task, timeout=5.0)

        replay_session = TestingSessionLocal()
        await replay_session.connection(
            execution_options={"isolation_level": "READ COMMITTED"}
        )
        replay_amount = await ClearingService(
            replay_session
        ).execute_clearing_with_amount(list(reversed(cycle)))

        assert replay_amount == Decimal("30.00000000")
        assert cancellation.value.cleared_amount == replay_amount
        assert not replay_session.in_transaction()

        # Anti-vacuum: a genuinely new occurrence has a new Debt-ID set and must
        # not be mistaken for replay of the committed cycle.
        async with TestingSessionLocal() as add_replacement:
            add_replacement.add(
                Debt(
                    id=replacement_debt_id,
                    debtor_id=b_id,
                    creditor_id=c_id,
                    equivalent_id=equivalent_id,
                    amount=Decimal("5.00"),
                )
            )
            await add_replacement.commit()

        replacement_session = TestingSessionLocal()
        replacement_amount = await ClearingService(
            replacement_session
        ).execute_clearing_with_amount(replacement_cycle)
        assert replacement_amount == Decimal("5.00000000")

        async with TestingSessionLocal() as verify:
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(participant_ids),
                    )
                )
            ).all()
            clearing_audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.operation_type == "CLEARING",
                        IntegrityAuditLog.equivalent_code == equivalent_code,
                    )
                )
            ).all()
            remaining_debts = {
                debt.id: debt.amount
                for debt in (
                    await verify.scalars(
                        select(Debt).where(Debt.equivalent_id == equivalent_id)
                    )
                ).all()
            }

        assert len(clearing_transactions) == 2
        assert all(tx.state == "COMMITTED" for tx in clearing_transactions)
        assert len(clearing_audits) == 2
        assert remaining_debts == {
            debt_ids[0]: Decimal("65.00000000"),
            debt_ids[2]: Decimal("5.00000000"),
        }
    finally:
        primary_error = sys.exc_info()[1]
        release_commit_ack.set()
        try:
            if service_task is not None and not service_task.done():
                service_task.cancel()
                try:
                    await asyncio.wait_for(service_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            elif service_task is not None and not service_task.cancelled():
                service_task.exception()

            async with asyncio.timeout(5.0):
                for session in (
                    service_session,
                    replay_session,
                    replacement_session,
                ):
                    if session is not None:
                        await session.rollback()
                        await session.close()

                async with TestingSessionLocal() as cleanup:
                    await cleanup.execute(
                        delete(IntegrityAuditLog).where(
                            IntegrityAuditLog.equivalent_code == equivalent_code
                        )
                    )
                    await cleanup.execute(
                        delete(PrepareLock).where(
                            PrepareLock.participant_id.in_(participant_ids)
                        )
                    )
                    await cleanup.execute(
                        delete(Transaction).where(
                            Transaction.initiator_id.in_(participant_ids)
                        )
                    )
                    await cleanup.execute(
                        delete(Debt).where(Debt.equivalent_id == equivalent_id)
                    )
                    await cleanup.execute(
                        delete(TrustLine).where(
                            TrustLine.equivalent_id == equivalent_id
                        )
                    )
                    await cleanup.execute(
                        delete(Participant).where(
                            Participant.id.in_(participant_ids)
                        )
                    )
                    await cleanup.execute(
                        delete(Equivalent).where(Equivalent.id == equivalent_id)
                    )
                    await cleanup.commit()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Clearing commit-replay teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
