"""The capacity of a payment segment and the routes of one payment over it (AGENTS §8 routing).

The capacity formula is protected: `limit(receiver -> sender) - debt(sender -> receiver) + debt(receiver
-> sender)`, and several routes of ONE payment over one segment are summed against it. Until programme
019 stage 4 this was measured on the engine's private `_get_segment_capacity_and_reserved_usage` and its
`prepare`/`prepare_routes`; since stage 4 the payment path computes it in `PaymentService._bind_payment`
(`_segment_capacity`), and this module measures it there (manifest t1901, 5.1, rows of this file). The
single-route and multi-route entries are one function now, so the old "the two entries give identical
details" comparison has no second entry to compare with; each refusal's details are asserted directly.

Since programme 019 stage 5 (`T1909`) there are no reservations: `prepare_locks` and every reader of it
are gone, so `reserved` in an `E002` refusal is only what EARLIER ROUTES OF THE SAME PAYMENT claim on the
segment. The former "only same-equivalent, same-direction, valid reservations count (7 of 7/50/60/999)"
assertion was the removed reservation contract and is dropped with it; the capacity formula and the
own-route accounting stay.
"""

from __future__ import annotations

from decimal import Decimal
import uuid

import pytest

from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RoutingException

from tests.debt_setup import debt_fixture_setup


async def _seed_capacity_policy_case(
    db_session,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    sender_id = uuid.uuid4()
    receiver_id = uuid.uuid4()
    equivalent_id = uuid.uuid4()

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
            ]
        )
    await db_session.commit()
    return sender_id, receiver_id, equivalent_id


@pytest.mark.asyncio
async def test_segment_capacity_is_the_limit_less_the_debt_plus_the_reverse_debt(
    db_session,
):
    sender_id, receiver_id, equivalent_id = await _seed_capacity_policy_case(db_session)

    available = await PaymentService(db_session)._segment_capacity(
        sender_id=sender_id,
        receiver_id=receiver_id,
        equivalent_id=equivalent_id,
    )

    # 100 - 30 + 10: the limit, less what the sender owes, plus what the receiver owes back.
    assert available == Decimal("80")


@pytest.mark.asyncio
async def test_a_single_route_refusal_reports_the_capacity_and_the_need(
    db_session,
):
    _sender_id, _receiver_id, equivalent_id = await _seed_capacity_policy_case(db_session)
    service = PaymentService(db_session)

    # Counter-check: the whole capacity is spendable, so the refusal below is the formula's edge and
    # not a segment that refuses everything.
    await service._bind_payment(
        f"single-fits-{uuid.uuid4()}",
        [(["CAP-S", "CAP-R"], Decimal("80"))],
        equivalent_id,
    )

    with pytest.raises(RoutingException) as error:
        await service._bind_payment(
            f"single-{uuid.uuid4()}",
            [(["CAP-S", "CAP-R"], Decimal("80.01"))],
            equivalent_id,
        )

    assert Decimal(error.value.details["available"]) == Decimal("80")
    assert Decimal(error.value.details["needed"]) == Decimal("80.01")
    # No other transaction reserves anything, and a single route has no earlier route of its own.
    assert Decimal(error.value.details["reserved"]) == Decimal("0")
    assert (error.value.details["from"], error.value.details["to"]) == ("CAP-S", "CAP-R")


@pytest.mark.asyncio
async def test_multipath_counts_its_own_earlier_routes_over_one_segment(
    db_session,
):
    _sender_id, _receiver_id, equivalent_id = await _seed_capacity_policy_case(db_session)

    with pytest.raises(RoutingException) as error:
        await PaymentService(db_session)._bind_payment(
            f"local-reservation-{uuid.uuid4()}",
            [
                (["CAP-S", "CAP-R"], Decimal("50")),
                (["CAP-S", "CAP-R"], Decimal("50")),
            ],
            equivalent_id,
        )

    # Each route alone fits (50 <= 80); the second is refused only because the first route of the
    # SAME payment already claims 50 of the segment: routing may not spend more capacity than the
    # segment has (AGENTS §8).
    assert Decimal(error.value.details["available"]) == Decimal("80")
    assert Decimal(error.value.details["needed"]) == Decimal("50")
    assert Decimal(error.value.details["reserved"]) == Decimal("50")
