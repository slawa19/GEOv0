"""PostgreSQL request-level payment idempotency races."""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_concurrent_duplicate_payment_request_never_regresses_terminal_state_postgres(
    db_session,
    monkeypatch,
):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates concurrent tx_id uniqueness semantics")

    from app.config import settings
    from app.core.payments.service import PaymentService
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.utils.event_bus import event_bus
    from app.utils.exceptions import ConflictException
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 5000)
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 15)

    nonce = uuid.uuid4().hex[:10]
    tx_id = str(uuid.uuid4())
    equivalent_id = uuid.uuid4()
    sender_id = uuid.uuid4()
    receiver_id = uuid.uuid4()
    equivalent_code = f"ID{nonce}".upper()
    sender_pid = f"A_ID_{nonce}"
    receiver_pid = f"B_ID_{nonce}"

    publications: list[dict] = []

    def _capture_publish(**kwargs):
        publications.append(dict(kwargs))

    monkeypatch.setattr(event_bus, "publish", _capture_publish)

    loser_passed_initial_lookup = asyncio.Event()
    release_loser = asyncio.Event()
    winner_row_committed = asyncio.Event()
    release_winner = asyncio.Event()
    loser_task = None
    winner_task = None
    loser_session = None
    winner_session = None

    try:
        equivalent = Equivalent(
            id=equivalent_id,
            code=equivalent_code,
            description="Concurrent idempotency test",
            precision=2,
        )
        sender = Participant(
            id=sender_id,
            pid=sender_pid,
            display_name="A",
            public_key=f"pk_A_{nonce}",
            type="person",
            status="active",
        )
        receiver = Participant(
            id=receiver_id,
            pid=receiver_pid,
            display_name="B",
            public_key=f"pk_B_{nonce}",
            type="person",
            status="active",
        )
        async with TestingSessionLocal() as setup:
            setup.add_all(
                [
                    equivalent,
                    sender,
                    receiver,
                    TrustLine(
                        from_participant_id=receiver_id,
                        to_participant_id=sender_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("100.00"),
                        status="active",
                    ),
                ]
            )
            await setup.commit()

        loser_session = TestingSessionLocal()
        winner_session = TestingSessionLocal()
        for session in (loser_session, winner_session):
            await session.connection(
                execution_options={"isolation_level": "READ COMMITTED"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "read committed"

        loser_service = PaymentService(loser_session)
        winner_service = PaymentService(winner_session)
        loser_build_graph = loser_service.router.build_graph
        winner_prepare = winner_service.engine.prepare

        async def _hold_loser_after_initial_lookup(*args, **kwargs):
            graph = await loser_build_graph(*args, **kwargs)
            loser_passed_initial_lookup.set()
            await release_loser.wait()
            return graph

        async def _hold_winner_after_new_row_commit(*args, **kwargs):
            winner_row_committed.set()
            await release_winner.wait()
            return await winner_prepare(*args, **kwargs)

        monkeypatch.setattr(
            loser_service.router,
            "build_graph",
            _hold_loser_after_initial_lookup,
        )
        monkeypatch.setattr(
            winner_service.engine,
            "prepare",
            _hold_winner_after_new_row_commit,
        )

        async def _pay(service: PaymentService):
            try:
                return await service.create_payment_internal(
                    sender_id,
                    to_pid=receiver_pid,
                    equivalent=equivalent_code,
                    amount="10.00",
                    idempotency_key=tx_id,
                )
            except Exception as exc:
                return exc

        loser_task = asyncio.create_task(_pay(loser_service))
        await asyncio.wait_for(
            loser_passed_initial_lookup.wait(),
            timeout=5.0,
        )

        winner_task = asyncio.create_task(_pay(winner_service))
        await asyncio.wait_for(winner_row_committed.wait(), timeout=5.0)
        assert not winner_task.done()

        release_loser.set()
        loser_result = await asyncio.wait_for(loser_task, timeout=5.0)
        assert isinstance(loser_result, ConflictException)
        assert loser_result.code == "E008"
        assert loser_result.status_code == 409
        assert "in progress" in loser_result.message

        release_winner.set()
        winner_result = await asyncio.wait_for(winner_task, timeout=10.0)
        assert not isinstance(winner_result, Exception)
        assert winner_result.status == "COMMITTED"

        async with TestingSessionLocal() as verify:
            transaction_count = await verify.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.tx_id == tx_id)
            )
            transaction = await verify.scalar(
                select(Transaction).where(Transaction.tx_id == tx_id)
            )
            debt_amount = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == sender_id,
                    Debt.creditor_id == receiver_id,
                    Debt.equivalent_id == equivalent_id,
                )
            )
            remaining_locks = (
                await verify.scalars(
                    select(PrepareLock).where(PrepareLock.tx_id == tx_id)
                )
            ).all()
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.operation_type == "PAYMENT",
                        IntegrityAuditLog.tx_id == tx_id,
                    )
                )
            ).all()

            assert transaction_count == 1
            assert transaction is not None and transaction.state == "COMMITTED"
            assert debt_amount == Decimal("10.00000000")
            assert remaining_locks == []
            assert len(audits) == 1
            assert audits[0].verification_passed is True

        assert len(publications) == 1
        assert publications[0]["event"] == "payment.received"
        assert publications[0]["payload"]["tx_id"] == tx_id
    finally:
        primary_error = sys.exc_info()[1]
        try:
            release_loser.set()
            release_winner.set()
            tasks = [
                task for task in (loser_task, winner_task) if task is not None
            ]
            done: set[asyncio.Task] = set()
            pending: set[asyncio.Task] = set()
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=5.0)
                for task in pending:
                    task.cancel()
            still_pending: set[asyncio.Task] = set()
            if pending:
                cancelled, still_pending = await asyncio.wait(pending, timeout=1.0)
                done.update(cancelled)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if still_pending:
                pending_names = sorted(task.get_name() for task in still_pending)
                raise AssertionError(
                    "Idempotency workers did not stop after cancellation: "
                    f"{pending_names}"
                )

            async with asyncio.timeout(5.0):
                for session in (loser_session, winner_session):
                    if session is not None:
                        await session.rollback()
                        await session.close()
                async with TestingSessionLocal() as cleanup:
                    await cleanup.execute(
                        delete(IntegrityAuditLog).where(
                            IntegrityAuditLog.tx_id == tx_id
                        )
                    )
                    await cleanup.execute(
                        delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
                    )
                    await cleanup.execute(
                        delete(Transaction).where(Transaction.tx_id == tx_id)
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
                            Participant.id.in_([sender_id, receiver_id])
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
                "Idempotency test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
