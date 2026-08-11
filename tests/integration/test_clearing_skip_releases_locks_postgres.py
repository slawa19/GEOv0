"""PostgreSQL proof that a skipped clearing candidate must release Debt row locks."""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text


pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "skip_branch",
    ["empty", "malformed", "missing", "locked", "policy"],
)
@pytest.mark.asyncio
async def test_skip_ends_service_owned_transaction_postgres(
    db_session,
    skip_branch,
):
    """Every None result must end the service-owned attempt."""

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: clearing skip-path transaction ownership")

    from app.core.clearing.service import ClearingService
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]

    db_session.add(
        Equivalent(
            id=equivalent_id,
            code=f"SO{nonce}".upper(),
            description=f"Clearing {skip_branch} ownership test",
            precision=2,
        )
    )
    db_session.add_all(
        [
            Participant(
                id=participant_id,
                pid=f"{label}_SO_{nonce}",
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
    db_session.add_all(
        [
            TrustLine(
                from_participant_id=creditor_id,
                to_participant_id=debtor_id,
                equivalent_id=equivalent_id,
                limit=Decimal("200.00"),
                policy={
                    "auto_clearing": not (
                        skip_branch == "policy" and creditor_id == c_id
                    )
                },
                status="active",
            )
            for debtor_id, creditor_id in (
                (a_id, b_id),
                (b_id, c_id),
                (c_id, a_id),
            )
        ]
    )
    db_session.add_all(
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

    if skip_branch == "locked":
        tx_id = str(uuid.uuid4())
        db_session.add(
            Transaction(
                id=uuid.uuid4(),
                tx_id=tx_id,
                type="PAYMENT",
                initiator_id=a_id,
                payload={"from": str(a_id), "to": str(b_id)},
                state="PREPARED",
            )
        )
        await db_session.flush()
        db_session.add(
            PrepareLock(
                tx_id=tx_id,
                participant_id=a_id,
                effects={
                    "flows": [
                        {
                            "from": str(a_id),
                            "to": str(b_id),
                            "amount": "5.00",
                            "equivalent": str(equivalent_id),
                        }
                    ]
                },
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )

    await db_session.commit()

    cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]
    if skip_branch in {"empty", "malformed"}:
        # Prove that even a pre-SQL rejection closes a transaction already opened
        # by the caller's candidate lookup.
        await db_session.execute(select(Debt.id).limit(1))
        assert db_session.in_transaction()
        cycle = [] if skip_branch == "empty" else [{"debt_id": "not-a-uuid"}]
    elif skip_branch == "missing":
        cycle[-1] = {"debt_id": str(uuid.uuid4())}
    result = await ClearingService(db_session).execute_clearing_with_amount(cycle)

    assert result is None
    assert not db_session.in_transaction(), f"skip_branch={skip_branch}"


@pytest.mark.asyncio
async def test_policy_skip_releases_debt_rows_before_concurrent_payment_postgres(
    db_session,
    monkeypatch,
):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: clearing skip-path row-lock ownership")

    from app.config import settings
    from app.core.clearing.service import ClearingService
    from app.core.payments.router import PaymentRouter
    from app.core.payments.service import PaymentService
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.schemas.payment import PaymentConstraints
    from app.utils.exceptions import TimeoutException
    from tests.conftest import TestingSessionLocal

    # Keep the real database wait bounded without injecting a synthetic DBAPI error.
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 3)

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"CS{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    participant_pids = [f"{label}_CS_{nonce}" for label in ("A", "B", "C")]
    a_pid, b_pid, c_pid = participant_pids
    debt_ids = [uuid.uuid4() for _ in range(3)]
    cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]
    blocked_payment_tx_id = str(uuid.uuid4())
    payment_tx_ids = [blocked_payment_tx_id]

    clearing_session = None
    payment_session = None
    payment_task = None

    try:
        async with TestingSessionLocal() as setup:
            setup.add(
                Equivalent(
                    id=equivalent_id,
                    code=equivalent_code,
                    description="Clearing policy-skip lock ownership test",
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
            # Trustline direction is creditor -> debtor. Only C -> B rejects
            # auto-clearing; the concurrent A -> B payment remains a valid route.
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=b_id,
                        to_participant_id=a_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        policy={
                            "auto_clearing": True,
                            "can_be_intermediate": True,
                        },
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=c_id,
                        to_participant_id=b_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        policy={
                            "auto_clearing": False,
                            "can_be_intermediate": True,
                        },
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=a_id,
                        to_participant_id=c_id,
                        equivalent_id=equivalent_id,
                        limit=Decimal("200.00"),
                        policy={
                            "auto_clearing": True,
                            "can_be_intermediate": True,
                        },
                        status="active",
                    ),
                ]
            )
            setup.add_all(
                [
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
            )
            await setup.commit()

        clearing_session = TestingSessionLocal()
        payment_session = TestingSessionLocal()
        for session in (
            clearing_session,
            payment_session,
        ):
            await session.connection(
                execution_options={"isolation_level": "READ COMMITTED"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "read committed"

        skipped_amount = await ClearingService(
            clearing_session
        ).execute_clearing_with_amount(cycle)
        assert skipped_amount is None

        payment_service = PaymentService(payment_session)
        payment_task = asyncio.create_task(
            payment_service.create_payment_internal(
                a_id,
                to_pid=b_pid,
                equivalent=equivalent_code,
                amount="5.00",
                constraints=PaymentConstraints(max_paths=1),
                idempotency_key=blocked_payment_tx_id,
            ),
            name="clearing-policy-skip-concurrent-payment",
        )

        payment_result = None
        payment_timeout = None
        try:
            payment_result = await payment_task
        except TimeoutException as exc:
            payment_timeout = exc

        async with TestingSessionLocal() as verify:
            payment_transactions = {
                tx.tx_id: tx
                for tx in (
                    await verify.scalars(
                        select(Transaction).where(
                            Transaction.tx_id.in_(payment_tx_ids)
                        )
                    )
                ).all()
            }
            remaining_prepare_locks = (
                await verify.scalars(
                    select(PrepareLock).where(
                        PrepareLock.tx_id.in_(payment_tx_ids)
                    )
                )
            ).all()
            shared_debt = await verify.scalar(
                select(Debt.amount).where(Debt.id == debt_ids[0])
            )

        assert payment_timeout is None, (
            "clearing policy skip retained Debt row locks: "
            f"actual={type(payment_timeout).__name__} "
            f"code={payment_timeout.code} status={payment_timeout.status_code}"
        )
        assert payment_result is not None and payment_result.status == "COMMITTED"
        assert payment_transactions[blocked_payment_tx_id].state == "COMMITTED"
        assert remaining_prepare_locks == []
        # Anti-vacuum: this is a real routed payment effect, not an empty waiter.
        assert shared_debt == Decimal("105.00000000")
    finally:
        primary_error = sys.exc_info()[1]
        try:
            if payment_task is not None and not payment_task.done():
                payment_task.cancel()
                try:
                    await asyncio.wait_for(payment_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            elif payment_task is not None and not payment_task.cancelled():
                payment_task.exception()

            async with asyncio.timeout(5.0):
                for session in (
                    clearing_session,
                    payment_session,
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
                            PrepareLock.tx_id.in_(payment_tx_ids)
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
            PaymentRouter.invalidate_cache(equivalent_code)
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Clearing policy-skip test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
