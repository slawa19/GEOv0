from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete


@pytest.mark.asyncio
async def test_concurrent_clearing_payment_lost_update_is_prevented_by_optimistic_lock(
    db_session,
):
    """Integration regression test for lost update (clearing vs payment).

    We simulate the problematic interleaving:
      - payment session reads Debt (old version)
      - clearing session updates+commits the same Debt (bumps version)
      - payment session attempts to flush an UPDATE with stale version

    Expected behavior:
      - SQLAlchemy raises StaleDataError
      - PaymentEngine._apply_flow catches it and retries
      - final amount matches (clearing applied) + (payment applied)

    Postgres-only: requires true concurrent writers without SQLite locking flakes.
    Uses best-effort cleanup because extra sessions are outside the db_session
    rollback boundary.
    """

    # Local imports: keep module import side effects minimal.
    from tests.conftest import TestingSessionLocal
    from app.core.payments.engine import PaymentEngine
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant

    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None

    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: concurrent clearing/payment lost update regression")

    nonce = uuid.uuid4().hex[:10]
    eq_code = ("T5" + nonce).upper()

    a_pid = f"A_T5_{nonce}"
    b_pid = f"B_T5_{nonce}"

    equivalent_id = None
    a_id = None
    b_id = None
    debt_id = None

    try:
        # Seed baseline state.
        async with TestingSessionLocal() as setup:
            eq = Equivalent(code=eq_code, symbol=eq_code, description=None, precision=2)
            a = Participant(
                pid=a_pid,
                display_name="A",
                public_key=f"pk_A_{nonce}",
                type="person",
                status="active",
                profile={},
            )
            b = Participant(
                pid=b_pid,
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

            equivalent_id = eq.id
            a_id = a.id
            b_id = b.id

            # Debt on the same edge that _apply_flow will UPDATE (debtor=from, creditor=to).
            debt = Debt(
                debtor_id=a_id,
                creditor_id=b_id,
                equivalent_id=equivalent_id,
                amount=Decimal("100"),
            )
            setup.add(debt)
            await setup.commit()
            await setup.refresh(debt)
            debt_id = debt.id

        assert equivalent_id is not None and a_id is not None and b_id is not None
        assert debt_id is not None

        injected = {"done": False}

        async with TestingSessionLocal() as payment_session, TestingSessionLocal() as clearing_session:
            engine = PaymentEngine(payment_session)
            original_get_debt = engine._get_debt

            async def wrapped_get_debt(debtor_id, creditor_id, eq_id):
                debt_obj = await original_get_debt(debtor_id, creditor_id, eq_id)

                # Inject the "clearing" write after payment has read the row
                # but before it flushes its UPDATE.
                if (
                    debt_obj is not None
                    and injected["done"] is False
                    and debtor_id == a_id
                    and creditor_id == b_id
                    and eq_id == equivalent_id
                ):
                    d2 = await clearing_session.get(Debt, debt_id)
                    assert d2 is not None
                    d2.amount = Decimal(str(d2.amount)) - Decimal("30")
                    clearing_session.add(d2)
                    await clearing_session.commit()
                    injected["done"] = True

                return debt_obj

            engine._get_debt = wrapped_get_debt  # type: ignore[method-assign]

            # Payment applies +50 on top of the concurrently-cleared value.
            await engine._apply_flow(a_id, b_id, Decimal("50"), equivalent_id)
            await payment_session.commit()

        assert injected["done"] is True, "Expected concurrent update injection to run"

        async with TestingSessionLocal() as verify:
            final = await verify.get(Debt, debt_id)
            assert final is not None
            assert Decimal(str(final.amount)) == Decimal("120")
    finally:
        # Cleanup (best-effort) to keep shared Postgres test DB tidy.
        if dialect in {"postgresql", "postgres"}:
            try:
                async with TestingSessionLocal() as cleanup:
                    if debt_id is not None:
                        await cleanup.execute(delete(Debt).where(Debt.id == debt_id))
                    if a_id is not None:
                        await cleanup.execute(delete(Participant).where(Participant.id == a_id))
                    if b_id is not None:
                        await cleanup.execute(delete(Participant).where(Participant.id == b_id))
                    if equivalent_id is not None:
                        await cleanup.execute(delete(Equivalent).where(Equivalent.id == equivalent_id))
                    await cleanup.commit()
            except Exception:
                pass
