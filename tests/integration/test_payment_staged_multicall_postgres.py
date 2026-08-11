import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.payments.engine import PaymentEngine
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine


pytestmark = pytest.mark.postgres


def _require_postgres(db_session) -> None:
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates staged transaction advisory locks")


def _sqlstate(exc: BaseException) -> str | None:
    if not isinstance(exc, DBAPIError):
        return None
    orig = getattr(exc, "orig", None)
    return (
        getattr(orig, "sqlstate", None)
        or getattr(orig, "pgcode", None)
        or getattr(orig, "code", None)
    )


def _payment_lock(*, tx_id, participant_id, from_id, to_id, equivalent_id, amount):
    return PrepareLock(
        tx_id=tx_id,
        participant_id=participant_id,
        effects={
            "flows": [
                {
                    "from": str(from_id),
                    "to": str(to_id),
                    "amount": str(amount),
                    "equivalent": str(equivalent_id),
                }
            ]
        },
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


async def _seed_staged_batches() -> dict:
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8]
    equivalent = Equivalent(
        code=f"SM{nonce}".upper(),
        description="Staged multi-call payment test",
        precision=2,
    )
    participant_a, participant_b, participant_c = [
        Participant(
            pid=f"P_SM_{label}_{nonce}",
            display_name=f"P_SM_{label}_{nonce}",
            public_key=f"pk_sm_{label}_{nonce}",
            type="person",
            status="active",
        )
        for label in ("A", "B", "C")
    ]

    async with TestingSessionLocal() as setup:
        setup.add_all([equivalent, participant_a, participant_b, participant_c])
        await setup.flush()
        setup.add_all(
            [
                TrustLine(
                    from_participant_id=participant_b.id,
                    to_participant_id=participant_a.id,
                    equivalent_id=equivalent.id,
                    limit=Decimal("50.00"),
                    status="active",
                ),
                TrustLine(
                    from_participant_id=participant_c.id,
                    to_participant_id=participant_b.id,
                    equivalent_id=equivalent.id,
                    limit=Decimal("50.00"),
                    status="active",
                ),
                Debt(
                    debtor_id=participant_a.id,
                    creditor_id=participant_b.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("1.00"),
                ),
                Debt(
                    debtor_id=participant_b.id,
                    creditor_id=participant_c.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("1.00"),
                ),
            ]
        )

        transactions = {}
        for name, initiator in (
            ("left_ab", participant_a),
            ("left_bc", participant_b),
            ("right_bc", participant_b),
            ("right_ab", participant_a),
        ):
            tx = Transaction(
                tx_id=str(uuid.uuid4()),
                type="PAYMENT",
                initiator_id=initiator.id,
                payload={},
                state="PREPARED",
            )
            transactions[name] = tx
            setup.add(tx)
        # PrepareLock has no ORM relationship to Transaction; flush parent rows first.
        await setup.flush()

        setup.add_all(
            [
                _payment_lock(
                    tx_id=transactions["left_ab"].tx_id,
                    participant_id=participant_a.id,
                    from_id=participant_a.id,
                    to_id=participant_b.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("1.00"),
                ),
                _payment_lock(
                    tx_id=transactions["left_bc"].tx_id,
                    participant_id=participant_b.id,
                    from_id=participant_b.id,
                    to_id=participant_c.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("1.00"),
                ),
                _payment_lock(
                    tx_id=transactions["right_bc"].tx_id,
                    participant_id=participant_b.id,
                    from_id=participant_b.id,
                    to_id=participant_c.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("2.00"),
                ),
                _payment_lock(
                    tx_id=transactions["right_ab"].tx_id,
                    participant_id=participant_a.id,
                    from_id=participant_a.id,
                    to_id=participant_b.id,
                    equivalent_id=equivalent.id,
                    amount=Decimal("2.00"),
                ),
            ]
        )
        await setup.commit()

    return {
        "equivalent_id": equivalent.id,
        "participant_ids": [participant_a.id, participant_b.id, participant_c.id],
        "participant_a_id": participant_a.id,
        "participant_b_id": participant_b.id,
        "participant_c_id": participant_c.id,
        "tx_ids": {name: tx.tx_id for name, tx in transactions.items()},
    }


