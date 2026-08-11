"""PostgreSQL schedules for the shared clearing/payment prepare boundary."""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


pytestmark = pytest.mark.postgres


def _require_postgres(db_session) -> None:
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: clearing/payment advisory interlock")


async def _use_serializable(session) -> int:
    await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
    isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    assert str(isolation).lower() == "serializable"
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


async def _wait_for_advisory_wait(observer, *, backend_pid: int) -> bool:
    try:
        async with asyncio.timeout(3.0):
            while True:
                waiting = await observer.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_locks "
                        "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted"
                        ")"
                    ),
                    {"pid": backend_pid},
                )
                if waiting:
                    return True
    except asyncio.TimeoutError:
        return False


async def _wait_for_matching_advisory_wait(
    observer,
    *,
    holder_pid: int,
) -> bool:
    try:
        async with asyncio.timeout(3.0):
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
                        "WHERE holder.pid = :holder_pid "
                        "AND holder.locktype = 'advisory' AND holder.granted "
                        "AND waiter.pid <> holder.pid AND NOT waiter.granted"
                        ")"
                    ),
                    {"holder_pid": holder_pid},
                )
                if waiting:
                    return True
    except asyncio.TimeoutError:
        return False


async def _wait_for_exact_blocker(
    observer,
    *,
    waiter_pid: int,
    holder_pid: int,
) -> bool:
    try:
        async with asyncio.timeout(3.0):
            while True:
                blockers = await observer.scalar(
                    text("SELECT pg_blocking_pids(:waiter_pid)"),
                    {"waiter_pid": waiter_pid},
                )
                if holder_pid in (blockers or []):
                    return True
    except asyncio.TimeoutError:
        return False


