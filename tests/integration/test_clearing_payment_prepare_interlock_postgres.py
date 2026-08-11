"""PostgreSQL schedules for the shared clearing/payment prepare boundary."""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text


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
    for _ in range(300):
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
        await asyncio.sleep(0.01)
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
@pytest.mark.xfail(
    reason="P200: clearing does not yet share the payment equivalent-owner lock",
    strict=True,
)
async def test_clearing_owner_blocks_new_reverse_prepare_after_empty_snapshot_postgres(
    db_session,
    monkeypatch,
):
    """Clearing-first: prepare cannot cross an already-empty conflict decision."""

    _require_postgres(db_session)

    from app.core.clearing.service import ClearingService
    from app.core.payments.engine import PaymentEngine
    from app.db.models.debt import Debt
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
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

        assert payment_tx is not None and payment_tx.state == "PREPARED"
        assert len(locks) == 1
        assert debts == {
            seed["debt_ids"][0]: (Decimal("70.00000000"), 2),
            seed["debt_ids"][2]: (Decimal("10.00000000"), 2),
        }
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
@pytest.mark.xfail(
    reason="P200: clearing does not yet share the payment equivalent-owner lock",
    strict=True,
)
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
        clearing_pid = await _use_serializable(clearing_session)
        await _use_serializable(payment_session)

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
        assert await _wait_for_advisory_wait(
            observer_session,
            backend_pid=clearing_pid,
        ), "clearing bypassed the uncommitted reverse prepare owner"
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

        assert payment_tx is not None and payment_tx.state == "PREPARED"
        assert len(locks) == 1
        assert clearing_transactions == []
        assert clearing_audits == []
        assert debts == {
            seed["debt_ids"][0]: (Decimal("100.00000000"), 1),
            seed["debt_ids"][1]: (Decimal("30.00000000"), 1),
            seed["debt_ids"][2]: (Decimal("40.00000000"), 1),
        }
    finally:
        primary_error = sys.exc_info()[1]
        try:
            if clearing_task is not None and not clearing_task.done():
                clearing_task.cancel()
                await asyncio.wait([clearing_task], timeout=2.0)
            if clearing_task is not None and clearing_task.done() and not clearing_task.cancelled():
                clearing_task.exception()
            for session in (clearing_session, payment_session, observer_session):
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
