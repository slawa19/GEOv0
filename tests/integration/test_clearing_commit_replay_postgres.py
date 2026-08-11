"""PostgreSQL clearing commit-confirmation and deterministic replay coverage."""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker


pytestmark = pytest.mark.postgres


async def _wait_for_matching_advisory_wait(
    observer,
    *,
    holder_pid: int,
    waiter_pid: int,
) -> bool:
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
                        "WHERE holder.pid = :holder_pid "
                        "AND holder.locktype = 'advisory' AND holder.granted "
                        "AND holder.database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database()) "
                        "AND waiter.pid = :waiter_pid AND NOT waiter.granted"
                        ")"
                    ),
                    {
                        "holder_pid": holder_pid,
                        "waiter_pid": waiter_pid,
                    },
                )
                if waiting:
                    return True
    except asyncio.TimeoutError:
        return False


@pytest.mark.asyncio
async def test_concurrent_same_cycle_serializable_resolves_one_durable_occurrence_postgres(
    db_session,
    monkeypatch,
):
    """Equivalent ownership serializes one occurrence and its durable replay."""

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: SERIALIZABLE clearing reconciliation")

    from app.core.clearing.service import ClearingService
    from app.core.payments.engine import PaymentEngine
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal, engine as test_engine

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CC{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]
    sessions = []
    workers = []
    observer = None

    try:
        async with TestingSessionLocal() as setup:
            setup.add(
                Equivalent(
                    id=equivalent_id,
                    code=equivalent_code,
                    description="Concurrent clearing replay test",
                    precision=2,
                )
            )
            setup.add_all(
                [
                    Participant(
                        id=participant_id,
                        pid=f"{label}_CC_{nonce}",
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

        first_owner_acquired = asyncio.Event()
        second_owner_attempted = asyncio.Event()
        release_first_owner = asyncio.Event()
        acquisition_count = 0
        acquisition_pids: dict[int, int] = {}
        original_acquire = PaymentEngine.acquire_session_equivalent_owner_lock

        async def _coordinate_owner_acquisition(engine, equivalent_id):
            nonlocal acquisition_count
            acquisition_count += 1
            call_number = acquisition_count
            acquisition_pids[call_number] = int(
                await engine.session.scalar(text("SELECT pg_backend_pid()"))
            )
            if call_number == 2:
                second_owner_attempted.set()
            result = await original_acquire(engine, equivalent_id)
            if call_number == 1:
                first_owner_acquired.set()
                await release_first_owner.wait()
            return result

        monkeypatch.setattr(
            PaymentEngine,
            "acquire_session_equivalent_owner_lock",
            _coordinate_owner_acquisition,
        )

        serializable_sessions = async_sessionmaker(
            bind=test_engine.execution_options(isolation_level="SERIALIZABLE"),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        for _ in range(2):
            session = serializable_sessions()
            sessions.append(session)
            await session.connection(
                execution_options={"isolation_level": "SERIALIZABLE"}
            )
            isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
            assert str(isolation).lower() == "serializable"
        observer = TestingSessionLocal()

        workers = [
            asyncio.create_task(
                ClearingService(session).execute_clearing_with_amount(
                    ordered_cycle
                )
            )
            for session, ordered_cycle in zip(
                sessions,
                (cycle, list(reversed(cycle))),
                strict=True,
            )
        ]
        await asyncio.wait_for(first_owner_acquired.wait(), timeout=5.0)
        await asyncio.wait_for(second_owner_attempted.wait(), timeout=5.0)
        assert await _wait_for_matching_advisory_wait(
            observer,
            holder_pid=acquisition_pids[1],
            waiter_pid=acquisition_pids[2],
        )
        release_first_owner.set()
        results = await asyncio.wait_for(
            asyncio.gather(*workers, return_exceptions=True),
            timeout=15.0,
        )

        assert results == [Decimal("30.00000000"), Decimal("30.00000000")]
        assert acquisition_count == 2
        assert all(not session.in_transaction() for session in sessions)

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

        assert len(clearing_transactions) == 1
        assert clearing_transactions[0].state == "COMMITTED"
        assert len(clearing_audits) == 1
        assert remaining_debts == {
            debt_ids[0]: Decimal("70.00000000"),
            debt_ids[2]: Decimal("10.00000000"),
        }
    finally:
        primary_error = sys.exc_info()[1]
        try:
            pending = [worker for worker in workers if not worker.done()]
            for worker in pending:
                worker.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2.0)
            for worker in workers:
                if worker.done() and not worker.cancelled():
                    worker.exception()

            async with asyncio.timeout(5.0):
                for session in sessions:
                    await session.rollback()
                    await session.close()
                if observer is not None:
                    await observer.rollback()
                    await observer.close()
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
                "Concurrent clearing replay teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


@pytest.mark.asyncio
async def test_serializable_conflict_without_committed_occurrence_stays_failure_postgres(
    db_session,
):
    """A real 40001 must not become success without the deterministic transaction."""

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: SERIALIZABLE clearing reconciliation")

    from app.core.clearing.service import ClearingService
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.utils.exceptions import GeoException
    from tests.conftest import TestingSessionLocal, engine as test_engine

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CN{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]
    execution_tx_id = ClearingService._execution_tx_id(debt_ids)
    owner_session = None
    owner_task = None
    snapshot_fixed = asyncio.Event()
    release_writer = asyncio.Event()
    observed_retryable_sqlstates: list[str] = []

    try:
        async with TestingSessionLocal() as setup:
            setup.add(
                Equivalent(
                    id=equivalent_id,
                    code=equivalent_code,
                    description="Unmatched serialization conflict test",
                    precision=2,
                )
            )
            setup.add_all(
                [
                    Participant(
                        id=participant_id,
                        pid=f"{label}_CN_{nonce}",
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

        class _ObservedClearingService(ClearingService):
            def __init__(self, session):
                super().__init__(session)
                self._pause_initial_replay = True

            async def _committed_execution_amount(self, tx_id: str):
                amount = await super()._committed_execution_amount(tx_id)
                if self._pause_initial_replay:
                    self._pause_initial_replay = False
                    assert amount is None
                    snapshot_fixed.set()
                    await release_writer.wait()
                return amount

            def _is_retryable_concurrency_error(self, exc: BaseException) -> bool:
                is_retryable = super()._is_retryable_concurrency_error(exc)
                if is_retryable:
                    pending = [exc]
                    seen: set[int] = set()
                    while pending:
                        current = pending.pop()
                        if id(current) in seen:
                            continue
                        seen.add(id(current))
                        for attr in ("sqlstate", "pgcode", "code"):
                            value = getattr(current, attr, None)
                            if str(value or "").strip() in {"40001", "40P01"}:
                                observed_retryable_sqlstates.append(str(value))
                        for linked in (
                            getattr(current, "orig", None),
                            current.__cause__,
                            current.__context__,
                        ):
                            if isinstance(linked, BaseException):
                                pending.append(linked)
                return is_retryable

        serializable_sessions = async_sessionmaker(
            bind=test_engine.execution_options(isolation_level="SERIALIZABLE"),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        owner_session = serializable_sessions()
        await owner_session.connection(
            execution_options={"isolation_level": "SERIALIZABLE"}
        )
        isolation = (
            await owner_session.execute(text("SHOW transaction_isolation"))
        ).scalar_one()
        assert str(isolation).lower() == "serializable"
        service = _ObservedClearingService(owner_session)

        # Establish the fresh post-interlock work snapshot, then let a writer
        # change a Debt without creating the deterministic clearing occurrence.
        owner_task = asyncio.create_task(service.execute_clearing_with_amount(cycle))
        await asyncio.wait_for(snapshot_fixed.wait(), timeout=5.0)
        async with TestingSessionLocal() as writer:
            debt = await writer.get(Debt, debt_ids[0])
            assert debt is not None
            debt.amount = Decimal("101.00")
            await writer.commit()
        release_writer.set()

        with pytest.raises(GeoException) as failure:
            await asyncio.wait_for(owner_task, timeout=10.0)

        assert failure.value.code == "E010"
        assert observed_retryable_sqlstates
        assert set(observed_retryable_sqlstates) == {"40001"}
        assert not owner_session.in_transaction()

        async with TestingSessionLocal() as verify:
            clearing_transactions = (
                await verify.scalars(
                    select(Transaction).where(
                        Transaction.tx_id == execution_tx_id,
                        Transaction.type == "CLEARING",
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

        assert clearing_transactions == []
        assert clearing_audits == []
        assert remaining_debts == {
            debt_ids[0]: Decimal("101.00000000"),
            debt_ids[1]: Decimal("30.00000000"),
            debt_ids[2]: Decimal("40.00000000"),
        }
    finally:
        primary_error = sys.exc_info()[1]
        try:
            release_writer.set()
            if owner_task is not None and not owner_task.done():
                owner_task.cancel()
                await asyncio.wait([owner_task], timeout=2.0)
            if owner_task is not None and owner_task.done() and not owner_task.cancelled():
                owner_task.exception()
            if owner_session is not None:
                await owner_session.rollback()
                await owner_session.close()
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
                "Unmatched serialization-conflict teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary_kind",
    ["cancellation", "ack_loss", "connection_loss"],
)
async def test_post_commit_boundary_reconciles_and_new_cycle_still_executes_postgres(
    db_session,
    monkeypatch,
    boundary_kind,
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
            execution_options={"isolation_level": "SERIALIZABLE"}
        )
        service = ClearingService(service_session)
        real_commit = AsyncSession.commit
        boundary_commit_seen = False

        async def _commit_then_delay_ack(session):
            nonlocal boundary_commit_seen
            await real_commit(session)
            if boundary_commit_seen:
                return
            boundary_commit_seen = True
            commit_completed.set()
            if boundary_kind == "ack_loss":
                raise RuntimeError("commit acknowledgement lost")
            if boundary_kind == "connection_loss":
                bind = session.bind
                assert isinstance(bind, AsyncConnection)
                await bind.invalidate()
                raise ConnectionError("connection lost after commit")
            await release_commit_ack.wait()

        monkeypatch.setattr(AsyncSession, "commit", _commit_then_delay_ack)

        service_task = asyncio.create_task(
            service.execute_clearing_with_amount(cycle),
            name="clearing-post-commit-cancellation",
        )
        await asyncio.wait_for(commit_completed.wait(), timeout=5.0)
        if boundary_kind == "cancellation":
            service_task.cancel()
            await asyncio.sleep(0)
            release_commit_ack.set()
            with pytest.raises(ClearingCommittedAfterCancellation) as cancellation:
                await asyncio.wait_for(service_task, timeout=5.0)
            boundary_amount = cancellation.value.cleared_amount
        else:
            boundary_amount = await asyncio.wait_for(service_task, timeout=5.0)

        replay_session = TestingSessionLocal()
        await replay_session.connection(
            execution_options={"isolation_level": "SERIALIZABLE"}
        )
        replay_amount = await ClearingService(
            replay_session
        ).execute_clearing_with_amount(list(reversed(cycle)))

        assert replay_amount == Decimal("30.00000000")
        assert boundary_amount == replay_amount
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