async def _cleanup_seed(seed: dict) -> None:
    from tests.conftest import TestingSessionLocal

    tx_ids = list(seed["tx_ids"].values())
    async with TestingSessionLocal() as cleanup:
        await cleanup.execute(
            delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids))
        )
        await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
        await cleanup.execute(delete(Transaction).where(Transaction.tx_id.in_(tx_ids)))
        await cleanup.execute(
            delete(Debt).where(Debt.equivalent_id == seed["equivalent_id"])
        )
        await cleanup.execute(
            delete(TrustLine).where(
                TrustLine.equivalent_id == seed["equivalent_id"]
            )
        )
        await cleanup.execute(
            delete(Participant).where(Participant.id.in_(seed["participant_ids"]))
        )
        await cleanup.execute(
            delete(Equivalent).where(Equivalent.id == seed["equivalent_id"])
        )
        await cleanup.commit()


@pytest.mark.asyncio
async def test_staged_multicall_batches_do_not_exhaust_retry_on_retained_locks_postgres(
    db_session,
) -> None:
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_staged_batches()
    first_calls_ready = asyncio.Event()
    first_calls_count = 0
    first_calls_guard = asyncio.Lock()
    owner_attempts = 0

    async def _release_barrier_for_current_or_coarse_owner(observer) -> None:
        for _ in range(2000):
            async with first_calls_guard:
                ready_count = first_calls_count
                attempts = owner_attempts
            if ready_count == 2:
                first_calls_ready.set()
                return
            if ready_count == 1 and attempts == 2:
                owner_waiting = await observer.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_locks "
                        "WHERE locktype = 'advisory' AND NOT granted"
                        ")"
                    )
                )
                if owner_waiting:
                    first_calls_ready.set()
                    return
            await asyncio.sleep(0.01)
        raise AssertionError("staged barrier did not observe a safe release condition")

    async def _run_batch(first_tx_id: str, second_tx_id: str) -> bool:
        nonlocal first_calls_count, owner_attempts
        async with TestingSessionLocal() as session:
            await session.connection(
                execution_options={"isolation_level": "SERIALIZABLE"}
            )
            engine = PaymentEngine(session)
            async with first_calls_guard:
                owner_attempts += 1
            await engine.acquire_staged_equivalent_owner_locks(
                [seed["equivalent_id"]]
            )
            assert engine._retry_attempts == 3
            engine._retry_base_delay_s = 0.0
            engine._retry_max_delay_s = 0.0
            try:
                assert await engine.commit(first_tx_id, commit=False) is True
                async with first_calls_guard:
                    first_calls_count += 1
                    if first_calls_count == 2:
                        first_calls_ready.set()
                await asyncio.wait_for(first_calls_ready.wait(), timeout=5.0)
                assert await engine.commit(second_tx_id, commit=False) is True
                await session.commit()
                return True
            except BaseException:
                await session.rollback()
                raise

    try:
        batches = [
            (seed["tx_ids"]["left_ab"], seed["tx_ids"]["left_bc"]),
            (seed["tx_ids"]["right_bc"], seed["tx_ids"]["right_ab"]),
        ]
        async with TestingSessionLocal() as observer:
            barrier_task = asyncio.create_task(
                _release_barrier_for_current_or_coarse_owner(observer)
            )
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(_run_batch(*batch) for batch in batches),
                    return_exceptions=True,
                ),
                timeout=20.0,
            )
            await barrier_task
        failure_indexes = [
            index
            for index, result in enumerate(results)
            if isinstance(result, BaseException)
        ]
        assert len(failure_indexes) == 1
        failed_index = failure_indexes[0]
        assert _sqlstate(results[failed_index]) == "40001"

        # The serialization snapshot belongs to the whole staged owner, not the
        # savepoint. Retry the failed batch in a fresh outer transaction.
        results[failed_index] = await _run_batch(*batches[failed_index])
        assert results == [True, True]

        async with TestingSessionLocal() as verify:
            tx_ids = list(seed["tx_ids"].values())
            states = (
                await verify.scalars(
                    select(Transaction.state).where(Transaction.tx_id.in_(tx_ids))
                )
            ).all()
            assert states == ["COMMITTED"] * 4
            debts = {
                tuple(row)
                for row in (
                    await verify.execute(
                        select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                            Debt.equivalent_id == seed["equivalent_id"]
                        )
                    )
                ).all()
            }
            assert debts == {
                (
                    seed["participant_a_id"],
                    seed["participant_b_id"],
                    Decimal("4.00000000"),
                ),
                (
                    seed["participant_b_id"],
                    seed["participant_c_id"],
                    Decimal("4.00000000"),
                ),
            }
            assert (
                await verify.scalar(
                    select(func.count()).select_from(PrepareLock).where(
                        PrepareLock.tx_id.in_(tx_ids)
                    )
                )
                == 0
            )
            assert (
                await verify.scalar(
                    select(func.count()).select_from(IntegrityAuditLog).where(
                        IntegrityAuditLog.tx_id.in_(tx_ids),
                        IntegrityAuditLog.operation_type == "PAYMENT",
                    )
                )
                == 4
            )
            limits = (
                await verify.scalars(
                    select(TrustLine.limit).where(
                        TrustLine.equivalent_id == seed["equivalent_id"]
                    )
                )
            ).all()
            assert limits == [Decimal("50.00000000")] * 2
    finally:
        await _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_staged_owner_sorts_multi_equivalent_sets_without_global_serialization_postgres(
    db_session,
) -> None:
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    equivalent_a = uuid.uuid4()
    equivalent_b = uuid.uuid4()
    independent_equivalent = uuid.uuid4()
    holder_acquired = asyncio.Event()
    waiter_attempted = asyncio.Event()
    waiter_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    holder_task = None
    waiter_task = None

    async with (
        TestingSessionLocal() as holder_session,
        TestingSessionLocal() as waiter_session,
        TestingSessionLocal() as independent_session,
    ):
        holder_engine = PaymentEngine(holder_session)
        waiter_engine = PaymentEngine(waiter_session)
        independent_engine = PaymentEngine(independent_session)
        waiter_acquire = waiter_engine._acquire_equivalent_owner_locks

        async def _hold_owner_set() -> None:
            await holder_engine.acquire_staged_equivalent_owner_locks(
                [equivalent_b, equivalent_a]
            )
            holder_acquired.set()
            await release_holder.wait()
            await holder_session.rollback()

        async def _observe_waiter(equivalent_ids) -> None:
            waiter_attempted.set()
            await waiter_acquire(equivalent_ids)
            waiter_acquired.set()

        waiter_engine._acquire_equivalent_owner_locks = _observe_waiter

        try:
            holder_task = asyncio.create_task(_hold_owner_set())
            await asyncio.wait_for(holder_acquired.wait(), timeout=5.0)
            waiter_task = asyncio.create_task(
                waiter_engine.acquire_staged_equivalent_owner_locks(
                    [equivalent_a, equivalent_b]
                )
            )
            await asyncio.wait_for(waiter_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(waiter_acquired.wait(), timeout=0.25)

            # A disjoint equivalent is not serialized by the coarse owner set.
            await asyncio.wait_for(
                independent_engine.acquire_staged_equivalent_owner_locks(
                    [independent_equivalent]
                ),
                timeout=5.0,
            )
            await independent_session.rollback()

            release_holder.set()
            await asyncio.wait_for(holder_task, timeout=5.0)
            await asyncio.wait_for(waiter_task, timeout=5.0)
            assert waiter_acquired.is_set()
        finally:
            release_holder.set()
            tasks = [task for task in (holder_task, waiter_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await holder_session.rollback()
            await waiter_session.rollback()
            await independent_session.rollback()


@pytest.mark.asyncio
async def test_staged_owner_restores_outer_transaction_lock_timeout_postgres(
    db_session,
) -> None:
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    unrelated_lock_key = int.from_bytes(uuid.uuid4().bytes[:8], "big", signed=True)
    blocked_task = None

    async with (
        TestingSessionLocal() as holder_session,
        TestingSessionLocal() as staged_session,
    ):
        await holder_session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": unrelated_lock_key},
        )
        before = await staged_session.scalar(text("SHOW lock_timeout"))
        assert before == "0"

        engine = PaymentEngine(staged_session)
        engine._advisory_lock_budget_s = 0.05

        try:
            await engine.acquire_staged_equivalent_owner_locks([uuid.uuid4()])
            after = await staged_session.scalar(text("SHOW lock_timeout"))
            assert after == before

            blocked_task = asyncio.create_task(
                staged_session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": unrelated_lock_key},
                )
            )
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(blocked_task), timeout=0.20)
            assert not blocked_task.done()
        finally:
            if blocked_task is not None and not blocked_task.done():
                blocked_task.cancel()
            if blocked_task is not None:
                await asyncio.gather(blocked_task, return_exceptions=True)
            await staged_session.rollback()
            await holder_session.rollback()
