import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.clearing.service import ClearingService
from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.invariants import InvariantChecker
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException

from tests.debt_setup import debt_fixture_setup, writer_operation
from tests.conftest import MODE_B, sessionmaker_of


@pytest.mark.asyncio
async def test_edge_model_attributes_a_debt_directionally(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce[:15]).upper(), symbol="T", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("100")))
    await db_session.flush()

    checker = InvariantChecker(db_session)

    # WAS `assert await checker.check_zero_sum(equivalent_id=eq.id) == {}` - true for any data
    # whatsoever, and therefore not a test of anything this fixture set up (F-014-2, T1403).
    # `_compute_imbalance` sums the SAME `Debt` rows grouped by creditor and grouped by debtor and
    # returns the difference, so it telescopes to zero for every input; `{}` was the only
    # reachable return. Deleting it outright would shrink coverage along with the false
    # confidence, so it is REPLACED by the falsifiable claim this fixture actually supports.
    #
    # The claim: the edge model attributes a debt DIRECTIONALLY. That is exactly what the old
    # assertion was blind to - swapping `Debt.creditor_id` for `Debt.debtor_id` in either
    # aggregate of `_compute_imbalance` leaves its total at zero, and left `== {}` green.
    assert await checker._calculate_net_position(a.id, eq.id) == Decimal("-100")
    assert await checker._calculate_net_position(b.id, eq.id) == Decimal("100")

    # And the documented rule that a missing trustline is a limit of zero: this fixture creates
    # no trustline at all, so a debt of 100 must violate. An inner join in place of the outer one
    # at app/core/invariants.py, or a different coalesce default, reddens this.
    with pytest.raises(IntegrityViolationException) as exc_info:
        await checker.check_trust_limits(equivalent_id=eq.id)
    violations = exc_info.value.details["violations"]
    assert len(violations) == 1
    from decimal import Decimal as _D

    assert _D(violations[0]["trust_limit"]) == _D("0")
    assert _D(violations[0]["debt_amount"]) == _D("100")

    # Zero-sum itself is no longer published as a check at all (T1402). What the wire now says is
    # asserted where it is produced - tests/integration/test_integrity_endpoints.py and
    # tests/unit/test_integrity_checkpoints.py - not here.


@pytest.mark.asyncio
async def test_trust_limit_violation_detected(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce[:15]).upper(), symbol="T", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    # Controlling trustline for debt(B->A) is trustline(A->B)
    db_session.add(
        TrustLine(
            from_participant_id=a.id,
            to_participant_id=b.id,
            equivalent_id=eq.id,
            limit=Decimal("100"),
            status="active",
        )
    )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("150")))
    await db_session.flush()

    checker = InvariantChecker(db_session)
    with pytest.raises(IntegrityViolationException) as exc_info:
        await checker.check_trust_limits(equivalent_id=eq.id)

    assert exc_info.value.code == "E008"
    assert exc_info.value.details.get("invariant") == "TRUST_LIMIT_VIOLATION"


# `test_payment_commit_aborts_on_trust_limit_violation` drove `PaymentEngine.commit` over a hand-seeded
# PREPARED payment and a reservation (both refused by migration 030 since programme 019 stage 4) and
# asserted the engine's abort call shape. The engine is gone; the effect - a payment whose debts end
# over the creditor's limit is refused with E008 TRUST_LIMIT_VIOLATION and nothing of it lands - is held
# on the real payment path by `tests/integration/test_p019_direct_execution_effects_postgres.py::
# test_a_payment_that_ends_over_a_limit_is_refused_and_leaves_nothing_but_its_refusal` (manifest t1901,
# 5.2, rows of this file). The checker's own refusal stays above (`test_trust_limit_violation_detected`).


@pytest.mark.asyncio
async def test_clearing_neutrality_passes_for_cycle_clearing(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce[:15]).upper(), symbol="T", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    c = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    # A -> B -> C -> A cycle
    async with debt_fixture_setup(db_session, label="setup"):
        d_ab = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10"))
        d_bc = Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10"))
        d_ca = Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10"))
        db_session.add_all([d_ab, d_bc, d_ca])
    await db_session.flush()

    checker = InvariantChecker(db_session)
    participants = [a.id, b.id, c.id]
    positions_before = {pid: await checker._calculate_net_position(pid, eq.id) for pid in participants}

    # A CLEARING'S OWN OPERATION. These statements are the movement a clearing makes, written by
    # hand so the invariant checker can be pointed at the result; the journal asks every movement of
    # money to name the operation that made it, and `CLEARING` is what the real writer declares
    # (`app/core/clearing/service.py`). Not `debt_fixture_setup`: this is not setup, it is the thing
    # under test.
    async with writer_operation(
        db_session, kind="CLEARING", equivalent_ids=[eq.id], initiator_id=a.id
    ):
        # Clearing by full min amount reduces all edges equally.
        d_ab.amount -= Decimal("10")
        d_bc.amount -= Decimal("10")
        d_ca.amount -= Decimal("10")

        if d_ab.amount == 0:
            await db_session.delete(d_ab)
        if d_bc.amount == 0:
            await db_session.delete(d_bc)
        if d_ca.amount == 0:
            await db_session.delete(d_ca)
        await db_session.flush()

    assert await checker.verify_clearing_neutrality(participants, eq.id, positions_before) is True


