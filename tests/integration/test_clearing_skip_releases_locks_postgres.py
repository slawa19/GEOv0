"""PostgreSQL proof that a skipped clearing candidate must release Debt row locks."""

from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from tests.debt_setup import debt_fixture_setup

# Every test here commits through several sessions and runs on a disposable clone of the migrated
# template; its rows go with the clone's drop and nothing is deleted row by row (018 B0b; see
# `tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture



# The `locked` branch (a cycle skipped because a live payment reservation covers one of its pairs) is
# DROPPED by 019 stage 4 (manifest `t1901-manifest.md` 5.3, rows :119-148, :171-173, :201): it seeded a
# `PREPARED` `PAYMENT` with a `PrepareLock`, which CHECK `030` refuses, and after stage 4 no payment path
# produces a reservation for clearing to skip on - the contract is removed, not moved. What the branch
# also proved, that a skip ends the service-owned transaction, is proved below by the four remaining
# branches (`result is None`, `not session.in_transaction()`), with the `policy` branch's witness.
@pytest.mark.parametrize(
    "skip_branch",
    ["empty", "malformed", "missing", "policy"],
)
@pytest.mark.asyncio
async def test_skip_ends_service_owned_transaction_postgres(
    db_session,
    skip_branch,
    monkeypatch,
):
    """Every None result must end the service-owned attempt."""

    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: clearing skip-path transaction ownership")

    from app.core.clearing.service import ClearingService
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]

    session = TestingSessionLocal()
    session.add(
        Equivalent(
            id=equivalent_id,
            code=f"SO{nonce}".upper(),
            description=f"Clearing {skip_branch} ownership test",
            precision=2,
        )
    )
    session.add_all(
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
    session.add_all(
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
    async with debt_fixture_setup(session, label="setup"):
        session.add_all(
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

    await session.commit()

    try:
        cycle = [{"debt_id": str(debt_id)} for debt_id in debt_ids]
        if skip_branch in {"empty", "malformed"}:
            # Prove that even a pre-SQL rejection closes a transaction already
            # opened by the caller's candidate lookup.
            await session.execute(select(Debt.id).limit(1))
            assert session.in_transaction()
            cycle = [] if skip_branch == "empty" else [{"debt_id": "not-a-uuid"}]
        elif skip_branch == "missing":
            cycle[-1] = {"debt_id": str(uuid.uuid4())}

        service = ClearingService(session)
        branch_witness = False
        if skip_branch == "policy":
            original_policy = service._cycle_respects_auto_clearing

            async def _witness_policy(debts):
                nonlocal branch_witness
                allowed = await original_policy(debts)
                assert allowed is False
                branch_witness = True
                return allowed

            monkeypatch.setattr(
                service,
                "_cycle_respects_auto_clearing",
                _witness_policy,
            )

        result = await service.execute_clearing_with_amount(cycle)

        assert result is None
        assert not session.in_transaction(), f"skip_branch={skip_branch}"
        if skip_branch == "policy":
            assert branch_witness, f"skip_branch={skip_branch} was not reached"
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_policy_skip_releases_debt_rows_before_concurrent_payment_postgres(
    db_session,
    monkeypatch,
):
    """A policy skip releases the cycle's Debt row locks AND the clearing's exclusive equivalent lock.

    019 stage 5 (`T1909`): the `prepare_locks` assertion this test ended with went with the table (the
    payment path writes no reservation, so it was vacuous since stage 4). In its place: the clearing
    really took its exclusive equivalent session lock (counted), and after the skip no advisory lock is
    held on this database - a skip that kept it would make the payment's shared acquisition wait and time
    out, exactly like a retained row lock.
    """
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: clearing skip-path row-lock ownership")

    from app.core.clearing.service import ClearingService
    from app.core.money_boundary import MoneyBoundary
    from app.core.payments.router import PaymentRouter
    from app.core.payments.service import PaymentService
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.schemas.payment import PaymentConstraints
    from app.utils.exceptions import TimeoutException
    from tests.conftest import TestingSessionLocal
    from tests.p019_locks_off import advisory_locks_held

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
            async with debt_fixture_setup(setup, label="setup"):
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
                execution_options={"isolation_level": "SERIALIZABLE"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "serializable"  # 019 stage 5 (T1907): the only supported level

        exclusive_acquired: list[uuid.UUID] = []
        original_exclusive = MoneyBoundary.acquire_exclusive_equivalent_session_lock

        async def _count_exclusive(self, locked_equivalent_id):
            await original_exclusive(self, locked_equivalent_id)
            exclusive_acquired.append(locked_equivalent_id)

        monkeypatch.setattr(
            MoneyBoundary, "acquire_exclusive_equivalent_session_lock", _count_exclusive
        )
        skipped_amount = await ClearingService(
            clearing_session
        ).execute_clearing_with_amount(cycle)
        monkeypatch.setattr(
            MoneyBoundary, "acquire_exclusive_equivalent_session_lock", original_exclusive
        )
        assert skipped_amount is None
        # The skip happened UNDER the clearing's exclusive lock, and left no advisory lock behind.
        assert exclusive_acquired == [equivalent_id]
        async with TestingSessionLocal() as observer:
            assert await advisory_locks_held(observer) == 0

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

            PaymentRouter.invalidate_cache(equivalent_code)
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Clearing policy-skip test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )
