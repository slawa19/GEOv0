"""The capacity of a payment segment and the reservations it still honours (AGENTS §8 routing).

The capacity formula is protected: `limit(receiver -> sender) - debt(sender -> receiver) + debt(receiver
-> sender)`, and several routes of ONE payment over one segment are summed against it. Until programme
019 stage 4 this was measured on the engine's private `_get_segment_capacity_and_reserved_usage` and its
`prepare`/`prepare_routes`; since stage 4 the payment path computes it in `PaymentService._bind_payment`
(`_segment_capacity`), and this module measures it there (manifest t1901, 5.1, rows of this file). The
single-route and multi-route entries are one function now, so the old "the two entries give identical
details" comparison has no second entry to compare with; each refusal's details are asserted directly.

THE RESERVATION BELOW IS SYNTHETIC. Since stage 4 no payment path writes `prepare_locks`; the reader
still honours a live reservation however it got there until stage 5 removes the table and its readers
(`T1909`). The row is written by hand and anchored on a terminal (`ABORTED`) payment only because
`prepare_locks.tx_id` must reference a transaction - it does not describe any reachable payment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest

from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RoutingException

from tests.debt_setup import debt_fixture_setup


def _payment_transaction(*, tx_id: str, initiator_id: uuid.UUID) -> Transaction:
    return Transaction(
        tx_id=tx_id,
        type="PAYMENT",
        initiator_id=initiator_id,
        payload={"from": "CAP-S", "to": "CAP-R", "amount": "74", "equivalent": "CAP"},
        # The FK anchor of the synthetic reservation: terminal, as every PAYMENT row is since 030.
        state="ABORTED",
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
    # Built here rather than inline below: `fixture_block_violations` allows only constructors and
    # session calls inside a fixture block, and a local factory is indistinguishable in the AST
    # from a helper that drives a writer. The object, the list and the single `add_all` are
    # unchanged, so the flush sees exactly what it saw before.
    reservation_transaction = _payment_transaction(
        tx_id=reservation_tx_id, initiator_id=sender_id
    )
    async with debt_fixture_setup(db_session, label="setup"):
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
                reservation_transaction,
            ]
        )
    # prepare_locks.tx_id references transactions.tx_id with no ORM relationship, so the
    # flush does not order the two inserts; write the transaction first.
    await db_session.flush()
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

    available, reserved = await PaymentService(db_session)._segment_capacity(
        tx_id=f"candidate-{uuid.uuid4()}",
        sender_id=sender_id,
        receiver_id=receiver_id,
        equivalent_id=equivalent_id,
    )

    # 100 - 30 + 10: the limit, less what the sender owes, plus what the receiver owes back.
    assert available == Decimal("80")
    # Only the same-equivalent, same-direction, well-formed flow of the (synthetic) reservation.
    assert reserved == Decimal("7")


@pytest.mark.asyncio
async def test_a_single_route_refusal_reports_the_capacity_the_need_and_the_reservation(
    db_session,
):
    _sender_id, _receiver_id, equivalent_id = await _seed_capacity_policy_case(db_session)

    with pytest.raises(RoutingException) as error:
        await PaymentService(db_session)._bind_payment(
            f"single-{uuid.uuid4()}",
            [(["CAP-S", "CAP-R"], Decimal("74"))],
            equivalent_id,
        )

    assert Decimal(error.value.details["available"]) == Decimal("80")
    assert Decimal(error.value.details["needed"]) == Decimal("74")
    assert Decimal(error.value.details["reserved"]) == Decimal("7")
    assert (error.value.details["from"], error.value.details["to"]) == ("CAP-S", "CAP-R")


@pytest.mark.asyncio
async def test_multipath_keeps_its_own_routes_in_addition_to_persisted_reservations(
    db_session,
):
    _sender_id, _receiver_id, equivalent_id = await _seed_capacity_policy_case(db_session)

    with pytest.raises(RoutingException) as error:
        await PaymentService(db_session)._bind_payment(
            f"local-reservation-{uuid.uuid4()}",
            [
                (["CAP-S", "CAP-R"], Decimal("40")),
                (["CAP-S", "CAP-R"], Decimal("40")),
            ],
            equivalent_id,
        )

    # The second route of the SAME payment counts the first one (40) on top of the reservation (7):
    # routing may not spend more capacity than the segment has (AGENTS §8).
    assert Decimal(error.value.details["available"]) == Decimal("80")
    assert Decimal(error.value.details["needed"]) == Decimal("40")
    assert Decimal(error.value.details["reserved"]) == Decimal("47")
