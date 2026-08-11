import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_payment_engine_commit_retries_whole_uow_on_serialization_failure_postgres(
    db_session, monkeypatch
):
    """Postgres-only regression test for P0.1.

    We simulate a SERIALIZABLE serialization failure (sqlstate=40001) on the first
    commit attempt and assert that the engine re-runs the *whole* unit-of-work
    (business logic + commit), not just `session.commit()`.
    """

    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None

    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates SERIALIZABLE whole-uow retry")

    # Local imports so sqlite runs don't import Postgres-only bits.
    from tests.conftest import TestingSessionLocal
    from app.core.payments.engine import PaymentEngine
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine

    nonce = uuid.uuid4().hex[:10]
    eq_code = ("UOW" + nonce).upper()

    tx_id = str(uuid.uuid4())

    async with TestingSessionLocal() as setup:
        eq = Equivalent(code=eq_code, symbol=eq_code, description=None, precision=2)
        a = Participant(
            pid=f"A_UOW_{nonce}",
            display_name="A",
            public_key=f"pk_A_{nonce}",
            type="person",
            status="active",
            profile={},
        )
        b = Participant(
            pid=f"B_UOW_{nonce}",
            display_name="B",
            public_key=f"pk_B_{nonce}",
            type="person",
            status="active",
            profile={},
        )
        setup.add_all([eq, a, b])
        await setup.commit()
        await setup.refresh(eq)
        await setup.refresh(a)
        await setup.refresh(b)

        # A -> B enabled by trustline B -> A.
        setup.add(
            TrustLine(
                from_participant_id=b.id,
                to_participant_id=a.id,
                equivalent_id=eq.id,
                limit=Decimal("100.00"),
                status="active",
            )
        )

        transaction = Transaction(
            id=uuid.uuid4(),
            tx_id=tx_id,
            type="PAYMENT",
            initiator_id=a.id,
            payload={"from": a.pid, "to": b.pid},
            state="PREPARED",
        )
        setup.add(transaction)
        await setup.flush()

        setup.add(
            PrepareLock(
                tx_id=tx_id,
                participant_id=a.id,
                effects={
                    "flows": [
                        {
                            "from": str(a.id),
                            "to": str(b.id),
                            "amount": "7.00",
                            "equivalent": str(eq.id),
                        }
                    ]
                },
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        )
        await setup.commit()

    async with TestingSessionLocal() as session:
        eng = PaymentEngine(session)
        # Make the test fast and deterministic.
        eng._retry_attempts = 2
        eng._retry_base_delay_s = 0.0
        eng._retry_max_delay_s = 0.0

        # Count business-logic executions.
        apply_calls = {"n": 0}
        orig_apply_flow = eng._apply_flow

        async def _apply_flow_counted(from_id, to_id, amount, equivalent_id):
            apply_calls["n"] += 1
            return await orig_apply_flow(from_id, to_id, amount, equivalent_id)

        monkeypatch.setattr(eng, "_apply_flow", _apply_flow_counted)

        # Force a retryable DBAPIError on the first commit.
        orig_commit = session.commit
        commit_calls = {"n": 0}

        class _FakePgError(Exception):
            sqlstate = "40001"

        async def _commit_with_one_serialization_failure():
            commit_calls["n"] += 1
            if commit_calls["n"] == 1:
                raise DBAPIError(
                    statement="COMMIT",
                    params=None,
                    orig=_FakePgError("serialization_failure"),
                    connection_invalidated=False,
                )
            return await orig_commit()

        monkeypatch.setattr(session, "commit", _commit_with_one_serialization_failure)

        ok = await eng.commit(tx_id)
        assert ok is True

        # If retry was commit-only, _apply_flow would run exactly once.
        assert apply_calls["n"] == 2

        tx_state = (
            await session.execute(
                select(Transaction.state).where(Transaction.tx_id == tx_id)
            )
        ).scalar_one()
        assert tx_state == "COMMITTED"

        locks_left = (
            await session.execute(
                select(PrepareLock.id).where(PrepareLock.tx_id == tx_id)
            )
        ).scalars().all()
        assert locks_left == []

        debt = (
            await session.execute(
                select(Debt).where(
                    Debt.debtor_id == a.id,
                    Debt.creditor_id == b.id,
                    Debt.equivalent_id == eq.id,
                )
            )
        ).scalar_one_or_none()

        assert debt is not None
        assert debt.amount == Decimal("7.00")

    # Cleanup (best-effort) to keep shared Postgres test DB tidy.
    async with TestingSessionLocal() as cleanup:
        await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id == tx_id))
        await cleanup.execute(delete(Transaction).where(Transaction.tx_id == tx_id))
        await cleanup.execute(delete(Debt).where(Debt.equivalent_id == eq.id))
        await cleanup.execute(delete(TrustLine).where(TrustLine.equivalent_id == eq.id))
        await cleanup.execute(delete(Participant).where(Participant.pid.in_([a.pid, b.pid])))
        await cleanup.execute(delete(Equivalent).where(Equivalent.code == eq_code))
        await cleanup.commit()


