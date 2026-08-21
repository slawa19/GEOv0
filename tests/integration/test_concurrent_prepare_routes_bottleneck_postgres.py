import uuid
import asyncio
import sys
from decimal import Decimal

import pytest

from sqlalchemy import select


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_concurrent_payments_shared_bottleneck_commit_once_postgres(
    db_session,
    monkeypatch,
):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None

    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates single-route advisory lock serialization")

    from sqlalchemy import delete, text

    from app.core.payments.engine import PaymentEngine
    from app.core.payments.service import PaymentService
    from app.config import settings
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.utils.event_bus import event_bus
    from app.utils.exceptions import RoutingException
    from tests.conftest import TestingSessionLocal

    # This test deliberately pauses inside the lock protocol. Keep the product
    # timeout taxonomy out of the schedule while retaining bounded test waits.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 30)

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    participant_ids = [uuid.uuid4() for _ in range(4)]
    eq = Equivalent(
        id=equivalent_id,
        code=f"SR{nonce}".upper(),
        description="Single route concurrency test",
        precision=2,
    )
    pids = [f"A_SR_{nonce}", f"B_SR_{nonce}", f"C_SR_{nonce}", f"D_SR_{nonce}"]
    participants = [
        Participant(
            id=participant_id,
            pid=pid,
            display_name=pid,
            public_key=f"pk_{pid}",
            type="person",
            status="active",
        )
        for participant_id, pid in zip(participant_ids, pids, strict=True)
    ]
    id_by_pid = {participant.pid: participant.id for participant in participants}
    a_pid, b_pid, c_pid, d_pid = pids
    tx_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    original_acquire = PaymentEngine._acquire_equivalent_owner_locks
    acquire_calls = 0
    holder_entered = asyncio.Event()
    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_entered = asyncio.Event()
    waiter_acquired = asyncio.Event()

    async def _synchronized_acquire(self, equivalent_ids):
        nonlocal acquire_calls
        acquire_calls += 1
        if acquire_calls == 1:
            holder_entered.set()
            await waiter_entered.wait()
            await original_acquire(self, equivalent_ids)
            holder_acquired.set()
            await release_holder.wait()
            return

        if acquire_calls == 2:
            waiter_entered.set()
            await holder_acquired.wait()
            await original_acquire(self, equivalent_ids)
            waiter_acquired.set()
            return

        await original_acquire(self, equivalent_ids)

    monkeypatch.setattr(
        PaymentEngine,
        "_acquire_equivalent_owner_locks",
        _synchronized_acquire,
    )

    publications: list[dict] = []

    def _capture_publish(**kwargs):
        publications.append(dict(kwargs))

    monkeypatch.setattr(event_bus, "publish", _capture_publish)

    async def _pay(sender_id, tx_id: str):
        async with TestingSessionLocal() as session:
            await session.connection(
                execution_options={"isolation_level": "READ COMMITTED"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "read committed"
            try:
                return await PaymentService(session).create_payment_internal(
                    sender_id,
                    to_pid=d_pid,
                    equivalent=eq.code,
                    amount="8.00",
                    idempotency_key=tx_id,
                )
            except Exception as exc:
                return exc

    task1 = None
    task2 = None
    try:
        async with TestingSessionLocal() as setup:
            setup.add(eq)
            setup.add_all(participants)
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=id_by_pid[c_pid],
                        to_participant_id=id_by_pid[a_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("100.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=id_by_pid[c_pid],
                        to_participant_id=id_by_pid[b_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("100.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=id_by_pid[d_pid],
                        to_participant_id=id_by_pid[c_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("10.00"),
                        status="active",
                    ),
                ]
            )
            await setup.commit()

        task1 = asyncio.create_task(_pay(id_by_pid[a_pid], tx_ids[0]))
        task2 = asyncio.create_task(_pay(id_by_pid[b_pid], tx_ids[1]))
        await asyncio.wait_for(holder_entered.wait(), timeout=5.0)
        await asyncio.wait_for(waiter_entered.wait(), timeout=5.0)
        await asyncio.wait_for(holder_acquired.wait(), timeout=5.0)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(waiter_acquired.wait(), timeout=0.25)

        assert acquire_calls == 2
        assert not task1.done()
        assert not task2.done()

        release_holder.set()
        result1, result2 = await asyncio.wait_for(
            asyncio.gather(task1, task2),
            timeout=30.0,
        )

        results = [result1, result2]
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RoutingException)
        assert failures[0].code == "E002"
        committed_tx_id = successes[0].tx_id
        rejected_tx_id = next(tx_id for tx_id in tx_ids if tx_id != committed_tx_id)

        async with TestingSessionLocal() as verify:
            states = dict(
                (
                    await verify.execute(
                        select(Transaction.tx_id, Transaction.state).where(
                            Transaction.tx_id.in_(tx_ids)
                        )
                    )
                ).all()
            )
            shared_debt = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == id_by_pid[c_pid],
                    Debt.creditor_id == id_by_pid[d_pid],
                    Debt.equivalent_id == eq.id,
                )
            )
            locks = (
                await verify.scalars(
                    select(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids))
                )
            ).all()
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.operation_type == "PAYMENT",
                        IntegrityAuditLog.tx_id.in_(tx_ids),
                    )
                )
            ).all()

            assert states == {
                committed_tx_id: "COMMITTED",
                rejected_tx_id: "ABORTED",
            }
            assert shared_debt == Decimal("8.00000000")
            assert shared_debt <= Decimal("10.00")
            assert locks == []
            assert [(audit.tx_id, audit.verification_passed) for audit in audits] == [
                (committed_tx_id, True)
            ]

        assert len(publications) == 1
        assert publications[0]["event"] == "payment.received"
        assert publications[0]["payload"]["tx_id"] == committed_tx_id
    finally:
        primary_error = sys.exc_info()[1]
        try:
            release_holder.set()
            tasks = [task for task in (task1, task2) if task is not None]
            done = set()
            pending = set()
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=5.0)
            for task in pending:
                task.cancel()
            still_pending = set()
            if pending:
                cancelled, still_pending = await asyncio.wait(pending, timeout=1.0)
                done.update(cancelled)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if still_pending:
                pending_names = sorted(task.get_name() for task in still_pending)
                raise AssertionError(
                    f"Payment workers did not stop after cancellation: {pending_names}"
                )
            async with asyncio.timeout(5.0):
                async with TestingSessionLocal() as cleanup:
                    await cleanup.execute(
                        delete(IntegrityAuditLog).where(
                            IntegrityAuditLog.tx_id.in_(tx_ids)
                        )
                    )
                    await cleanup.execute(
                        delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids))
                    )
                    await cleanup.execute(
                        delete(Transaction).where(Transaction.tx_id.in_(tx_ids))
                    )
                    await cleanup.execute(
                        delete(Debt).where(Debt.equivalent_id == eq.id)
                    )
                    await cleanup.execute(
                        delete(TrustLine).where(TrustLine.equivalent_id == eq.id)
                    )
                    await cleanup.execute(
                        delete(Participant).where(
                            Participant.id.in_(list(id_by_pid.values()))
                        )
                    )
                    await cleanup.execute(
                        delete(Equivalent).where(Equivalent.id == eq.id)
                    )
                    await cleanup.commit()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Payment test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