@pytest.mark.asyncio
async def test_clearing_neutrality_violation_detected(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce[:15]).upper(), symbol="T", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    c = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    async with debt_fixture_setup(db_session, label="setup"):
        d_ab = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10"))
        d_bc = Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10"))
        d_ca = Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10"))
        db_session.add_all([d_ab, d_bc, d_ca])
    await db_session.flush()

    checker = InvariantChecker(db_session)
    participants = [a.id, b.id, c.id]
    positions_before = {pid: await checker._calculate_net_position(pid, eq.id) for pid in participants}

    # A CLEARING'S OWN OPERATION. These statements are the movement a clearing makes, written by
    # hand so the invariant checker can be pointed at the result; the journal asks every movement of
    # money to name the operation that made it, and `CLEARING` is what the real writer declares
    # (`app/core/clearing/service.py`). Not `debt_fixture_setup`: this is not setup, it is the thing
    # under test.
    async with writer_operation(
        db_session, kind="CLEARING", equivalent_ids=[eq.id], initiator_id=a.id
    ):
        # Break neutrality: modify only one edge.
        d_ab.amount -= Decimal("10")

        if d_ab.amount == 0:
            await db_session.delete(d_ab)
        await db_session.flush()

    with pytest.raises(IntegrityViolationException) as exc_info:
        await checker.verify_clearing_neutrality(participants, eq.id, positions_before)

    assert exc_info.value.code == "E008"
    assert exc_info.value.details.get("invariant") == "CLEARING_NEUTRALITY_VIOLATION"


@pytest.mark.asyncio
async def test_integrity_checkpoint_status_critical_for_trust_limits(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("T" + nonce[:15]).upper(),
        symbol="T",
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

    # Controlling trustline for debt(B->A) is trustline(A->B)
    db_session.add(
        TrustLine(
            from_participant_id=a.id,
            to_participant_id=b.id,
            equivalent_id=eq.id,
            limit=Decimal("100"),
            status="active",
        )
    )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(
            Debt(
                debtor_id=b.id,
                creditor_id=a.id,
                equivalent_id=eq.id,
                amount=Decimal("150"),
            )
        )
    await db_session.flush()

    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)
    status = (cp.invariants_status or {}).get("status")
    assert status == "critical"
    assert (cp.invariants_status or {}).get("passed") is False
    checks = (cp.invariants_status or {}).get("checks") or {}
    assert checks.get("trust_limits", {}).get("passed") is False


@pytest.mark.asyncio
async def test_integrity_checkpoint_status_warning_for_debt_symmetry(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("T" + nonce[:15]).upper(),
        symbol="T",
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

    # Provide trustlines so limits are not the failing invariant.
    db_session.add_all(
        [
            TrustLine(
                from_participant_id=a.id,
                to_participant_id=b.id,
                equivalent_id=eq.id,
                limit=Decimal("100"),
                status="active",
            ),
            TrustLine(
                from_participant_id=b.id,
                to_participant_id=a.id,
                equivalent_id=eq.id,
                limit=Decimal("100"),
                status="active",
            ),
        ]
    )

    # Mutual debts create a symmetry warning in checkpoints.
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add_all(
            [
                Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("1")),
                Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("2")),
            ]
        )
    await db_session.flush()

    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)
    status = (cp.invariants_status or {}).get("status")
    assert status == "warning"
    assert (cp.invariants_status or {}).get("passed") is False
    checks = (cp.invariants_status or {}).get("checks") or {}
    assert checks.get("debt_symmetry", {}).get("passed") is False


