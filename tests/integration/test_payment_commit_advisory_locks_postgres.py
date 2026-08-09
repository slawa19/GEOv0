import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text

from app.core.payments.engine import PaymentEngine
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RoutingException


pytestmark = pytest.mark.postgres


def _require_postgres(db_session) -> None:
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates payment commit advisory-lock serialization")


async def _use_read_committed(session) -> None:
    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
    isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    assert str(isolation).lower() == "read committed"


async def _seed_prepared_payment(
    *,
    include_waiter: bool,
    prepare_holder: bool = True,
):
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8]
    equivalent = Equivalent(
        code=f"PC{nonce}".upper(),
        description="Payment commit advisory-lock test",
        precision=2,
    )
    sender_pid = f"C_PC_{nonce}"
    receiver_pid = f"D_PC_{nonce}"
    sender = Participant(
        pid=sender_pid,
        display_name=sender_pid,
        public_key=f"pk_{sender_pid}",
        type="person",
        status="active",
    )
    receiver = Participant(
        pid=receiver_pid,
        display_name=receiver_pid,
        public_key=f"pk_{receiver_pid}",
        type="person",
        status="active",
    )

    async with TestingSessionLocal() as setup:
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
                limit=Decimal("10.00"),
                status="active",
            )
        )
        holder_tx = Transaction(
            id=uuid.uuid4(),
            tx_id=str(uuid.uuid4()),
            type="PAYMENT",
            initiator_id=sender.id,
            payload={},
            state="NEW",
        )
        setup.add(holder_tx)
        waiter_tx = None
        if include_waiter:
            waiter_tx = Transaction(
                id=uuid.uuid4(),
                tx_id=str(uuid.uuid4()),
                type="PAYMENT",
                initiator_id=sender.id,
                payload={},
                state="NEW",
            )
            setup.add(waiter_tx)
        await setup.commit()

    if prepare_holder:
        async with TestingSessionLocal() as prepare_session:
            await _use_read_committed(prepare_session)
            await PaymentEngine(prepare_session).prepare(
                holder_tx.tx_id,
                [sender_pid, receiver_pid],
                Decimal("8.00"),
                equivalent.id,
            )

    return {
        "equivalent_id": equivalent.id,
        "participant_ids": [sender.id, receiver.id],
        "sender_id": sender.id,
        "receiver_id": receiver.id,
        "sender_pid": sender_pid,
        "receiver_pid": receiver_pid,
        "holder_tx_id": holder_tx.tx_id,
        "waiter_tx_id": waiter_tx.tx_id if waiter_tx is not None else None,
    }


async def _cleanup_seed(seed: dict) -> None:
    from tests.conftest import TestingSessionLocal

    tx_ids = [seed["holder_tx_id"]]
    if seed["waiter_tx_id"] is not None:
        tx_ids.append(seed["waiter_tx_id"])

    async with TestingSessionLocal() as cleanup:
        await cleanup.execute(
            delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids))
        )
        await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
        await cleanup.execute(
            delete(Transaction).where(Transaction.tx_id.in_(tx_ids))
        )
        await cleanup.execute(
            delete(Debt).where(Debt.equivalent_id == seed["equivalent_id"])
        )
        await cleanup.execute(
            delete(TrustLine).where(
                TrustLine.equivalent_id == seed["equivalent_id"]
            )
        )
        await cleanup.execute(
            delete(Participant).where(
                Participant.id.in_(seed["participant_ids"])
            )
        )
        await cleanup.execute(
            delete(Equivalent).where(Equivalent.id == seed["equivalent_id"])
        )
        await cleanup.commit()