@pytest.mark.asyncio
async def test_payment_engine_commit_retries_real_concurrent_debt_insert_postgres(
    db_session,
    monkeypatch,
):
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates a real concurrent Debt insert retry")

    from sqlalchemy import func

    from app.core.payments.engine import PaymentEngine
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    equivalent = Equivalent(
        code=("UQ" + nonce).upper(),
        symbol="UQ",
        description=None,
        precision=2,
    )
    participant_a = Participant(
        pid=f"A_UQ_{nonce}",
        display_name="A",
        public_key=f"pk_A_UQ_{nonce}",
        type="person",
        status="active",
        profile={},
    )
    participant_b = Participant(
        pid=f"B_UQ_{nonce}",
        display_name="B",
        public_key=f"pk_B_UQ_{nonce}",
        type="person",
        status="active",
        profile={},
    )
    tx_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    async with TestingSessionLocal() as setup:
        setup.add_all([equivalent, participant_a, participant_b])
        await setup.flush()
        trustline = TrustLine(
            from_participant_id=participant_b.id,
            to_participant_id=participant_a.id,
            equivalent_id=equivalent.id,
            limit=Decimal("100.00"),
            status="active",
        )
        setup.add(trustline)
        for tx_id in tx_ids:
            setup.add(
                Transaction(
                    id=uuid.uuid4(),
                    tx_id=tx_id,
                    type="PAYMENT",
                    initiator_id=participant_a.id,
                    payload={"from": participant_a.pid, "to": participant_b.pid},
                    state="PREPARED",
                )
            )
        await setup.flush()
        for tx_id in tx_ids:
            setup.add(
                PrepareLock(
                    tx_id=tx_id,
                    participant_id=participant_a.id,
                    effects={
                        "flows": [
                            {
                                "from": str(participant_a.id),
                                "to": str(participant_b.id),
                                "amount": "3.00",
                                "equivalent": str(equivalent.id),
                            }
                        ]
                    },
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                )
            )
        await setup.commit()

    holder_acquired = asyncio.Event()
    waiter_attempted = asyncio.Event()
    waiter_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    observed_retry_errors: list[tuple[str | None, str]] = []
    apply_calls = {"holder": 0, "waiter": 0}
    holder_task = None
    waiter_task = None

    async with TestingSessionLocal() as holder_session, TestingSessionLocal() as waiter_session:
        await holder_session.connection(
            execution_options={"isolation_level": "SERIALIZABLE"}
        )
        await waiter_session.connection(
            execution_options={"isolation_level": "SERIALIZABLE"}
        )
        holder_engine = PaymentEngine(holder_session)
        waiter_engine = PaymentEngine(waiter_session)
        assert holder_engine._retry_attempts == 3
        assert waiter_engine._retry_attempts == 3
        holder_engine._retry_base_delay_s = 0.0
        holder_engine._retry_max_delay_s = 0.0
        waiter_engine._retry_base_delay_s = 0.0
        waiter_engine._retry_max_delay_s = 0.0

        holder_owner_acquire = holder_engine._acquire_equivalent_owner_locks
        waiter_owner_acquire = waiter_engine._acquire_equivalent_owner_locks
        holder_apply = holder_engine._apply_flow
        waiter_apply = waiter_engine._apply_flow
        waiter_retryable = waiter_engine._is_retryable_db_error

        async def _hold_owner(equivalent_ids):
            await holder_owner_acquire(equivalent_ids)
            holder_acquired.set()
            await release_holder.wait()

        async def _observe_waiter_owner(equivalent_ids):
            waiter_attempted.set()
            await waiter_owner_acquire(equivalent_ids)
            waiter_acquired.set()

        async def _count_holder_apply(*args, **kwargs):
            apply_calls["holder"] += 1
            return await holder_apply(*args, **kwargs)

        async def _count_waiter_apply(*args, **kwargs):
            apply_calls["waiter"] += 1
            return await waiter_apply(*args, **kwargs)

        def _record_retryable(exc, *, op):
            observed_retry_errors.append(
                (waiter_engine._get_pgcode(exc), str(exc.statement or ""))
            )
            return waiter_retryable(exc, op=op)

        monkeypatch.setattr(
            holder_engine,
            "_acquire_equivalent_owner_locks",
            _hold_owner,
        )
        monkeypatch.setattr(
            waiter_engine,
            "_acquire_equivalent_owner_locks",
            _observe_waiter_owner,
        )
        monkeypatch.setattr(holder_engine, "_apply_flow", _count_holder_apply)
        monkeypatch.setattr(waiter_engine, "_apply_flow", _count_waiter_apply)
        monkeypatch.setattr(waiter_engine, "_is_retryable_db_error", _record_retryable)

        try:
            holder_task = asyncio.create_task(holder_engine.commit(tx_ids[0]))
            await asyncio.wait_for(holder_acquired.wait(), timeout=5.0)
            waiter_task = asyncio.create_task(waiter_engine.commit(tx_ids[1]))
            await asyncio.wait_for(waiter_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(waiter_acquired.wait(), timeout=0.25)

            release_holder.set()
            assert await asyncio.wait_for(holder_task, timeout=10.0) is True
            assert await asyncio.wait_for(waiter_task, timeout=10.0) is True
        finally:
            release_holder.set()
            tasks = [task for task in (holder_task, waiter_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await holder_session.rollback()
            await waiter_session.rollback()

    assert len(observed_retry_errors) == 1
    assert observed_retry_errors[0][0] in {"40001", "23505"}
    assert "INSERT INTO debts" in observed_retry_errors[0][1]
    assert apply_calls == {"holder": 1, "waiter": 2}

    try:
        async with TestingSessionLocal() as verify:
            states = (
                await verify.execute(
                    select(Transaction.state)
                    .where(Transaction.tx_id.in_(tx_ids))
                    .order_by(Transaction.tx_id)
                )
            ).scalars().all()
            debt_amount = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == participant_a.id,
                    Debt.creditor_id == participant_b.id,
                    Debt.equivalent_id == equivalent.id,
                )
            )
            lock_count = await verify.scalar(
                select(func.count()).select_from(PrepareLock).where(
                    PrepareLock.tx_id.in_(tx_ids)
                )
            )
            audit_count = await verify.scalar(
                select(func.count()).select_from(IntegrityAuditLog).where(
                    IntegrityAuditLog.tx_id.in_(tx_ids),
                    IntegrityAuditLog.operation_type == "PAYMENT",
                )
            )
            persisted_limit = await verify.scalar(
                select(TrustLine.limit).where(TrustLine.id == trustline.id)
            )

            assert states == ["COMMITTED", "COMMITTED"]
            assert debt_amount == Decimal("6.00000000")
            assert lock_count == 0
            assert audit_count == 2
            assert persisted_limit == Decimal("100.00000000")
    finally:
        async with TestingSessionLocal() as cleanup:
            await cleanup.execute(
                delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids))
            )
            await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
            await cleanup.execute(delete(Transaction).where(Transaction.tx_id.in_(tx_ids)))
            await cleanup.execute(
                delete(Debt).where(Debt.equivalent_id == equivalent.id)
            )
            await cleanup.execute(
                delete(TrustLine).where(TrustLine.equivalent_id == equivalent.id)
            )
            await cleanup.execute(
                delete(Participant).where(
                    Participant.id.in_([participant_a.id, participant_b.id])
                )
            )
            await cleanup.execute(
                delete(Equivalent).where(Equivalent.id == equivalent.id)
            )
            await cleanup.commit()