@MODE_B
@pytest.mark.asyncio
async def test_payment_commit_writes_integrity_audit_log_on_success(
    db_session,
    monkeypatch,
):
    """FIX-014 on the real payment path: one passed `IntegrityAuditLog` row naming the route's participants.

    Since programme 019 stage 4 the payment executes directly (`PaymentService._apply_payment`) - no
    engine commit of a seeded PREPARED payment. THE PERTURBATION is kept: after the book applies the
    flow, every ORM object of the session is expired, so the audit write must not depend on state the
    flow left loaded (in an async session a lazy load there raises). It is only evidence if the payment
    really goes through the perturbed seam - once per flow.
    """

    import app.core.ledger.book as book_module
    from app.schemas.payment import PaymentCreateRequest

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("T" + nonce[:15]).upper(),
        symbol="T",
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

    # Debt(A->B) is controlled by trustline(B->A)
    db_session.add(
        TrustLine(
            from_participant_id=b.id,
            to_participant_id=a.id,
            equivalent_id=eq.id,
            limit=Decimal("100"),
            status="active",
        )
    )
    await db_session.commit()
    expected_audit_participants = sorted([a.pid, b.pid])

    original_apply_payment_flow = book_module._apply_payment_flow
    perturbed: list[object] = []

    async def _apply_flow_and_expire(session, flow):
        result = await original_apply_payment_flow(session, flow)
        session.expire_all()
        perturbed.append(flow)
        return result

    monkeypatch.setattr(book_module, "_apply_payment_flow", _apply_flow_and_expire)
    request = PaymentCreateRequest(
        tx_id="tx-" + uuid.uuid4().hex,
        to=b.pid,
        equivalent=eq.code,
        amount="1",
        signature="__internal__",
    )
    try:
        result = await PaymentService.pay(
            sessionmaker_of(db_session), a.id, request, require_signature=False
        )
    finally:
        PaymentRouter.invalidate_cache(eq.code)
    assert result.status == "COMMITTED", result
    assert len(perturbed) == 1, perturbed

    async with sessionmaker_of(db_session)() as observer:
        log = (
            await observer.execute(
                select(IntegrityAuditLog).where(
                    IntegrityAuditLog.operation_type == "PAYMENT",
                    IntegrityAuditLog.tx_id == request.tx_id,
                )
            )
        ).scalar_one()
    assert log.verification_passed is True
    assert log.affected_participants == {
        "participants": expected_audit_participants,
    }


@MODE_B
@pytest.mark.asyncio
async def test_clearing_writes_integrity_audit_log_on_success(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("T" + nonce[:15]).upper(),
        symbol="T",
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
    c = Participant(
        pid="C" + nonce,
        display_name="C",
        public_key="pkC-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    # Trustlines controlling the cycle debts, auto-clearing enabled by default policy.
    db_session.add_all(
        [
            TrustLine(
                from_participant_id=b.id,
                to_participant_id=a.id,
                equivalent_id=eq.id,
                limit=Decimal("100"),
                status="active",
            ),
            TrustLine(
                from_participant_id=c.id,
                to_participant_id=b.id,
                equivalent_id=eq.id,
                limit=Decimal("100"),
                status="active",
            ),
            TrustLine(
                from_participant_id=a.id,
                to_participant_id=c.id,
                equivalent_id=eq.id,
                limit=Decimal("100"),
                status="active",
            ),
        ]
    )

    async with debt_fixture_setup(db_session, label="setup"):
        d_ab = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10"))
        d_bc = Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10"))
        d_ca = Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10"))
        db_session.add_all([d_ab, d_bc, d_ca])
    await db_session.commit()

    # Captured as plain values BEFORE clearing. On PostgreSQL clearing runs on its own interlock
    # connection and ends the caller's transaction first (`_rollback_before_interlock`,
    # `app/core/clearing/service.py`), and a rollback expires every instance in this session: reading
    # `eq.id` afterwards is a lazy load, which async SQLAlchemy refuses with `MissingGreenlet`.
    # SQLite executes on this session and never rolled it back, so the stale read went unnoticed;
    # mode B is the first time this test reached the PostgreSQL path (017 stage 2b).
    eq_id = eq.id

    svc = ClearingService(db_session)
    cleared = await svc.execute_clearing_with_amount(
        [{"debt_id": str(d_ab.id)}, {"debt_id": str(d_bc.id)}, {"debt_id": str(d_ca.id)}]
    )
    assert cleared == Decimal("10")

    tx = (
        await db_session.execute(select(Transaction).where(Transaction.type == "CLEARING"))
    ).scalar_one()
    assert tx.state == "COMMITTED"

    log = (
        await db_session.execute(
            select(IntegrityAuditLog).where(
                IntegrityAuditLog.operation_type == "CLEARING",
                IntegrityAuditLog.tx_id == tx.tx_id,
            )
        )
    ).scalar_one()
    assert log.verification_passed is True

    remaining = (
        (await db_session.execute(select(Debt).where(Debt.equivalent_id == eq_id)))
        .scalars()
        .all()
    )
    assert remaining == []