async def _seed_interlock_case():
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"PI{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    participant_pids = [f"{label}_PI_{nonce}" for label in ("A", "B", "C")]
    a_id, b_id, c_id = participant_ids
    a_pid, b_pid, _c_pid = participant_pids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    payment_tx_id = str(uuid.uuid4())

    async with TestingSessionLocal() as setup:
        setup.add(
            Equivalent(
                id=equivalent_id,
                code=equivalent_code,
                description="Clearing/payment prepare interlock test",
                precision=2,
            )
        )
        setup.add_all(
            [
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
                    # Reverse B -> A payment capacity is controlled by A -> B.
                    (b_id, a_id),
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
        setup.add(
            Transaction(
                id=uuid.UUID(payment_tx_id),
                tx_id=payment_tx_id,
                idempotency_key=payment_tx_id,
                type="PAYMENT",
                initiator_id=a_id,
                payload={
                    "from": a_pid,
                    "to": b_pid,
                    "amount": "5.00",
                    "equivalent": equivalent_code,
                },
                state="NEW",
            )
        )
        await setup.commit()

    return {
        "equivalent_id": equivalent_id,
        "equivalent_code": equivalent_code,
        "participant_ids": participant_ids,
        "participant_pids": participant_pids,
        "debt_ids": debt_ids,
        "cycle": [{"debt_id": str(debt_id)} for debt_id in debt_ids],
        "payment_tx_id": payment_tx_id,
    }


async def _cleanup_interlock_case(seed) -> None:
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as cleanup:
        await cleanup.execute(
            delete(IntegrityAuditLog).where(
                IntegrityAuditLog.equivalent_code == seed["equivalent_code"]
            )
        )
        await cleanup.execute(
            delete(PrepareLock).where(
                PrepareLock.participant_id.in_(seed["participant_ids"])
            )
        )
        await cleanup.execute(
            delete(Transaction).where(
                Transaction.initiator_id.in_(seed["participant_ids"])
            )
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
            delete(Participant).where(Participant.id.in_(seed["participant_ids"]))
        )
        await cleanup.execute(
            delete(Equivalent).where(Equivalent.id == seed["equivalent_id"])
        )
        await cleanup.commit()


@pytest.mark.asyncio
async def test_clearing_owner_blocks_new_reverse_prepare_after_empty_snapshot_postgres(
    db_session,
    monkeypatch,
):
    """Clearing-first: prepare cannot cross an already-empty conflict decision."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from app.core.payments.engine import PaymentEngine
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = None
    payment_session = None
    observer_session = None
    clearing_task = None
    payment_task = None
    empty_snapshot_seen = asyncio.Event()
    release_clearing = asyncio.Event()

    try:
        clearing_session = TestingSessionLocal()
        payment_session = TestingSessionLocal()
        observer_session = TestingSessionLocal()
        await _use_serializable(clearing_session)
        payment_pid = await _use_serializable(payment_session)

        clearing_service = ClearingService(clearing_session)
        original_locked_pairs = clearing_service._locked_pairs_for_equivalent

        async def _pause_after_empty_snapshot(equivalent_id):
            isolation = await clearing_service.session.scalar(
                text("SHOW transaction_isolation")
            )
            assert str(isolation).lower() == "serializable"
            locked_pairs = await original_locked_pairs(equivalent_id)
            assert locked_pairs == set()
            empty_snapshot_seen.set()
            await release_clearing.wait()
            return locked_pairs

        monkeypatch.setattr(
            clearing_service,
            "_locked_pairs_for_equivalent",
            _pause_after_empty_snapshot,
        )
        clearing_task = asyncio.create_task(
            clearing_service.execute_clearing_with_amount(seed["cycle"]),
            name="clearing-first-owner",
        )
        await asyncio.wait_for(empty_snapshot_seen.wait(), timeout=5.0)

        payment_task = asyncio.create_task(
            PaymentEngine(payment_session).prepare(
                seed["payment_tx_id"],
                list(reversed(seed["participant_pids"][:2])),
                Decimal("5.00"),
                seed["equivalent_id"],
                commit=True,
            ),
            name="reverse-prepare-waiter",
        )
        assert await _wait_for_advisory_wait(
            observer_session,
            backend_pid=payment_pid,
        ), "reverse prepare crossed clearing's empty conflict decision"
        assert not payment_task.done()

        release_clearing.set()
        cleared_amount, prepared = await asyncio.wait_for(
            asyncio.gather(clearing_task, payment_task),
            timeout=15.0,
        )
        assert cleared_amount == Decimal("30.00000000")
        assert prepared is True

        async with TestingSessionLocal() as verify:
            payment_tx = await verify.scalar(
                select(Transaction).where(
                    Transaction.tx_id == seed["payment_tx_id"]
                )
            )
            locks = (
                await verify.scalars(
                    select(PrepareLock).where(
                        PrepareLock.tx_id == seed["payment_tx_id"]
                    )
                )
            ).all()
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.equivalent_code
                        == seed["equivalent_code"]
                    )
                )
            ).all()
            debts = {
                debt.id: (debt.amount, debt.version)
                for debt in (
                    await verify.scalars(
                        select(Debt).where(
                            Debt.equivalent_id == seed["equivalent_id"]
                        )
                    )
                ).all()
            }
            trust_limits = (
                await verify.scalars(
                    select(TrustLine.limit).where(
                        TrustLine.equivalent_id == seed["equivalent_id"]
                    )
                )
            ).all()

        assert payment_tx is not None and payment_tx.state == "PREPARED"
        assert len(locks) == 1
        assert len(clearing_transactions) == 1
        clearing_tx = clearing_transactions[0]
        assert clearing_tx.state == "COMMITTED"
        assert Decimal(str(clearing_tx.payload["amount"])) == Decimal("30.00000000")
        assert set(clearing_tx.payload["cycle"]) == {
            str(debt_id) for debt_id in seed["debt_ids"]
        }
        assert {
            (audit.operation_type, audit.tx_id, audit.verification_passed)
            for audit in audits
        } == {("CLEARING", clearing_tx.tx_id, True)}
        assert debts == {
            seed["debt_ids"][0]: (Decimal("70.00000000"), 2),
            seed["debt_ids"][2]: (Decimal("10.00000000"), 2),
        }
        assert trust_limits == [Decimal("200.00000000")] * 4
    finally:
        primary_error = sys.exc_info()[1]
        release_clearing.set()
        try:
            tasks = [
                task for task in (clearing_task, payment_task) if task is not None
            ]
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2.0)
            for task in tasks:
                if task.done() and not task.cancelled():
                    task.exception()
            for session in (clearing_session, payment_session, observer_session):
                if session is not None:
                    await session.rollback()
                    await session.close()
            await _cleanup_interlock_case(seed)
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Clearing-first interlock teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


@pytest.mark.asyncio
async def test_uncommitted_reverse_prepare_blocks_clearing_until_visible_postgres(
    db_session,
):
    """Payment-first: clearing waits, then observes the committed PrepareLock."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from app.core.payments.engine import PaymentEngine
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = None
    payment_session = None
    observer_session = None
    clearing_task = None

    try:
        clearing_session = TestingSessionLocal()
        payment_session = TestingSessionLocal()
        observer_session = TestingSessionLocal()
        await _use_serializable(clearing_session)
        payment_pid = await _use_serializable(payment_session)

        prepared = await PaymentEngine(payment_session).prepare(
            seed["payment_tx_id"],
            list(reversed(seed["participant_pids"][:2])),
            Decimal("5.00"),
            seed["equivalent_id"],
            commit=False,
        )
        assert prepared is True
        assert payment_session.in_transaction()

        clearing_task = asyncio.create_task(
            ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            ),
            name="clearing-after-uncommitted-prepare",
        )
        assert await _wait_for_matching_advisory_wait(
            observer_session,
            holder_pid=payment_pid,
        ), "clearing did not wait on the prepared payment's exact owner lock"
        assert not clearing_task.done()

        await payment_session.commit()
        cleared_amount = await asyncio.wait_for(clearing_task, timeout=10.0)
        assert cleared_amount is None
        assert not clearing_session.in_transaction()

        async with TestingSessionLocal() as verify:
            payment_tx = await verify.scalar(
                select(Transaction).where(
                    Transaction.tx_id == seed["payment_tx_id"]
                )
            )
            locks = (
                await verify.scalars(
                    select(PrepareLock).where(
                        PrepareLock.tx_id == seed["payment_tx_id"]
                    )
                )
            ).all()
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
            clearing_audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.operation_type == "CLEARING",
                        IntegrityAuditLog.equivalent_code
                        == seed["equivalent_code"],
                    )
                )
            ).all()
            all_boundary_audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.equivalent_code
                        == seed["equivalent_code"]
                    )
                )
            ).all()
            debts = {
                debt.id: (debt.amount, debt.version)
                for debt in (
                    await verify.scalars(
                        select(Debt).where(
                            Debt.equivalent_id == seed["equivalent_id"]
                        )
                    )
                ).all()
            }
            trust_limits = (
                await verify.scalars(
                    select(TrustLine.limit).where(
                        TrustLine.equivalent_id == seed["equivalent_id"]
                    )
                )
            ).all()

        assert payment_tx is not None and payment_tx.state == "PREPARED"
        assert len(locks) == 1
        assert clearing_transactions == []
        assert clearing_audits == []
        assert all_boundary_audits == []
        assert debts == {
            seed["debt_ids"][0]: (Decimal("100.00000000"), 1),
            seed["debt_ids"][1]: (Decimal("30.00000000"), 1),
            seed["debt_ids"][2]: (Decimal("40.00000000"), 1),
        }
        assert trust_limits == [Decimal("200.00000000")] * 4
    finally:
        primary_error = sys.exc_info()[1]
        try:
            if clearing_task is not None and not clearing_task.done():
                clearing_task.cancel()
                await asyncio.wait([clearing_task], timeout=2.0)
            if clearing_task is not None and clearing_task.done() and not clearing_task.cancelled():
                clearing_task.exception()
            for session in (
                clearing_session,
                payment_session,
                observer_session,
            ):
                if session is not None:
                    await session.rollback()
                    await session.close()
            await _cleanup_interlock_case(seed)
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Payment-first interlock teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


