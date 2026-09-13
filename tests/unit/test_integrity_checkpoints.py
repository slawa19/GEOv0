import uuid
from decimal import Decimal

import pytest

from app.core.integrity import (
    compute_and_store_integrity_checkpoints,
    compute_integrity_checkpoint_for_equivalent,
)
from app.core.invariants import InvariantChecker
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup


@pytest.mark.asyncio
async def test_integrity_checkpoint_propagates_unavailable_invariant_checker(
    db_session,
    monkeypatch,
):
    async def _checker_unavailable(*_args, **_kwargs):
        raise RuntimeError("invariant checker unavailable")

    # Patches `check_trust_limits`, not `check_zero_sum`. Until T1402 this patched zero-sum,
    # which the checkpoint no longer calls at all - so the probe stopped firing and this test
    # would have passed with the propagation it exists to prove removed entirely. The subject is
    # unchanged: a checker that raises must not be swallowed into a healthy checkpoint.
    monkeypatch.setattr(InvariantChecker, "check_trust_limits", _checker_unavailable)

    with pytest.raises(RuntimeError, match="invariant checker unavailable"):
        await compute_integrity_checkpoint_for_equivalent(
            db_session,
            equivalent_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_checkpoint_batch_fails_and_rolls_back_when_checker_is_unavailable(
    db_session,
    monkeypatch,
):
    equivalent = Equivalent(
        code="BATCH",
        symbol="B",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    db_session.add(equivalent)
    await db_session.commit()

    async def _checker_unavailable(*_args, **_kwargs):
        raise RuntimeError("batch checker unavailable")

    # Patches `check_trust_limits`, not `check_zero_sum`. Until T1402 this patched zero-sum,
    # which the checkpoint no longer calls at all - so the probe stopped firing and this test
    # would have passed with the propagation it exists to prove removed entirely. The subject is
    # unchanged: a checker that raises must not be swallowed into a healthy checkpoint.
    monkeypatch.setattr(InvariantChecker, "check_trust_limits", _checker_unavailable)

    with pytest.raises(RuntimeError, match="batch checker unavailable"):
        await compute_and_store_integrity_checkpoints(db_session)

    assert db_session.in_transaction() is False


@pytest.mark.asyncio
async def test_integrity_checkpoint_records_invariant_checks_when_healthy(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("I" + nonce[:15]).upper(), symbol="I", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    async with debt_fixture_setup(db_session, label="setup"):
        debt = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("5"))
        db_session.add(debt)
        db_session.add(
            TrustLine(
                from_participant_id=b.id,
                to_participant_id=a.id,
                equivalent_id=eq.id,
                limit=Decimal("100"),
                status="active",
                policy={"auto_clearing": True},
            )
        )
    await db_session.commit()

    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)

    status = cp.invariants_status
    assert status["status"] == "healthy"
    assert status["passed"] is True
    assert status.get("alerts") == []
    assert set(status.get("checks", {}).keys()) == {"zero_sum", "trust_limits", "debt_symmetry"}

    # zero_sum carries NO verdict since T1402. `passed` must be absent, not false: an absent
    # verdict and a failed one are different statements, and the whole point of the withdrawal is
    # that this check makes neither.
    assert status["checks"]["zero_sum"] == {"status": "not_verified", "reason": "check_withdrawn"}
    assert "passed" not in status["checks"]["zero_sum"]
    assert status.get("unverified") == ["zero_sum"]

    assert status["checks"]["trust_limits"]["passed"] is True
    assert status["checks"]["debt_symmetry"]["passed"] is True

    # WAS the whole assertion set. `zero_sum.passed is True` compared a literal written two lines
    # away in app/core/integrity.py to a literal here, one indirection deep, because the `except`
    # branch beside it was unreachable (F-014-2 / `C-A4-4-003`, T1403). What follows are
    # properties of THIS fixture that a production defect can actually break.

    # The counts are data-derived and were asserted by nothing.
    assert status["debts_count"] == 1
    assert status["trustlines_count"] == 1
    assert status["debts_non_negative"] is True
    assert status["debts_negative_count"] == 0

    # The checksum is deterministic - the ordering tie-breakers in
    # compute_integrity_checkpoint_for_equivalent exist precisely for this and were untested.
    cp_again = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)
    assert cp_again.checksum == cp.checksum

    # And it is sensitive to the exact corruption the withdrawn check could not see. This pairs
    # the two facts in one place: the CHECKSUM notices a one-cent inflation, the zero_sum entry
    # still carries no verdict, and neither statement can be mistaken for the other.
    # DECLARED, because it IS a movement of money: raising a stored debt by a cent is exactly what
    # the journal asks a writer to name, and this test's subject is the checksum noticing it.
    async with debt_fixture_setup(db_session, label="one-cent-inflation"):
        debt.amount += Decimal("0.01")
    await db_session.flush()
    cp_after = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)
    assert cp_after.checksum != cp.checksum
    assert cp_after.invariants_status["checks"]["zero_sum"] == {
        "status": "not_verified",
        "reason": "check_withdrawn",
    }


@pytest.mark.asyncio
async def test_integrity_checkpoint_marks_trust_limit_violation_as_critical(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("L" + nonce[:15]).upper(), symbol="L", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")))
        db_session.add(
            TrustLine(
                from_participant_id=b.id,
                to_participant_id=a.id,
                equivalent_id=eq.id,
                limit=Decimal("5"),
                status="active",
                policy={"auto_clearing": True},
            )
        )
    await db_session.commit()

    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)

    status = cp.invariants_status
    assert status["status"] == "critical"
    assert status["passed"] is False
    assert "trust_limits" in (status.get("alerts") or [])
    assert status["checks"]["trust_limits"]["passed"] is False
    assert status["checks"]["trust_limits"]["violations"] >= 1


@pytest.mark.asyncio
async def test_integrity_checkpoint_marks_debt_symmetry_violation_as_warning(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("S" + nonce[:15]).upper(), symbol="S", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(
            [
                Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("5")),
                Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("3")),
            ]
        )
        db_session.add_all(
            [
                TrustLine(
                    from_participant_id=b.id,
                    to_participant_id=a.id,
                    equivalent_id=eq.id,
                    limit=Decimal("100"),
                    status="active",
                    policy={"auto_clearing": True},
                ),
                TrustLine(
                    from_participant_id=a.id,
                    to_participant_id=b.id,
                    equivalent_id=eq.id,
                    limit=Decimal("100"),
                    status="active",
                    policy={"auto_clearing": True},
                ),
            ]
        )
    await db_session.commit()

    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)

    status = cp.invariants_status
    assert status["status"] == "warning"
    assert status["passed"] is False
    assert "debt_symmetry" in (status.get("alerts") or [])
    assert status["checks"]["debt_symmetry"]["passed"] is False
