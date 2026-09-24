"""RT-015-12 / T1522: the payment delta check accepts being wrong by one whole atom.

WHAT THE CHECK IS FOR. `MoneyBoundary.check_payment_delta` (`app/core/money_boundary.py`) is the barrier between "the flows we
intended" and "what the ledger actually holds". It recomputes each participant's net position after
the write and compares the movement against the flows that were applied. It is the only place on
the payment path that compares an intention with a durable effect - `check_trust_limits` and
`check_debt_symmetry` judge the resulting state, not the movement, and zero-sum was withdrawn by
programme 014 because it could not fail at all.

THE BLIND SPOT, AND WHY IT IS EXACTLY ONE QUANTUM AND NOT LESS. The comparison is
`abs(drift) > Decimal("0.00000001")`, strict, against exactly one quantum of `Numeric(20, 8)`.
Storage is scale 8 and, since 012/T1201, the door refuses any amount the column cannot hold
unchanged, so every net position and every flow is a scale-8 value and every possible drift is a
MULTIPLE of 1e-8. There is no sub-quantum drift left to tolerate - that was the 012-era phenomenon,
when the door accepted scale 18 and the column rounded underneath it. What the tolerance actually
admits today is the smallest drift that can exist: the ledger moving by one atom more, or less,
than the payment said.

So the barrier is set to ignore precisely the corruption it is positioned to catch. One atom per
payment, undetected, is the arithmetic form of the owner's sentence: "if the system goes off the
rails, the error will accumulate."

WHAT THESE TESTS DO. `test_one_quantum_of_drift_is_detected` is the reproducer and it is RED before
the fix: it applies a debt one atom larger than the flow it declares, and today the check certifies
it. The two neighbours are controls, and they are the reason the fix cannot be "raise on
everything": an exact match must stay silent, and a drift the check already caught must keep being
caught with the same shape of report.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money_boundary import MoneyBoundary
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.utils.exceptions import IntegrityViolationException

from tests.debt_setup import debt_fixture_setup

_QUANTUM = Decimal("0.00000001")


async def _seed(db_session: AsyncSession, stored_amount: Decimal):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("Q" + nonce[:15]).upper(),
        symbol="Q",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    sender = Participant(
        pid="qs" + nonce, display_name="S", public_key="pkqs-" + nonce,
        type="person", status="active", profile={},
    )
    receiver = Participant(
        pid="qr" + nonce, display_name="R", public_key="pkqr-" + nonce,
        type="person", status="active", profile={},
    )
    db_session.add_all([eq, sender, receiver])
    await db_session.flush()
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(
            Debt(
                debtor_id=sender.id,
                creditor_id=receiver.id,
                equivalent_id=eq.id,
                amount=stored_amount,
            )
        )
    await db_session.commit()
    return eq, sender, receiver


@pytest.mark.asyncio
async def test_one_quantum_of_drift_is_detected(db_session: AsyncSession) -> None:
    """The reproducer. RED before T1522, and the drift is the smallest one that can exist.

    The flow declares 10.00000000; the ledger holds 10.00000001. One atom appeared that no payment
    asked for. `abs(1E-8) > 1E-8` is False, so the barrier reports nothing and the payment commits.
    """
    declared = Decimal("10.00000000")
    stored = declared + _QUANTUM
    eq, sender, receiver = await _seed(db_session, stored)

    engine = MoneyBoundary(db_session)

    with pytest.raises(IntegrityViolationException) as exc:
        await engine.check_payment_delta(
            equivalent_id=eq.id,
            flows=[(sender.id, receiver.id, declared)],
            net_positions_before={sender.id: Decimal("0"), receiver.id: Decimal("0")},
        )

    details = exc.value.details
    assert details.get("invariant") == "PAYMENT_DELTA_DRIFT"
    drifts = {d["participant_id"]: Decimal(d["drift"]) for d in details["drifts"]}
    # The debtor's net position went one atom lower than declared, the creditor's one atom higher.
    assert drifts[sender.pid] == -_QUANTUM
    assert drifts[receiver.pid] == _QUANTUM


@pytest.mark.asyncio
async def test_an_exact_match_stays_silent(db_session: AsyncSession) -> None:
    """Control. Tightening the barrier must not turn every payment into a violation.

    Without this the fix could be "raise whenever anything is compared" and the reproducer above
    would still pass - which is the shape of false green this repository has paid for repeatedly.
    """
    declared = Decimal("10.00000000")
    eq, sender, receiver = await _seed(db_session, declared)

    engine = MoneyBoundary(db_session)
    await engine.check_payment_delta(
        equivalent_id=eq.id,
        flows=[(sender.id, receiver.id, declared)],
        net_positions_before={sender.id: Decimal("0"), receiver.id: Decimal("0")},
    )


@pytest.mark.asyncio
async def test_a_drift_the_barrier_already_caught_is_still_caught(
    db_session: AsyncSession,
) -> None:
    """Control on the other side: two quanta were detected before T1522 and must stay detected.

    It also pins the REPORT, not just the raise. `total_drift` is the sum of absolute drifts halved
    - both sides of one movement are the same discrepancy seen twice - and a fix that changed the
    comparison could quietly change that too.
    """
    declared = Decimal("10.00000000")
    stored = declared + 2 * _QUANTUM
    eq, sender, receiver = await _seed(db_session, stored)

    engine = MoneyBoundary(db_session)
    with pytest.raises(IntegrityViolationException) as exc:
        await engine.check_payment_delta(
            equivalent_id=eq.id,
            flows=[(sender.id, receiver.id, declared)],
            net_positions_before={sender.id: Decimal("0"), receiver.id: Decimal("0")},
        )

    details = exc.value.details
    assert Decimal(str(details["total_drift"])) == 2 * _QUANTUM
    assert details.get("equivalent") == eq.code


def test_the_barrier_compares_against_zero_and_not_against_a_quantum() -> None:
    """The source-level pin, because the two controls above pass at either setting.

    `test_one_quantum_of_drift_is_detected` is what proves the behaviour, and it would also pass at
    a tolerance of, say, 5e-9 - a value that still admits nothing real, but also says nothing. This
    states the intended constant so that widening it is a visible edit rather than a silent one.

    It reads the module constant rather than the method's source text: the constant is what the
    012 counter-check patches, and a pin on source text would have gone green the moment the value
    moved out of the function.
    """
    from app.core.money_boundary import _DELTA_DRIFT_TOLERANCE

    assert _DELTA_DRIFT_TOLERANCE == Decimal("0"), (
        "the payment delta barrier must compare against exact zero: on a scale-8 ledger every "
        "possible drift is a multiple of one quantum, so any non-zero tolerance admits the "
        "smallest corruption that can occur"
    )
