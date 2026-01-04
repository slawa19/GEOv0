from decimal import Decimal

import pytest

from app.core.balance.service import BalanceService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.config import settings


@pytest.mark.asyncio
async def test_balance_summary_capacity_semantics(db_session):
    # Disable cache to keep assertions deterministic.
    settings.BALANCE_SUMMARY_CACHE_TTL_SECONDS = 0

    usd = Equivalent(code="USD", description="US Dollar", precision=2)
    db_session.add(usd)

    a = Participant(pid="A", type="person", display_name="A", public_key="pkA", status="active")
    b = Participant(pid="B", type="person", display_name="B", public_key="pkB", status="active")
    db_session.add_all([a, b])
    await db_session.flush()

    # Trustlines:
    # - B trusts A with limit 100 => enables payment edge A -> B
    # - A trusts B with limit 50  => enables payment edge B -> A
    tl_b_a = TrustLine(from_participant_id=b.id, to_participant_id=a.id, equivalent_id=usd.id, limit=Decimal("100"))
    tl_a_b = TrustLine(from_participant_id=a.id, to_participant_id=b.id, equivalent_id=usd.id, limit=Decimal("50"))
    db_session.add_all([tl_b_a, tl_a_b])

    # Debts:
    # - A owes B 10
    # - B owes A 20
    d_a_b = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=usd.id, amount=Decimal("10"))
    d_b_a = Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=usd.id, amount=Decimal("20"))
    db_session.add_all([d_a_b, d_b_a])

    await db_session.commit()

    service = BalanceService(db_session)
    summary = await service.get_summary(a.id)

    assert len(summary.equivalents) == 1
    eq = summary.equivalents[0]
    assert eq.code == "USD"

    assert Decimal(eq.total_debt) == Decimal("10")
    assert Decimal(eq.total_credit) == Decimal("20")
    assert Decimal(eq.net_balance) == Decimal("10")

    # spend_capacity(A->B) = Limit(B->A) - Debt(A->B) + Debt(B->A) = 100 - 10 + 20 = 110
    assert Decimal(eq.available_to_spend) == Decimal("110")

    # receive_capacity(B->A) = Limit(A->B) - Debt(B->A) + Debt(A->B) = 50 - 20 + 10 = 40
    assert Decimal(eq.available_to_receive) == Decimal("40")
