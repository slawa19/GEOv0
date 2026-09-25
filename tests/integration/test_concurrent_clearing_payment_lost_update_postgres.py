"""PostgreSQL clearing/payment contention on one trustline.

019 stage 5 (`T1909`, `KEEP-EQUIVALENT-LOCK`): the clearing holds the ONE equivalent lock EXCLUSIVELY on its
pinned connection, a payment takes it SHARED. The schedule parks the clearing inside its money transaction
(`_cycle_respects_auto_clearing`, cycle rows `FOR UPDATE`, nothing mutated - the removed reservation scan
was the park point until then) and asserts that the payment's shared acquisition is queued behind that
exact backend (`pg_locks`: `ShareLock` waiting, `ExclusiveLock` held, `pg_blocking_pids`). The effects
asserted afterwards - final debts and versions, one clearing, the audits, one publication - are the
contract and are unchanged. The lock-free variant of the same race (40001 and the retry of both owners)
is `tests/integration/test_p019_t1908_lock_removal_experiments_postgres.py`.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.config import settings
from tests.debt_setup import debt_fixture_setup

# Every test here commits through several sessions and runs on a disposable clone of the migrated
# template; its rows go with the clone's drop and nothing is deleted row by row (018 B0b; see
# `tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture



async def _wait_for_matching_advisory_wait(
    observer, *, waiter_pid: int, holder_pid: int
) -> bool:
    """`waiter_pid` is queued SHARED on the advisory lock `holder_pid` holds EXCLUSIVELY, and blocked by it."""
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
                        "AND holder.mode = 'ExclusiveLock' AND holder.pid = :holder_pid "
                        "AND holder.database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database()) "
                        "AND waiter.pid = :waiter_pid AND NOT waiter.granted "
                        "AND waiter.mode = 'ShareLock' "
                        "AND :holder_pid = ANY(pg_blocking_pids(waiter.pid))"
                        ")"
                    ),
                    {"waiter_pid": waiter_pid, "holder_pid": holder_pid},
                )
                await observer.rollback()
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
    from app.core.money_boundary import MoneyBoundary
    from app.core.payments.service import PaymentService
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
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

    clearing_parked = asyncio.Event()
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
            async with debt_fixture_setup(setup, label="setup"):
                setup.add_all(debts)
            await setup.commit()

        clearing_session = TestingSessionLocal()
        payment_session = TestingSessionLocal()
        observer_session = TestingSessionLocal()
        for session in (clearing_session, payment_session):
            await session.connection(
                execution_options={"isolation_level": "SERIALIZABLE"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "serializable"  # 019 stage 5 (T1907): the only supported level
        # THE PAYMENT'S OWN TIMEOUTS ARE WIDENED, and the reason is a measurement rather than a
        # convenience. This test deliberately parks the clearing inside its exclusive equivalent lock
        # and asserts that the payment WAITS and then succeeds, so the payment's budget has to cover
        # the whole hold plus the whole clearing. THE ONE THAT ACTUALLY FIRED (measured before 019)
        # WAS `PREPARE_TIMEOUT_SECONDS`, measured by widening the others first and watching it fail
        # unchanged: the payment's binding phase is what waits on the clearing's lock, and its
        # budget was 3 seconds (`app/config.py`). The clearing's locked section grew when the debt journal was armed
        # (step 4 slice C) - an envelope at open, per-edge entries at each flush, and a
        # per-equivalent completion row, all inside it - and three seconds stopped covering it. The
        # commit and total budgets are widened alongside so that the next thing to go over is a real
        # result and not the next constant in the same line; they are read per call, while
        # `MoneyBoundary.__init__` reads its advisory-lock budget ONCE - and `PaymentService.__init__`
        # builds its boundary (until 019 stage 4 it was `PaymentEngine.__init__`, a `MoneyBoundary`) -
        # which is why all of this sits above the services.
        #
        # That the locked section is now longer is a real consequence and is recorded as one; what it
        # is NOT is the subject of this test, which is that neither writer loses the other's effects.
        # Timing out here would have measured the budget instead.
        monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 60, raising=False)
        monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 60, raising=False)
        monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 120, raising=False)

        clearing_service = ClearingService(clearing_session)
        payment_service = PaymentService(payment_session)
        original_policy = clearing_service._cycle_respects_auto_clearing
        original_payment_shared = MoneyBoundary._acquire_shared_equivalent_locks_in_order
        payment_owner_attempted = asyncio.Event()
        payment_owner_pid: int | None = None
        clearing_work_pid: int | None = None

        async def _park_inside_the_money_transaction(debts):
            # The execution's call, with the cycle rows it holds `FOR UPDATE`; `self.session` is the
            # clearing's pinned work session, which holds the exclusive equivalent lock.
            nonlocal clearing_work_pid
            clearing_work_pid = int(
                await clearing_service.session.scalar(text("SELECT pg_backend_pid()"))
            )
            allowed = await original_policy(debts)
            clearing_parked.set()
            await release_clearing.wait()
            return allowed

        monkeypatch.setattr(
            clearing_service,
            "_cycle_respects_auto_clearing",
            _park_inside_the_money_transaction,
        )

        async def _observe_payment_shared_lock(boundary, equivalent_ids):
            nonlocal payment_owner_pid
            payment_owner_pid = int(
                await boundary.session.scalar(text("SELECT pg_backend_pid()"))
            )
            payment_owner_attempted.set()
            return await original_payment_shared(boundary, equivalent_ids)

        monkeypatch.setattr(
            MoneyBoundary,
            "_acquire_shared_equivalent_locks_in_order",
            _observe_payment_shared_lock,
        )

        clearing_task = asyncio.create_task(
            clearing_service.execute_clearing_with_amount(cycle)
        )
        await asyncio.wait_for(clearing_parked.wait(), timeout=5.0)
        assert clearing_work_pid is not None

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
            holder_pid=clearing_work_pid,
        )
        payment_error = (
            payment_task.exception()
            if payment_task.done() and not payment_task.cancelled()
            else None
        )
        assert payment_waiting, (
            "payment's shared lock did not wait on the clearing's exclusive lock: "
            f"done={payment_task.done()} error={payment_error!r}"
        )
        assert not clearing_task.done()
        assert not payment_task.done()

        release_clearing.set()
        # THE BUDGET, not the behaviour. Fifteen seconds stopped being enough when the debt journal
        # was armed (step 4 slice C): the payment waits on the clearing's exclusive lock for the whole
        # clearing, and both units of work now carry an envelope of their own. The payment's own
        # timeouts (`COMMIT_TIMEOUT_SECONDS`, `PAYMENT_TOTAL_TIMEOUT_SECONDS`) are what this test
        # leaves in place to decide the outcome; this number only has to be larger than them, or the
        # harness decides it instead and reports a `TimeoutError` for a payment that was going to
        # succeed.
        cleared_amount, payment_result = await asyncio.wait_for(
            asyncio.gather(clearing_task, payment_task),
            timeout=60.0,
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
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Clearing/payment test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
