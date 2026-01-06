import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.invariants import InvariantChecker
from app.core.payments.engine import PaymentEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import IntegrityViolationException


@pytest.mark.asyncio
async def test_debt_symmetry_violation_detected(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("S" + nonce[:15]).upper(), symbol="S", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("7")),
        ]
    )
    await db_session.flush()

    checker = InvariantChecker(db_session)
    with pytest.raises(IntegrityViolationException) as exc_info:
        await checker.check_debt_symmetry(equivalent_id=eq.id)

    assert exc_info.value.code == "E008"
    assert exc_info.value.details.get("invariant") == "DEBT_SYMMETRY_VIOLATION"


@pytest.mark.asyncio
async def test_apply_flow_nets_mutual_debts(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("N" + nonce[:15]).upper(), symbol="N", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    # Create mutual debts
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("7")),
        ]
    )
    await db_session.flush()

    engine = PaymentEngine(db_session)
    # Apply a flow A->B that would normally add debt A->B, but engine should net mutual.
    await engine._apply_flow(a.id, b.id, Decimal("0"), eq.id)
    await db_session.flush()

    debts = (
        await db_session.execute(
            select(Debt).where(
                Debt.equivalent_id == eq.id,
            )
        )
    ).scalars().all()

    # After netting, only one direction should remain with net amount 3.
    amounts = {(d.debtor_id, d.creditor_id): d.amount for d in debts if d.amount > 0}
    assert len(amounts) == 1
    remaining_amount = list(amounts.values())[0]
    assert remaining_amount == Decimal("3")
