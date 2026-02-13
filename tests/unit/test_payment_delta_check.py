import hashlib
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.payments.engine import PaymentEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import IntegrityViolationException


@pytest.mark.asyncio
async def test_payment_engine_delta_check_raises_on_drift(db_session: AsyncSession) -> None:
    nonce = hashlib.sha256(b"payment_delta_check").hexdigest()[:10]

    eq = Equivalent(
        code=("PDC" + nonce[:13]).upper(),
        symbol="PDC",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    sender = Participant(
        pid="p_sender_" + nonce,
        display_name="Sender",
        public_key="pk_sender_" + nonce,
        type="person",
        status="active",
        profile={},
    )
    receiver = Participant(
        pid="p_receiver_" + nonce,
        display_name="Receiver",
        public_key="pk_receiver_" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, sender, receiver])
    await db_session.flush()

    # Expected flow: 10. Actual DB state after commit: only 7.
    db_session.add(
        Debt(
            debtor_id=sender.id,
            creditor_id=receiver.id,
            equivalent_id=eq.id,
            amount=Decimal("7"),
        )
    )
    await db_session.commit()

    engine = PaymentEngine(db_session)

    with pytest.raises(IntegrityViolationException) as e:
        await engine.check_payment_delta(
            equivalent_id=eq.id,
            flows=[(sender.id, receiver.id, Decimal("10"))],
            net_positions_before={sender.id: Decimal("0"), receiver.id: Decimal("0")},
        )

    details = e.value.details
    assert details.get("invariant") == "PAYMENT_DELTA_DRIFT"
    assert details.get("source") == "delta_check"
    assert details.get("equivalent") == eq.code
    assert Decimal(str(details.get("total_drift"))) == Decimal("3")
    drifts = details.get("drifts")
    assert isinstance(drifts, list)
    assert {d.get("participant_id") for d in drifts} == {sender.pid, receiver.pid}
