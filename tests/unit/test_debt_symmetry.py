import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.invariants import InvariantChecker
from app.core.ledger.book import Book, PaymentFlow
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import IntegrityViolationException

from tests.debt_setup import debt_fixture_setup, writer_operation


@pytest.mark.asyncio
async def test_debt_symmetry_violation_detected(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("S" + nonce[:15]).upper(), symbol="S", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    async with debt_fixture_setup(db_session, label="asymmetric-debts"):
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
    async with debt_fixture_setup(db_session, label="mutual-debts"):
        db_session.add_all(
            [
                Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
                Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("7")),
            ]
        )
    await db_session.flush()

    # THE WRITER'S OWN OPERATION, not a fixture context (design v2 §8 R5/F7). A payment flow is
    # production code that moves money; called directly it opens no operation, and the journal
    # refuses its flush. Declaring `TEST_FIXTURE` here would journal a payment's effects under the
    # kind reserved for scaffolding, so the real kind is declared instead.
    # Apply a flow A->B that would normally add debt A->B, but engine should net mutual.
    async with writer_operation(
        db_session, kind="PAYMENT", equivalent_ids=[eq.id], initiator_id=a.id
    ):
        # The payment path's flow, as `PaymentService._apply_payment` applies it since 019 stage 4 (the
        # engine's `_apply_flow` forwarder is gone).
        await Book.current(db_session).apply(
            PaymentFlow(from_id=a.id, to_id=b.id, amount=Decimal("0"), equivalent_id=eq.id)
        )
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
