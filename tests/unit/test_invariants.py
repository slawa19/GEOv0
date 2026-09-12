import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.clearing.service import ClearingService
from app.core.integrity import compute_integrity_checkpoint_for_equivalent
from app.core.invariants import InvariantChecker
from app.core.payments.engine import PaymentEngine
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException

from tests.debt_setup import debt_fixture_setup


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("commit_changes", "expected_tx_lock_already_held"),
    [(True, False), (False, True)],
)
async def test_payment_commit_aborts_on_trust_limit_violation(
    db_session,
    monkeypatch,
    commit_changes,
    expected_tx_lock_already_held,
):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce[:15]).upper(), symbol="T", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b])
    await db_session.flush()

    # Debt(A->B) is controlled by trustline(B->A)
    db_session.add(
        TrustLine(
            from_participant_id=b.id,
            to_participant_id=a.id,
            equivalent_id=eq.id,
            limit=Decimal("10"),
            status="active",
        )
    )

    tx_id = "tx-" + uuid.uuid4().hex
    db_session.add(
        Transaction(
            tx_id=tx_id,
            type="PAYMENT",
            initiator_id=a.id,
            payload={},
            signatures=[],
            state="PREPARED",
        )
    )
    # prepare_locks.tx_id references transactions.tx_id with no ORM relationship, so the
    # flush does not order the two inserts; write the transaction first.
    await db_session.flush()
    db_session.add(
        PrepareLock(
            tx_id=tx_id,
            participant_id=a.id,
            effects={
                "flows": [
                    {
                        "from": str(a.id),
                        "to": str(b.id),
                        "amount": "20",
                        "equivalent": str(eq.id),
                    }
                ]
            },
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.flush()

    engine = PaymentEngine(db_session)

    abort_called = {
        "called": False,
        "tx_lock_already_held": False,
        "equivalent_owner_locks_already_held": False,
    }

    async def _abort_noop(
        _tx_id: str,
        reason: str = "Aborted",
        *,
        commit: bool = True,
        error_code: str | None = None,
        details: dict | None = None,
        _tx_lock_already_held: bool = False,
        _equivalent_owner_locks_already_held: bool = False,
    ):
        abort_called["called"] = True
        abort_called["tx_lock_already_held"] = _tx_lock_already_held
        abort_called["equivalent_owner_locks_already_held"] = (
            _equivalent_owner_locks_already_held
        )
        return True

    async def _rollback_noop():
        return None

    async def _no_commit_with_retry():
        await db_session.flush()

    # P0.1: commit-only retry was removed; keep the test deterministic by
    # bypassing the whole-uow retry wrapper.
    async def _run_uow_no_retry(*, op: str, fn, use_savepoint: bool = False):
        return await fn()

    monkeypatch.setattr(engine, "_run_uow_with_retry", _run_uow_no_retry)
    monkeypatch.setattr(engine, "abort", _abort_noop)
    monkeypatch.setattr(db_session, "rollback", _rollback_noop)

    with pytest.raises(IntegrityViolationException) as exc_info:
        await engine.commit(tx_id, commit=commit_changes)

    assert exc_info.value.code == "E008"
    assert exc_info.value.details.get("invariant") == "TRUST_LIMIT_VIOLATION"
    assert abort_called["called"] is True
    assert (
        abort_called["tx_lock_already_held"] is expected_tx_lock_already_held
    )
    assert (
        abort_called["equivalent_owner_locks_already_held"]
        is expected_tx_lock_already_held
    )


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


@pytest.mark.asyncio
async def test_payment_commit_writes_integrity_audit_log_on_success(
    db_session,
    monkeypatch,
):
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

    tx_id = "tx-" + uuid.uuid4().hex
    db_session.add(
        Transaction(
            tx_id=tx_id,
            type="PAYMENT",
            initiator_id=a.id,
            payload={
                "from": a.pid,
                "to": b.pid,
                "amount": "1",
                "equivalent": eq.code,
                "path": [a.pid, b.pid],
            },
            signatures=[],
            state="PREPARED",
        )
    )
    await db_session.flush()
    db_session.add(
        PrepareLock(
            tx_id=tx_id,
            participant_id=a.id,
            effects={
                "flows": [
                    {
                        "from": str(a.id),
                        "to": str(b.id),
                        "amount": "1",
                        "equivalent": str(eq.id),
                    }
                ]
            },
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.commit()
    expected_audit_participants = sorted([a.pid, b.pid])

    engine = PaymentEngine(db_session)
    original_apply_flow = engine._apply_flow

    async def _apply_flow_and_expire(*args, **kwargs):
        await original_apply_flow(*args, **kwargs)
        db_session.expire_all()

    monkeypatch.setattr(engine, "_apply_flow", _apply_flow_and_expire)
    assert await engine.commit(tx_id) is True

    log = (
        await db_session.execute(
            select(IntegrityAuditLog).where(
                IntegrityAuditLog.operation_type == "PAYMENT",
                IntegrityAuditLog.tx_id == tx_id,
            )
        )
    ).scalar_one()
    assert log.verification_passed is True
    assert log.affected_participants == {
        "participants": expected_audit_participants,
    }

    locks = (
        (await db_session.execute(select(PrepareLock).where(PrepareLock.tx_id == tx_id)))
        .scalars()
        .all()
    )
    assert locks == []


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
        (await db_session.execute(select(Debt).where(Debt.equivalent_id == eq.id)))
        .scalars()
        .all()
    )
    assert remaining == []