@pytest.mark.asyncio
async def test_clearing_interlock_completes_with_single_connection_pool_postgres(
    db_session,
):
    """The shared boundary must not require two simultaneous pool connections."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from tests.conftest import TEST_DATABASE_URL

    seed = await _seed_interlock_case()
    one_connection_engine = create_async_engine(
        TEST_DATABASE_URL,
        isolation_level="SERIALIZABLE",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
    )
    sessions = async_sessionmaker(
        bind=one_connection_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    clearing_session = sessions()
    try:
        amount = await asyncio.wait_for(
            ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            ),
            timeout=3.0,
        )
        assert amount == Decimal("30.00000000")
        assert not clearing_session.in_transaction()
    finally:
        await clearing_session.rollback()
        await clearing_session.close()
        await one_connection_engine.dispose()
        await _cleanup_interlock_case(seed)


@pytest.mark.asyncio
async def test_cancellation_during_interlocked_work_rolls_back_before_unlock_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from app.core.payments.engine import PaymentEngine
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    probe_session = None
    task = None
    work_entered = asyncio.Event()
    never_release = asyncio.Event()
    service = ClearingService(clearing_session)
    original_locked_pairs = service._locked_pairs_for_equivalent

    async def _pause_inside_money_uow(equivalent_id):
        locked_pairs = await original_locked_pairs(equivalent_id)
        work_entered.set()
        await never_release.wait()
        return locked_pairs

    monkeypatch.setattr(
        service,
        "_locked_pairs_for_equivalent",
        _pause_inside_money_uow,
    )
    try:
        task = asyncio.create_task(service.execute_clearing_with_amount(seed["cycle"]))
        await asyncio.wait_for(work_entered.wait(), timeout=5.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)

        assert not clearing_session.in_transaction()
        async with TestingSessionLocal() as verify:
            final_debts = {
                debt.id: (debt.amount, debt.version)
                for debt in (
                    await verify.scalars(
                        select(Debt).where(
                            Debt.equivalent_id == seed["equivalent_id"]
                        )
                    )
                ).all()
            }
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.equivalent_code
                        == seed["equivalent_code"]
                    )
                )
            ).all()
        assert final_debts == {
            seed["debt_ids"][0]: (Decimal("100.00000000"), 1),
            seed["debt_ids"][1]: (Decimal("30.00000000"), 1),
            seed["debt_ids"][2]: (Decimal("40.00000000"), 1),
        }
        assert clearing_transactions == []
        assert audits == []
        probe_session = TestingSessionLocal()
        await asyncio.wait_for(
            PaymentEngine(probe_session).acquire_staged_equivalent_owner_locks(
                [seed["equivalent_id"]]
            ),
            timeout=2.0,
        )
    finally:
        never_release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2.0)
        for session in (clearing_session, probe_session):
            if session is not None:
                await session.rollback()
                await session.close()
        await _cleanup_interlock_case(seed)


@pytest.mark.asyncio
async def test_cancellation_during_preflight_select_rolls_back_caller_postgres(
    db_session,
):
    """Cancellation before pinned ownership must still end the caller UoW."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from tests.conftest import TestingSessionLocal

    clearing_session = TestingSessionLocal()
    holder_session = TestingSessionLocal()
    observer_session = TestingSessionLocal()
    task = None
    try:
        clearing_pid = await _use_serializable(clearing_session)
        holder_pid = await _use_serializable(holder_session)
        await holder_session.execute(text("LOCK TABLE debts IN ACCESS EXCLUSIVE MODE"))

        task = asyncio.create_task(
            ClearingService(clearing_session).execute_clearing_with_amount(
                [{"debt_id": str(uuid.uuid4())}]
            )
        )
        assert await _wait_for_exact_blocker(
            observer_session,
            waiter_pid=clearing_pid,
            holder_pid=holder_pid,
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)
        assert not clearing_session.in_transaction()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2.0)
        for session in (clearing_session, holder_session, observer_session):
            await session.rollback()
            await session.close()