@pytest.mark.asyncio
async def test_prepare_reservation_blocks_concurrent_commit_on_same_segment_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=True)
    waiter_segment_acquired = asyncio.Event()
    release_waiter_segment = asyncio.Event()
    commit_segment_attempted = asyncio.Event()
    commit_segment_acquired = asyncio.Event()
    waiter_keys: set[int] = set()
    commit_keys: set[int] = set()
    waiter_task = None
    commit_task = None
    exercise_completed = False

    async with TestingSessionLocal() as waiter_session, TestingSessionLocal() as commit_session:
        await _use_read_committed(waiter_session)
        await _use_read_committed(commit_session)
        waiter_engine = PaymentEngine(waiter_session)
        commit_engine = PaymentEngine(commit_session)
        waiter_segment_acquire = waiter_engine._acquire_segment_advisory_lock_keys
        commit_segment_acquire = commit_engine._acquire_segment_advisory_lock_keys

        async def _hold_waiter_segment(keys):
            waiter_keys.update(keys)
            await waiter_segment_acquire(keys)
            waiter_segment_acquired.set()
            await release_waiter_segment.wait()

        async def _observe_commit_segment(keys):
            commit_keys.update(keys)
            commit_segment_attempted.set()
            await commit_segment_acquire(keys)
            commit_segment_acquired.set()

        monkeypatch.setattr(
            waiter_engine,
            "_acquire_segment_advisory_lock_keys",
            _hold_waiter_segment,
        )
        monkeypatch.setattr(
            commit_engine,
            "_acquire_segment_advisory_lock_keys",
            _observe_commit_segment,
        )

        async def _prepare_waiter():
            try:
                await waiter_engine.prepare(
                    seed["waiter_tx_id"],
                    [seed["sender_pid"], seed["receiver_pid"]],
                    Decimal("8.00"),
                    seed["equivalent_id"],
                )
                return "ok"
            except Exception as exc:
                await waiter_session.rollback()
                return exc

        try:
            waiter_task = asyncio.create_task(_prepare_waiter())
            await asyncio.wait_for(waiter_segment_acquired.wait(), timeout=5.0)

            commit_task = asyncio.create_task(
                commit_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(commit_segment_attempted.wait(), timeout=5.0)
            assert waiter_keys == commit_keys
            assert waiter_keys
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(commit_segment_acquired.wait(), timeout=0.25)
            assert not commit_task.done()

            release_waiter_segment.set()
            waiter_result, commit_result = await asyncio.wait_for(
                asyncio.gather(waiter_task, commit_task),
                timeout=10.0,
            )

            assert isinstance(waiter_result, RoutingException)
            assert waiter_result.code == "E002"
            assert commit_result is True
            assert commit_segment_acquired.is_set()
            exercise_completed = True
        finally:
            release_waiter_segment.set()
            tasks = [task for task in (waiter_task, commit_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await waiter_session.rollback()
                await commit_session.rollback()
                await _cleanup_seed(seed)

    try:
        async with TestingSessionLocal() as verify:
            holder_state = await verify.scalar(
                select(Transaction.state).where(
                    Transaction.tx_id == seed["holder_tx_id"]
                )
            )
            waiter_state = await verify.scalar(
                select(Transaction.state).where(
                    Transaction.tx_id == seed["waiter_tx_id"]
                )
            )
            debt_amount = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == seed["sender_id"],
                    Debt.creditor_id == seed["receiver_id"],
                    Debt.equivalent_id == seed["equivalent_id"],
                )
            )
            lock_count = len(
                (
                    await verify.execute(
                        select(PrepareLock).where(
                            PrepareLock.tx_id.in_(
                                [seed["holder_tx_id"], seed["waiter_tx_id"]]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert holder_state == "COMMITTED"
            assert waiter_state == "NEW"
            assert debt_amount == Decimal("8.00000000")
            assert lock_count == 0
    finally:
        await _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_concurrent_same_transaction_commit_applies_effects_once_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=False)
    holder_at_segment = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_tx_lock_attempted = asyncio.Event()
    waiter_tx_lock_acquired = asyncio.Event()
    holder_task = None
    waiter_task = None
    exercise_completed = False

    async with TestingSessionLocal() as holder_session, TestingSessionLocal() as waiter_session:
        holder_isolation = (
            await holder_session.execute(text("SHOW transaction_isolation"))
        ).scalar_one()
        waiter_isolation = (
            await waiter_session.execute(text("SHOW transaction_isolation"))
        ).scalar_one()
        assert str(holder_isolation).lower() == "serializable"
        assert str(waiter_isolation).lower() == "serializable"
        holder_engine = PaymentEngine(holder_session)
        waiter_engine = PaymentEngine(waiter_session)
        holder_engine._retry_base_delay_s = 0.0
        holder_engine._retry_max_delay_s = 0.0
        waiter_engine._retry_base_delay_s = 0.0
        waiter_engine._retry_max_delay_s = 0.0
        holder_segment_acquire = holder_engine._acquire_segment_advisory_lock_keys
        waiter_execute = waiter_session.execute

        async def _hold_before_segment(keys):
            holder_at_segment.set()
            await release_holder.wait()
            await holder_segment_acquire(keys)

        async def _observe_waiter_tx_lock(statement, *args, **kwargs):
            sql = str(statement).lower()
            is_tx_lock = "pg_advisory_xact_lock" in sql and "namespace" in sql
            if is_tx_lock:
                waiter_tx_lock_attempted.set()
            result = await waiter_execute(statement, *args, **kwargs)
            if is_tx_lock:
                waiter_tx_lock_acquired.set()
            return result

        monkeypatch.setattr(
            holder_engine,
            "_acquire_segment_advisory_lock_keys",
            _hold_before_segment,
        )
        monkeypatch.setattr(waiter_session, "execute", _observe_waiter_tx_lock)

        try:
            holder_task = asyncio.create_task(
                holder_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(holder_at_segment.wait(), timeout=5.0)

            waiter_task = asyncio.create_task(
                waiter_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(waiter_tx_lock_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(waiter_tx_lock_acquired.wait(), timeout=0.25)
            assert not waiter_task.done()

            release_holder.set()
            holder_result, waiter_result = await asyncio.wait_for(
                asyncio.gather(holder_task, waiter_task),
                timeout=15.0,
            )
            assert holder_result is True
            assert waiter_result is True
            assert waiter_tx_lock_acquired.is_set()
            exercise_completed = True
        finally:
            release_holder.set()
            tasks = [task for task in (holder_task, waiter_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await holder_session.rollback()
                await waiter_session.rollback()
                await _cleanup_seed(seed)

    try:
        async with TestingSessionLocal() as verify:
            state = await verify.scalar(
                select(Transaction.state).where(
                    Transaction.tx_id == seed["holder_tx_id"]
                )
            )
            debt_amount = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == seed["sender_id"],
                    Debt.creditor_id == seed["receiver_id"],
                    Debt.equivalent_id == seed["equivalent_id"],
                )
            )
            remaining_locks = (
                await verify.execute(
                    select(PrepareLock).where(
                        PrepareLock.tx_id == seed["holder_tx_id"]
                    )
                )
            ).scalars().all()
            assert state == "COMMITTED"
            assert debt_amount == Decimal("8.00000000")
            assert remaining_locks == []
    finally:
        await _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_concurrent_commit_and_abort_share_segment_lock_protocol_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=False)
    holder_at_segment = asyncio.Event()
    release_holder = asyncio.Event()
    abort_tx_lock_attempted = asyncio.Event()
    abort_tx_lock_acquired = asyncio.Event()
    commit_task = None
    abort_task = None
    exercise_completed = False

    async with TestingSessionLocal() as commit_session, TestingSessionLocal() as abort_session:
        await _use_read_committed(commit_session)
        await _use_read_committed(abort_session)
        commit_engine = PaymentEngine(commit_session)
        abort_engine = PaymentEngine(abort_session)
        commit_segment_acquire = commit_engine._acquire_segment_advisory_lock_keys
        abort_execute = abort_session.execute

        async def _hold_before_segment(keys):
            holder_at_segment.set()
            await release_holder.wait()
            await commit_segment_acquire(keys)

        async def _observe_abort_tx_lock(statement, *args, **kwargs):
            sql = str(statement).lower()
            is_tx_lock = "pg_advisory_xact_lock" in sql and "namespace" in sql
            if is_tx_lock:
                abort_tx_lock_attempted.set()
            result = await abort_execute(statement, *args, **kwargs)
            if is_tx_lock:
                abort_tx_lock_acquired.set()
            return result

        monkeypatch.setattr(
            commit_engine,
            "_acquire_segment_advisory_lock_keys",
            _hold_before_segment,
        )
        monkeypatch.setattr(abort_session, "execute", _observe_abort_tx_lock)

        try:
            commit_task = asyncio.create_task(
                commit_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(holder_at_segment.wait(), timeout=5.0)

            abort_task = asyncio.create_task(
                abort_engine.abort(
                    seed["holder_tx_id"],
                    reason="Concurrent abort lost to commit",
                )
            )
            await asyncio.wait_for(abort_tx_lock_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(abort_tx_lock_acquired.wait(), timeout=0.25)
            assert not abort_task.done()

            release_holder.set()
            commit_result, abort_result = await asyncio.wait_for(
                asyncio.gather(commit_task, abort_task),
                timeout=15.0,
            )
            assert commit_result is True
            assert abort_result is True
            assert abort_tx_lock_acquired.is_set()
            exercise_completed = True
        finally:
            release_holder.set()
            tasks = [task for task in (commit_task, abort_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await commit_session.rollback()
                await abort_session.rollback()
                await _cleanup_seed(seed)

    try:
        async with TestingSessionLocal() as verify:
            tx = (
                await verify.execute(
                    select(Transaction).where(
                        Transaction.tx_id == seed["holder_tx_id"]
                    )
                )
            ).scalar_one()
            debt_amount = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == seed["sender_id"],
                    Debt.creditor_id == seed["receiver_id"],
                    Debt.equivalent_id == seed["equivalent_id"],
                )
            )
            remaining_locks = (
                await verify.execute(
                    select(PrepareLock).where(
                        PrepareLock.tx_id == seed["holder_tx_id"]
                    )
                )
            ).scalars().all()

            assert tx.state == "COMMITTED"
            assert debt_amount == Decimal("8.00000000")
            assert tx.error is None
            assert remaining_locks == []
    finally:
        await _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_duplicate_prepare_cannot_resurrect_transaction_during_commit_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(include_waiter=False)
    duplicate_tx_lock_acquired = asyncio.Event()
    release_duplicate_tx_lock = asyncio.Event()
    commit_tx_lock_attempted = asyncio.Event()
    commit_tx_lock_acquired = asyncio.Event()
    duplicate_task = None
    commit_task = None
    exercise_completed = False

    async with TestingSessionLocal() as duplicate_session, TestingSessionLocal() as commit_session:
        await _use_read_committed(duplicate_session)
        await _use_read_committed(commit_session)
        duplicate_engine = PaymentEngine(duplicate_session)
        commit_engine = PaymentEngine(commit_session)
        duplicate_tx_lock = duplicate_engine._acquire_tx_advisory_lock
        commit_tx_lock = commit_engine._acquire_tx_advisory_lock

        async def _hold_duplicate_tx_lock(tx_id):
            await duplicate_tx_lock(tx_id)
            duplicate_tx_lock_acquired.set()
            await release_duplicate_tx_lock.wait()

        async def _observe_commit_tx_lock(tx_id):
            commit_tx_lock_attempted.set()
            await commit_tx_lock(tx_id)
            commit_tx_lock_acquired.set()

        monkeypatch.setattr(
            duplicate_engine,
            "_acquire_tx_advisory_lock",
            _hold_duplicate_tx_lock,
        )
        monkeypatch.setattr(
            commit_engine,
            "_acquire_tx_advisory_lock",
            _observe_commit_tx_lock,
        )

        try:
            duplicate_task = asyncio.create_task(
                duplicate_engine.prepare(
                    seed["holder_tx_id"],
                    [seed["sender_pid"], seed["receiver_pid"]],
                    Decimal("8.00"),
                    seed["equivalent_id"],
                )
            )
            await asyncio.wait_for(duplicate_tx_lock_acquired.wait(), timeout=5.0)

            commit_task = asyncio.create_task(
                commit_engine.commit(seed["holder_tx_id"])
            )
            await asyncio.wait_for(commit_tx_lock_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(commit_tx_lock_acquired.wait(), timeout=0.25)
            assert not commit_task.done()

            release_duplicate_tx_lock.set()
            duplicate_result, commit_result = await asyncio.wait_for(
                asyncio.gather(duplicate_task, commit_task),
                timeout=15.0,
            )
            assert duplicate_result is True
            assert commit_result is True
            assert commit_tx_lock_acquired.is_set()
            exercise_completed = True
        finally:
            release_duplicate_tx_lock.set()
            tasks = [task for task in (duplicate_task, commit_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await duplicate_session.rollback()
                await commit_session.rollback()
                await _cleanup_seed(seed)

    try:
        async with TestingSessionLocal() as verify:
            tx = await verify.scalar(
                select(Transaction).where(
                    Transaction.tx_id == seed["holder_tx_id"]
                )
            )
            debt_amount = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == seed["sender_id"],
                    Debt.creditor_id == seed["receiver_id"],
                    Debt.equivalent_id == seed["equivalent_id"],
                )
            )
            remaining_locks = await verify.scalars(
                select(PrepareLock).where(
                    PrepareLock.tx_id == seed["holder_tx_id"]
                )
            )
            assert tx is not None and tx.state == "COMMITTED"
            assert debt_amount == Decimal("8.00000000")
            assert remaining_locks.all() == []
    finally:
        await _cleanup_seed(seed)


@pytest.mark.asyncio
async def test_new_prepare_cannot_resurrect_transaction_during_abort_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_prepared_payment(
        include_waiter=False,
        prepare_holder=False,
    )
    participant_read_ready = asyncio.Event()
    release_participant_read = asyncio.Event()
    abort_tx_lock_attempted = asyncio.Event()
    prepare_task = None
    abort_task = None
    exercise_completed = False

    async with TestingSessionLocal() as prepare_session, TestingSessionLocal() as abort_session:
        await _use_read_committed(prepare_session)
        await _use_read_committed(abort_session)
        prepare_execute = prepare_session.execute
        abort_execute = abort_session.execute

        async def _prepare_execute(statement, *args, **kwargs):
            sql = str(statement).lower()
            if "from participants" in sql and "participants.pid" in sql:
                participant_read_ready.set()
                await release_participant_read.wait()
            return await prepare_execute(statement, *args, **kwargs)

        async def _abort_execute(statement, *args, **kwargs):
            sql = str(statement).lower()
            if "pg_advisory_xact_lock" in sql and "namespace" in sql:
                abort_tx_lock_attempted.set()
            return await abort_execute(statement, *args, **kwargs)

        monkeypatch.setattr(prepare_session, "execute", _prepare_execute)
        monkeypatch.setattr(abort_session, "execute", _abort_execute)

        try:
            prepare_task = asyncio.create_task(
                PaymentEngine(prepare_session).prepare(
                    seed["holder_tx_id"],
                    [seed["sender_pid"], seed["receiver_pid"]],
                    Decimal("8.00"),
                    seed["equivalent_id"],
                )
            )
            await asyncio.wait_for(participant_read_ready.wait(), timeout=5.0)

            abort_task = asyncio.create_task(
                PaymentEngine(abort_session).abort(
                    seed["holder_tx_id"],
                    reason="Concurrent abort after prepare",
                )
            )
            await asyncio.wait_for(abort_tx_lock_attempted.wait(), timeout=5.0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(abort_task), timeout=0.25)

            release_participant_read.set()
            prepare_result, abort_result = await asyncio.wait_for(
                asyncio.gather(prepare_task, abort_task),
                timeout=15.0,
            )
            assert prepare_result is True
            assert abort_result is True
            exercise_completed = True
        finally:
            release_participant_read.set()
            tasks = [task for task in (prepare_task, abort_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if not exercise_completed:
                await prepare_session.rollback()
                await abort_session.rollback()
                await _cleanup_seed(seed)

    try:
        async with TestingSessionLocal() as verify:
            tx = await verify.scalar(
                select(Transaction).where(
                    Transaction.tx_id == seed["holder_tx_id"]
                )
            )
            debt_count = await verify.scalar(
                select(func.count()).select_from(Debt).where(
                    Debt.equivalent_id == seed["equivalent_id"]
                )
            )
            remaining_locks = await verify.scalars(
                select(PrepareLock).where(
                    PrepareLock.tx_id == seed["holder_tx_id"]
                )
            )
            assert tx is not None and tx.state == "ABORTED"
            assert (tx.error or {}).get("message") == "Concurrent abort after prepare"
            assert debt_count == 0
            assert remaining_locks.all() == []
    finally:
        await _cleanup_seed(seed)
