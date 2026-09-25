"""PostgreSQL request-level payment idempotency races.

SINCE 019 STAGE 3 (`T1904`) a payment is ONE transaction: its `Transaction` row is not visible, and
not committed, before the payment's single commit. So the second of two concurrent requests with the
same `tx_id` no longer meets a committed `NEW` row and a "payment is in progress" 409 (the test's
assertion until stage 3): its insert WAITS on the first's uncommitted unique-index entry, and once the
first commits it gets the first's stored result (spec, "Идентичность tx_id"; §3 (В)). The terminal
state never regresses, and the effects exist once.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

# Every test here commits through several sessions and runs on a disposable clone of the migrated
# template; its rows go with the clone's drop and nothing is deleted row by row (018 B0b; see
# `tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture



async def _a_backend_waits_on_a_transaction(*, timeout: float = 5.0) -> bool:
    """Some backend waits (not granted) on a transaction lock - an insert behind an uncommitted key."""

    from tests.conftest import TestingSessionLocal

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with TestingSessionLocal() as observer:
        while True:
            waiting = await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted "
                    "AND locktype = 'transactionid')"
                )
            )
            await observer.rollback()
            if waiting:
                return True
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)


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
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.utils.event_bus import event_bus
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
    winner_row_inserted = asyncio.Event()
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
                execution_options={"isolation_level": "SERIALIZABLE"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "serializable"  # 019 stage 5 (T1907): the only supported level

        loser_service = PaymentService(loser_session)
        winner_service = PaymentService(winner_session)
        loser_build_graph = loser_service.router.build_graph
        winner_prepare = winner_service._bind_payment  # the binding phase (019 stage 4)

        async def _hold_loser_after_initial_lookup(*args, **kwargs):
            graph = await loser_build_graph(*args, **kwargs)
            loser_passed_initial_lookup.set()
            await release_loser.wait()
            return graph

        async def _hold_winner_after_its_row_is_inserted(*args, **kwargs):
            # Since stage 3 the row is inserted - not committed - inside the payment's transaction.
            winner_row_inserted.set()
            await release_winner.wait()
            return await winner_prepare(*args, **kwargs)

        monkeypatch.setattr(
            loser_service.router,
            "build_graph",
            _hold_loser_after_initial_lookup,
        )
        monkeypatch.setattr(
            winner_service,
            "_bind_payment",
            _hold_winner_after_its_row_is_inserted,
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
        await asyncio.wait_for(winner_row_inserted.wait(), timeout=5.0)
        assert not winner_task.done()

        # The loser's insert queues on the winner's uncommitted unique-index entry - it is neither
        # answered "in progress" nor allowed through.
        release_loser.set()
        assert await _a_backend_waits_on_a_transaction(), (
            "premise: the second request did not wait on the first one's uncommitted row"
        )
        assert not loser_task.done()

        release_winner.set()
        winner_result = await asyncio.wait_for(winner_task, timeout=10.0)
        loser_result = await asyncio.wait_for(loser_task, timeout=10.0)
        assert not isinstance(winner_result, Exception), repr(winner_result)
        assert winner_result.status == "COMMITTED"
        # The second request is answered with the first one's stored result.
        assert not isinstance(loser_result, Exception), repr(loser_result)
        assert loser_result.status == "COMMITTED"
        assert loser_result.tx_id == winner_result.tx_id == tx_id

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
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Idempotency test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