@pytest.mark.asyncio
async def test_cancellation_during_interlock_release_preserves_durable_amount_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from app.core.clearing.service import (
        ClearingCommittedAfterCancellation,
        ClearingService,
    )
    from app.core.payments.engine import PaymentEngine
    from app.db.models.transaction import Transaction
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    clearing_session = TestingSessionLocal()
    probe_session = None
    task = None
    cleanup_entered = asyncio.Event()
    hold_cleanup = asyncio.Event()
    original_release = ClearingService._release_interlock_session

    async def _hold_after_durable_work(*args, **kwargs):
        cleanup_task = asyncio.create_task(original_release(*args, **kwargs))
        cleanup_entered.set()
        try:
            await hold_cleanup.wait()
        except asyncio.CancelledError:
            await asyncio.shield(cleanup_task)
            raise
        return await cleanup_task

    monkeypatch.setattr(
        ClearingService,
        "_release_interlock_session",
        staticmethod(_hold_after_durable_work),
    )
    try:
        task = asyncio.create_task(
            ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            )
        )
        await asyncio.wait_for(cleanup_entered.wait(), timeout=5.0)
        task.cancel()
        with pytest.raises(ClearingCommittedAfterCancellation) as committed:
            await asyncio.wait_for(task, timeout=5.0)
        assert committed.value.cleared_amount == Decimal("30.00000000")

        async with TestingSessionLocal() as verify:
            transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.type == "CLEARING",
                        Transaction.initiator_id.in_(seed["participant_ids"]),
                    )
                )
            ).all()
        assert len(transactions) == 1
        assert transactions[0].state == "COMMITTED"
        probe_session = TestingSessionLocal()
        await asyncio.wait_for(
            PaymentEngine(probe_session).acquire_staged_equivalent_owner_locks(
                [seed["equivalent_id"]]
            ),
            timeout=2.0,
        )
    finally:
        hold_cleanup.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2.0)
        await clearing_session.rollback()
        await clearing_session.close()
        if probe_session is not None:
            await probe_session.rollback()
            await probe_session.close()
        await _cleanup_interlock_case(seed)


