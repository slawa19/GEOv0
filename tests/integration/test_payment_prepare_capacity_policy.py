from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest

from app.core.payments.engine import PaymentEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RoutingException


def _payment_transaction(*, tx_id: str, initiator_id: uuid.UUID) -> Transaction:
    return Transaction(
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=initiator_id,
        payload={"from": "CAP-S", "to": "CAP-R", "amount": "74", "equivalent": "CAP"},
        state="ROUTED",
    )


async def _seed_capacity_policy_case(
    db_session,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    sender_id = uuid.uuid4()
    receiver_id = uuid.uuid4()
    equivalent_id = uuid.uuid4()
    other_equivalent_id = uuid.uuid4()
    reservation_tx_id = f"reservation-{uuid.uuid4()}"

    db_session.add_all(
        [
            Participant(
                id=sender_id,
                pid="CAP-S",
                display_name="Capacity sender",
                public_key=f"capacity-sender-{uuid.uuid4()}",
            ),
            Participant(
                id=receiver_id,
                pid="CAP-R",
                display_name="Capacity receiver",
                public_key=f"capacity-receiver-{uuid.uuid4()}",
            ),
            Equivalent(id=equivalent_id, code="CAP", precision=2),
            Equivalent(id=other_equivalent_id, code="ALT", precision=2),
        ]
    )
    db_session.add(
        TrustLine(
            from_participant_id=receiver_id,
            to_participant_id=sender_id,
            equivalent_id=equivalent_id,
            limit=Decimal("100"),
            status="active",
        )
    )
    db_session.add_all(
        [
            Debt(
                debtor_id=sender_id,
                creditor_id=receiver_id,
                equivalent_id=equivalent_id,
                amount=Decimal("30"),
            ),
            Debt(
                debtor_id=receiver_id,
                creditor_id=sender_id,
                equivalent_id=equivalent_id,
                amount=Decimal("10"),
            ),
            _payment_transaction(tx_id=reservation_tx_id, initiator_id=sender_id),
        ]
    )
    db_session.add(
        PrepareLock(
            tx_id=reservation_tx_id,
            participant_id=sender_id,
            effects={
                "flows": [
                    {
                        "from": str(sender_id),
                        "to": str(receiver_id),
                        "amount": "7",
                        "equivalent": str(equivalent_id),
                    },
                    {
                        "from": str(sender_id),
                        "to": str(receiver_id),
                        "amount": "50",
                        "equivalent": str(other_equivalent_id),
                    },
                    {
                        "from": str(receiver_id),
                        "to": str(sender_id),
                        "amount": "60",
                        "equivalent": str(equivalent_id),
                    },
                    {
                        "from": "not-a-uuid",
                        "to": str(receiver_id),
                        "amount": "999",
                        "equivalent": str(equivalent_id),
                    },
                ]
            },
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    )
    await db_session.commit()
    return sender_id, receiver_id, equivalent_id


@pytest.mark.asyncio
async def test_segment_capacity_policy_counts_only_matching_valid_reservations(
    db_session,
):
    sender_id, receiver_id, equivalent_id = await _seed_capacity_policy_case(db_session)

    available, reserved = await PaymentEngine(
        db_session
    )._get_segment_capacity_and_reserved_usage(
        tx_id=f"candidate-{uuid.uuid4()}",
        sender_id=sender_id,
        receiver_id=receiver_id,
        equivalent_id=equivalent_id,
    )

    assert available == Decimal("80")
    assert reserved == Decimal("7")


@pytest.mark.asyncio
async def test_single_and_multipath_prepare_apply_the_same_persisted_reservation_policy(
    db_session,
):
    sender_id, _receiver_id, equivalent_id = await _seed_capacity_policy_case(
        db_session
    )
    single_tx_id = f"single-{uuid.uuid4()}"
    multipath_tx_id = f"multipath-{uuid.uuid4()}"
    db_session.add_all(
        [
            _payment_transaction(tx_id=single_tx_id, initiator_id=sender_id),
            _payment_transaction(tx_id=multipath_tx_id, initiator_id=sender_id),
        ]
    )
    await db_session.commit()

    engine = PaymentEngine(db_session)
    with pytest.raises(RoutingException) as single_error:
        await engine.prepare(
            single_tx_id,
            ["CAP-S", "CAP-R"],
            Decimal("74"),
            equivalent_id,
            commit=False,
        )
    with pytest.raises(RoutingException) as multipath_error:
        await engine.prepare_routes(
            multipath_tx_id,
            [(["CAP-S", "CAP-R"], Decimal("74"))],
            equivalent_id,
            commit=False,
        )

    assert single_error.value.details == multipath_error.value.details
    assert Decimal(single_error.value.details["available"]) == Decimal("80")
    assert Decimal(single_error.value.details["needed"]) == Decimal("74")
    assert Decimal(single_error.value.details["reserved"]) == Decimal("7")


@pytest.mark.asyncio
async def test_multipath_prepare_keeps_local_reservations_in_addition_to_persisted_ones(
    db_session,
):
    sender_id, _receiver_id, equivalent_id = await _seed_capacity_policy_case(
        db_session
    )
    tx_id = f"local-reservation-{uuid.uuid4()}"
    db_session.add(_payment_transaction(tx_id=tx_id, initiator_id=sender_id))
    await db_session.commit()

    with pytest.raises(RoutingException) as error:
        await PaymentEngine(db_session).prepare_routes(
            tx_id,
            [
                (["CAP-S", "CAP-R"], Decimal("40")),
                (["CAP-S", "CAP-R"], Decimal("40")),
            ],
            equivalent_id,
            commit=False,
        )

    assert Decimal(error.value.details["available"]) == Decimal("80")
    assert Decimal(error.value.details["needed"]) == Decimal("40")
    assert Decimal(error.value.details["reserved"]) == Decimal("47")