@pytest.mark.asyncio
async def test_concurrent_prepare_routes_shared_bottleneck_serializes_on_postgres(
    db_session,
):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None

    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates pg_advisory_xact_lock serialization")

    # Local imports so sqlite runs don't import unused Postgres-only bits.
    from tests.conftest import TestingSessionLocal
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.core.payments.engine import PaymentEngine
    from sqlalchemy import delete

    # `db_session` is wrapped in an outer transaction whose writes are invisible to
    # other connections. Use nonce-scoped committed rows for this concurrency test.
    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CB{nonce}".upper()
    a_pid, b_pid, c_pid, d_pid = (
        f"A_CB_{nonce}",
        f"B_CB_{nonce}",
        f"C_CB_{nonce}",
        f"D_CB_{nonce}",
    )
    participant_ids = [uuid.uuid4() for _ in range(4)]
    id_by_pid = dict(
        zip(
            (a_pid, b_pid, c_pid, d_pid),
            participant_ids,
            strict=True,
        )
    )
    tx_ids = (f"cb-{nonce}-1", f"cb-{nonce}-2")
    task1 = None
    task2 = None

    routes1 = [([a_pid, c_pid, d_pid], Decimal("8.00"))]
    routes2 = [([b_pid, c_pid, d_pid], Decimal("8.00"))]

    async def _prepare_one(tx_id: str, routes):
        # Separate session so we get true concurrent DB behavior.
        # DO NOT wrap in an outer transaction + rollback: the second worker must be
        # able to observe the first worker's committed prepare locks.
        async with TestingSessionLocal() as session:
            eng = PaymentEngine(session)
            try:
                await eng.prepare_routes(tx_id, routes, equivalent_id)
                return "ok"
            except Exception as exc:
                return exc

    try:
        equivalent = Equivalent(
            id=equivalent_id,
            code=equivalent_code,
            description="Concurrent prepare routes test",
            precision=2,
        )
        participants = [
            Participant(
                id=id_by_pid[pid],
                pid=pid,
                display_name=pid,
                public_key=f"pk_{pid}",
                type="person",
                status="active",
            )
            for pid in (a_pid, b_pid, c_pid, d_pid)
        ]
        async with TestingSessionLocal() as setup:
            setup.add(equivalent)
            setup.add_all(participants)
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=id_by_pid[c_pid],
                        to_participant_id=id_by_pid[a_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("100.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=id_by_pid[c_pid],
                        to_participant_id=id_by_pid[b_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("100.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=id_by_pid[d_pid],
                        to_participant_id=id_by_pid[c_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("10.00"),
                        status="active",
                    ),
                    Transaction(
                        id=uuid.uuid4(),
                        tx_id=tx_ids[0],
                        type="PAYMENT",
                        initiator_id=id_by_pid[a_pid],
                        payload={},
                        state="NEW",
                    ),
                    Transaction(
                        id=uuid.uuid4(),
                        tx_id=tx_ids[1],
                        type="PAYMENT",
                        initiator_id=id_by_pid[b_pid],
                        payload={},
                        state="NEW",
                    ),
                ]
            )
            await setup.commit()

        task1 = asyncio.create_task(_prepare_one(tx_ids[0], routes1))
        task2 = asyncio.create_task(_prepare_one(tx_ids[1], routes2))
        r1, r2 = await asyncio.wait_for(
            asyncio.gather(task1, task2),
            timeout=10.0,
        )

        # Exactly one prepare may reserve the shared bottleneck.
        results = [r1, r2]
        ok_count = sum(1 for result in results if result == "ok")
        if ok_count != 1:
            summary = [
                "ok" if result == "ok" else f"{type(result).__name__}: {result}"
                for result in results
            ]
            raise AssertionError(
                f"Expected exactly one success; got {ok_count}. Results: {summary}"
            )

        from app.utils.exceptions import RoutingException

        failures = [result for result in results if result != "ok"]
        assert len(failures) == 1
        assert isinstance(failures[0], RoutingException)
        assert getattr(failures[0], "code", None) == "E002"
    finally:
        primary_error = sys.exc_info()[1]
        try:
            tasks = [task for task in (task1, task2) if task is not None]
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=5.0)
                for task in pending:
                    task.cancel()
                still_pending = set()
                if pending:
                    cancelled, still_pending = await asyncio.wait(pending, timeout=1.0)
                    done.update(cancelled)
                for task in done:
                    if not task.cancelled():
                        task.exception()
                if still_pending:
                    pending_names = sorted(task.get_name() for task in still_pending)
                    raise AssertionError(
                        "Prepare workers did not stop after cancellation: "
                        f"{pending_names}"
                    )

            async with asyncio.timeout(5.0):
                async with TestingSessionLocal() as cleanup:
                    await cleanup.execute(
                        delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids))
                    )
                    await cleanup.execute(
                        delete(Transaction).where(Transaction.tx_id.in_(tx_ids))
                    )
                    await cleanup.execute(
                        delete(TrustLine).where(
                            TrustLine.equivalent_id == equivalent_id
                        )
                    )
                    await cleanup.execute(
                        delete(Participant).where(Participant.id.in_(participant_ids))
                    )
                    await cleanup.execute(
                        delete(Equivalent).where(Equivalent.id == equivalent_id)
                    )
                    await cleanup.commit()
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Prepare-routes test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
