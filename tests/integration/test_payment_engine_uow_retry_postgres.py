import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError


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

        setup.add(
            Transaction(
                id=uuid.uuid4(),
                tx_id=tx_id,
                type="PAYMENT",
                initiator_id=a.id,
                payload={"from": a.pid, "to": b.pid},
                state="PREPARED",
            )
        )

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