@pytest.mark.asyncio
async def test_interlock_timeout_rolls_back_work_and_releases_owner_postgres(
    db_session,
    monkeypatch,
):
    _require_postgres(db_session)

    from app.config import settings
    from app.core.clearing.service import ClearingService
    from app.core.payments.engine import PaymentEngine
    from app.utils.exceptions import TimeoutException
    from tests.conftest import TestingSessionLocal

    seed = await _seed_interlock_case()
    holder_session = TestingSessionLocal()
    clearing_session = TestingSessionLocal()
    retry_session = None
    original_commit_timeout = settings.COMMIT_TIMEOUT_SECONDS
    original_total_timeout = settings.PAYMENT_TOTAL_TIMEOUT_SECONDS
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 0.05)
    try:
        await PaymentEngine(holder_session).acquire_staged_equivalent_owner_locks(
            [seed["equivalent_id"]]
        )
        with pytest.raises(TimeoutException):
            await ClearingService(clearing_session).execute_clearing_with_amount(
                seed["cycle"]
            )
        assert not clearing_session.in_transaction()

        await holder_session.rollback()
        monkeypatch.setattr(
            settings,
            "COMMIT_TIMEOUT_SECONDS",
            original_commit_timeout,
        )
        monkeypatch.setattr(
            settings,
            "PAYMENT_TOTAL_TIMEOUT_SECONDS",
            original_total_timeout,
        )
        retry_session = TestingSessionLocal()
        amount = await asyncio.wait_for(
            ClearingService(retry_session).execute_clearing_with_amount(seed["cycle"]),
            timeout=3.0,
        )
        assert amount == Decimal("30.00000000")
    finally:
        for session in (holder_session, clearing_session, retry_session):
            if session is not None:
                await session.rollback()
                await session.close()
        await _cleanup_interlock_case(seed)
