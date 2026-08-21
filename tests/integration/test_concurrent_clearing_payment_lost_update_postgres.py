"""PostgreSQL clearing/payment contention on one trustline."""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text


pytestmark = pytest.mark.postgres


async def _wait_for_matching_advisory_wait(observer, *, waiter_pid: int) -> bool:
    try:
        async with asyncio.timeout(5.0):
            while True:
                waiting = await observer.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_locks holder "
                        "JOIN pg_locks waiter ON "
                        "waiter.locktype = holder.locktype "
                        "AND waiter.database IS NOT DISTINCT FROM holder.database "
                        "AND waiter.classid IS NOT DISTINCT FROM holder.classid "
                        "AND waiter.objid IS NOT DISTINCT FROM holder.objid "
                        "AND waiter.objsubid IS NOT DISTINCT FROM holder.objsubid "
                        "WHERE holder.locktype = 'advisory' AND holder.granted "
                        "AND holder.database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database()) "
                        "AND waiter.pid = :waiter_pid AND NOT waiter.granted"
                        ")"
                    ),
                    {"waiter_pid": waiter_pid},
                )
                if waiting:
                    return True
    except asyncio.TimeoutError:
        return False


@pytest.mark.asyncio
async def test_concurrent_payment_and_clearing_same_trustline_preserve_effects_postgres(
    db_session,
    monkeypatch,
):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: clearing/payment row-lock and retry semantics")

    from app.core.clearing.service import ClearingService
    from app.core.payments.engine import PaymentEngine
    from app.core.payments.service import PaymentService
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.utils.event_bus import event_bus
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    payment_tx_id = str(uuid.uuid4())
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CP{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    debt_ids = [uuid.uuid4() for _ in range(3)]
    participant_pids = [f"{label}_CP_{nonce}" for label in ("A", "B", "C")]
    a_id, b_id, c_id = participant_ids
    a_pid, b_pid, c_pid = participant_pids
    cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]

    publications: list[dict] = []

    def _capture_publish(**kwargs):
        publications.append(dict(kwargs))

    monkeypatch.setattr(event_bus, "publish", _capture_publish)

    clearing_lock_scan_done = asyncio.Event()
    release_clearing = asyncio.Event()
    clearing_task = None
    payment_task = None
    clearing_session = None
    payment_session = None
    observer_session = None

    try:
        equivalent = Equivalent(
            id=equivalent_id,
            code=equivalent_code,
            description="Clearing/payment concurrency test",
            precision=2,
        )
        participants = [
            Participant(
                id=participant_id,
                pid=pid,
                display_name=label,
                public_key=f"pk_{label}_{nonce}",
                type="person",
                status="active",
            )
            for participant_id, pid, label in zip(
                participant_ids,
                participant_pids,
                ("A", "B", "C"),
                strict=True,
            )
        ]
        debts = [
            Debt(
                id=debt_ids[0],
                debtor_id=a_id,
                creditor_id=b_id,
                equivalent_id=equivalent_id,
                amount=Decimal("100.00"),
            ),
            Debt(
                id=debt_ids[1],
                debtor_id=b_id,
                creditor_id=c_id,
                equivalent_id=equivalent_id,
                amount=Decimal("30.00"),
            ),
            Debt(
                id=debt_ids[2],
                debtor_id=c_id,
                creditor_id=a_id,
                equivalent_id=equivalent_id,
                amount=Decimal("40.00"),
            ),
        ]
        async with TestingSessionLocal() as setup:
            setup.add(equivalent)
            setup.add_all(participants)
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=b_id,
                        to_participant_id=a_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=c_id,
                        to_participant_id=b_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=a_id,
                        to_participant_id=c_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        status="active",
                    ),
                ]
            )
            setup.add_all(debts)
            await setup.commit()

        clearing_session = TestingSessionLocal()
        payment_session = TestingSessionLocal()
        observer_session = TestingSessionLocal()
        for session in (clearing_session, payment_session):
            await session.connection(
                execution_options={"isolation_level": "READ COMMITTED"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "read committed"
        clearing_service = ClearingService(clearing_session)
        payment_service = PaymentService(payment_session)
        original_locked_pairs = clearing_service._locked_pairs_for_equivalent
        original_payment_owner = PaymentEngine._acquire_equivalent_owner_locks
        payment_owner_attempted = asyncio.Event()
        payment_owner_pid: int | None = None

        async def _hold_after_lock_scan(current_equivalent_id):
            locked_pairs = await original_locked_pairs(current_equivalent_id)
            assert locked_pairs == set()
            clearing_lock_scan_done.set()
            await release_clearing.wait()
            return locked_pairs

        monkeypatch.setattr(
            clearing_service,
            "_locked_pairs_for_equivalent",
            _hold_after_lock_scan,
        )

        async def _observe_payment_owner(engine, equivalent_ids):
            nonlocal payment_owner_pid
            payment_owner_pid = int(
                await engine.session.scalar(text("SELECT pg_backend_pid()"))
            )
            payment_owner_attempted.set()
            return await original_payment_owner(engine, equivalent_ids)

        monkeypatch.setattr(
            PaymentEngine,
            "_acquire_equivalent_owner_locks",
            _observe_payment_owner,
        )

        clearing_task = asyncio.create_task(
            clearing_service.execute_clearing_with_amount(cycle)
        )
        await asyncio.wait_for(clearing_lock_scan_done.wait(), timeout=5.0)

        payment_task = asyncio.create_task(
            payment_service.create_payment_internal(
                a_id,
                to_pid=b_pid,
                equivalent=equivalent_code,
                amount="50.00",
                idempotency_key=payment_tx_id,
            )
        )
        await asyncio.wait_for(payment_owner_attempted.wait(), timeout=5.0)
        assert payment_owner_pid is not None
        payment_waiting = await _wait_for_matching_advisory_wait(
            observer_session,
            waiter_pid=payment_owner_pid,
        )
        payment_error = (
            payment_task.exception()
            if payment_task.done() and not payment_task.cancelled()
            else None
        )
        assert payment_waiting, (
            "payment did not wait on clearing owner: "
            f"done={payment_task.done()} error={payment_error!r}"
        )
        assert not clearing_task.done()
        assert not payment_task.done()

        release_clearing.set()
        cleared_amount, payment_result = await asyncio.wait_for(
            asyncio.gather(clearing_task, payment_task),
            timeout=15.0,
        )
        assert cleared_amount == Decimal("30.00000000")
        assert payment_result.status == "COMMITTED"

        async with TestingSessionLocal() as verify:
            payment_tx = await verify.scalar(
                select(Transaction).where(Transaction.tx_id == payment_tx_id)
            )
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(participant_ids),
                    )
                )
            ).all()
            assert payment_tx is not None and payment_tx.state == "COMMITTED"
            assert len(clearing_transactions) == 1
            clearing_tx = clearing_transactions[0]
            assert clearing_tx.state == "COMMITTED"

            final_debts = {
                debt.id: (debt.amount, debt.version)
                for debt in (
                    await verify.scalars(
                        select(Debt).where(Debt.equivalent_id == equivalent_id)
                    )
                ).all()
            }
            trust_limits = (
                await verify.scalars(
                    select(TrustLine.limit).where(
                        TrustLine.equivalent_id == equivalent_id
                    )
                )
            ).all()
            remaining_locks = (
                await verify.scalars(
                    select(PrepareLock).where(PrepareLock.tx_id == payment_tx_id)
                )
            ).all()
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.tx_id.in_(
                            [payment_tx_id, clearing_tx.tx_id]
                        )
                    )
                )
            ).all()

            assert Decimal(str(clearing_tx.payload["amount"])) == Decimal(
                "30.00000000"
            )
            assert final_debts == {
                debt_ids[0]: (Decimal("120.00000000"), 3),
                debt_ids[2]: (Decimal("10.00000000"), 2),
            }
            assert trust_limits == [Decimal("200.00000000")] * 3
            assert remaining_locks == []
            assert {
                (audit.operation_type, audit.tx_id, audit.verification_passed)
                for audit in audits
            } == {
                ("PAYMENT", payment_tx_id, True),
                ("CLEARING", clearing_tx.tx_id, True),
            }

        assert len(publications) == 1
        assert publications[0]["event"] == "payment.received"
        assert publications[0]["payload"]["tx_id"] == payment_tx_id
    finally:
        primary_error = sys.exc_info()[1]
        try:
            release_clearing.set()
            tasks = [
                task for task in (clearing_task, payment_task) if task is not None
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
                    "Clearing/payment workers did not stop after cancellation: "
                    f"{pending_names}"
                )

            async with asyncio.timeout(5.0):
                for session in (
                    clearing_session,
                    payment_session,
                    observer_session,
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
                            PrepareLock.tx_id == payment_tx_id
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
                "Clearing/payment test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
