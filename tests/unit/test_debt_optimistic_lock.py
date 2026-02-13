import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant


@pytest.mark.asyncio
async def test_debt_optimistic_locking_raises_on_stale_update(db_session):
    """Verifies the lost-update class of bug is prevented via ORM versioning.

    Without optimistic locking, two sessions can overwrite each other's absolute
    debt updates. With versioned rows, the second updater must raise StaleDataError.
    """

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("OL" + nonce[:14]).upper(),
        symbol="OL",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    a = Participant(
        pid="A" + nonce,
        display_name="A",
        public_key="pkA-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    b = Participant(
        pid="B" + nonce,
        display_name="B",
        public_key="pkB-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, a, b])
    await db_session.flush()

    d = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("100"))
    db_session.add(d)
    await db_session.commit()

    # Use a separate AsyncSession to hold a stale ORM instance.
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s1:
        async with TestingSessionLocal() as s2:
            debt1 = (
                await s1.execute(select(Debt).where(Debt.id == d.id))
            ).scalar_one()
            debt2 = (
                await s2.execute(select(Debt).where(Debt.id == d.id))
            ).scalar_one()

            debt1.amount = Decimal("70")
            await s1.commit()

            debt2.amount = Decimal("130")
            with pytest.raises(StaleDataError):
                await s2.commit()
