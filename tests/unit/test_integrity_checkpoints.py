import uuid
from decimal import Decimal

import pytest

from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_integrity_checkpoint_records_invariant_checks_when_healthy(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("I" + nonce[:15]).upper(), symbol="I", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    db_session.add(Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("5")))
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
    assert status["checks"]["zero_sum"]["passed"] is True
    assert status["checks"]["trust_limits"]["passed"] is True
    assert status["checks"]["debt_symmetry"]["passed"] is True


@pytest.mark.asyncio
async def test_integrity_checkpoint_marks_trust_limit_violation_as_critical(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("L" + nonce[:15]).upper(), symbol="L", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

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
