import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.payments.engine import PaymentEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant

from tests.debt_setup import debt_fixture_setup, writer_operation


@pytest.mark.asyncio
async def test_apply_flow_retries_on_stale_data(db_session):
    """Ensures PaymentEngine._apply_flow retries StaleDataError and succeeds.

    Scenario:
    - Session S_stale loads the debt (version N)
    - Session S_fresh updates it (version N+1)
    - S_stale tries to update and must hit StaleDataError
    - _apply_flow should retry with fresh state and complete
    """

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("AF" + nonce[:14]).upper(),
        symbol="AF",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    sender = Participant(
        pid="S" + nonce,
        display_name="S",
        public_key="pkS-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        pid="R" + nonce,
        display_name="R",
        public_key="pkR-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, sender, receiver])
    await db_session.flush()

    # Receiver owes sender 100.
    debt = Debt(
        debtor_id=receiver.id,
        creditor_id=sender.id,
        equivalent_id=eq.id,
        amount=Decimal("100"),
    )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(debt)
    await db_session.commit()

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as s_fresh:
        # Bump the version in a separate session.
        d2 = (
            await s_fresh.execute(select(Debt).where(Debt.id == debt.id))
        ).scalar_one()
        async with debt_fixture_setup(s_fresh, label="version-bump"):
            d2.amount = Decimal("90")
        await s_fresh.commit()

    # Load stale instance in db_session and attempt to apply flow.
    _ = (
        await db_session.execute(select(Debt).where(Debt.id == debt.id))
    ).scalar_one()

    engine = PaymentEngine(db_session)
    # THE WRITER'S OWN OPERATION, not a fixture context (design v2 §8 R5/F7). `_apply_flow` is
    # production code that moves money; called directly it opens no operation, and the journal
    # refuses its flush. Declaring `TEST_FIXTURE` here would journal a payment's effects under the
    # kind reserved for scaffolding, so the real kind is declared instead.
    async with writer_operation(
        db_session, kind="PAYMENT", equivalent_ids=[eq.id], initiator_id=sender.id
    ):
        await engine._apply_flow(sender.id, receiver.id, Decimal("10"), eq.id)

    updated = (
        await db_session.execute(select(Debt).where(Debt.id == debt.id))
    ).scalar_one()

    # After concurrent update (to 90) and applying flow (reduce by 10), expect 80.
    assert updated.amount == Decimal("80")
