"""The book keeps each operation kind to its own semantics (018 stage A, `T1802`).

The semantics are preserved per kind and NOT unified (spec 018, stage A): `CLEARING` only decreases,
`INJECT` only increases or refuses - an opposing debt is refused and returned, never netted - and an
effect reaches `debts` only through a posting that is open. Each rule is checked on the real database
through the same envelope the application uses (`writer_operation` opens a `Book` operation).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.ledger.book import (
    APPLIED,
    REFUSED_OPPOSING_DEBT,
    REFUSED_OVER_CEILING,
    Book,
    BookError,
    ClearingReduction,
    InjectIncrease,
    PaymentFlow,
)
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.debt_setup import debt_fixture_setup, writer_operation


async def _world(session, *, debt_b_owes_a: str | None = None):
    n = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("K" + n[:15]).upper(), symbol="K", precision=2, metadata_={},
                    is_active=True)
    a = Participant(pid="A" + n, display_name="A", public_key="pkA-" + n, type="person",
                    status="active", profile={})
    b = Participant(pid="B" + n, display_name="B", public_key="pkB-" + n, type="person",
                    status="active", profile={})
    session.add_all([eq, a, b])
    await session.flush()
    debt = None
    if debt_b_owes_a is not None:
        async with debt_fixture_setup(session, label="p018-kind-semantics"):
            debt = Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                        amount=Decimal(debt_b_owes_a))
            session.add(debt)
        await session.flush()
    return eq, a, b, debt


async def _debts(session, eq) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    rows = (
        await session.execute(
            select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == eq.id)
        )
    ).all()
    return {(d, c): Decimal(str(amount)) for d, c, amount in rows}


@pytest.mark.asyncio
async def test_inject_refuses_an_opposing_debt_and_never_nets(db_session) -> None:
    eq, a, b, _ = await _world(db_session, debt_b_owes_a="5.00")
    async with writer_operation(db_session, kind="INJECT", equivalent_ids=[eq.id]):
        posting = Book.current(db_session)
        # A owes B 3.00 would be the opposite direction of the existing B owes A 5.00.
        outcome = await posting.apply(
            InjectIncrease(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id,
                           amount=Decimal("3.00"), ceiling=Decimal("100"))
        )
    await db_session.flush()
    assert outcome == REFUSED_OPPOSING_DEBT
    assert await _debts(db_session, eq) == {(b.id, a.id): Decimal("5.00")}


@pytest.mark.asyncio
async def test_inject_refuses_a_result_over_its_ceiling(db_session) -> None:
    eq, a, b, _ = await _world(db_session, debt_b_owes_a="5.00")
    async with writer_operation(db_session, kind="INJECT", equivalent_ids=[eq.id]):
        posting = Book.current(db_session)
        over = await posting.apply(
            InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                           amount=Decimal("6.00"), ceiling=Decimal("10"))
        )
        within = await posting.apply(
            InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                           amount=Decimal("5.00"), ceiling=Decimal("10"))
        )
    await db_session.flush()
    # Counter-check: the same direction within the ceiling still increases.
    assert (over, within) == (REFUSED_OVER_CEILING, APPLIED)
    assert await _debts(db_session, eq) == {(b.id, a.id): Decimal("10.00")}


@pytest.mark.asyncio
async def test_clearing_refuses_to_grow_a_debt(db_session) -> None:
    eq, a, b, debt = await _world(db_session, debt_b_owes_a="5.00")
    with pytest.raises(BookError, match="CLEARING only decreases"):
        async with writer_operation(db_session, kind="CLEARING", equivalent_ids=[eq.id],
                                    initiator_id=a.id):
            await Book.current(db_session).apply(
                ClearingReduction(debt=debt, amount=Decimal("6.00"))
            )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_clearing_decreases_and_deletes_at_zero(db_session) -> None:
    """Counter-check for the refusal above: a reduction within the debt is applied."""
    eq, a, b, debt = await _world(db_session, debt_b_owes_a="5.00")
    async with writer_operation(db_session, kind="CLEARING", equivalent_ids=[eq.id],
                                initiator_id=a.id):
        posting = Book.current(db_session)
        assert await posting.apply(ClearingReduction(debt=debt, amount=Decimal("2.00"))) == APPLIED
        assert await posting.apply(ClearingReduction(debt=debt, amount=Decimal("3.00"))) == APPLIED
    await db_session.flush()
    assert await _debts(db_session, eq) == {}


@pytest.mark.asyncio
async def test_a_kind_takes_only_its_own_effect(db_session) -> None:
    """An INJECT operation cannot be handed a payment flow - the netting path is not reachable."""
    eq, a, b, _ = await _world(db_session, debt_b_owes_a="5.00")
    with pytest.raises(BookError, match="INJECT operation does not take PaymentFlow"):
        async with writer_operation(db_session, kind="INJECT", equivalent_ids=[eq.id]):
            await Book.current(db_session).apply(
                PaymentFlow(from_id=a.id, to_id=b.id, amount=Decimal("1"), equivalent_id=eq.id)
            )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_no_effect_moves_outside_an_open_posting(db_session) -> None:
    eq, a, b, _ = await _world(db_session)
    with pytest.raises(BookError, match="no Book operation is open"):
        Book.current(db_session)
    async with writer_operation(db_session, kind="INJECT", equivalent_ids=[eq.id]):
        posting = Book.current(db_session)
    await db_session.flush()
    with pytest.raises(BookError, match="is closed"):
        await posting.apply(
            InjectIncrease(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id,
                           amount=Decimal("1.00"), ceiling=Decimal("10"))
        )
    assert await _debts(db_session, eq) == {}
