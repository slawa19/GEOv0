"""T1543: a legally frozen trust line is compared with its stored limit, not with zero.

THE DEFECT. `check_trust_limits` outer-joined a debt only to an `active` trust line and substituted
a limit of zero when none matched, so a debt on a FROZEN line counted as exceeding a limit of zero.
Three observable consequences followed, and each has a test below that was red before the fix:

* the checkpoint of any equivalent holding such a line was `critical`;
* every payment in that equivalent wrote an audit row with `verification_passed=false`;
* a payment that partly repaid the debt on a frozen line was ABORTED as a trust-limit violation,
  because the commit-time check saw the remaining debt against a limit of zero.

THE RULE, decided by the Codex plan review of 2026-09-13 against `docs/ru/02-protocol-spec.md`
§3.3 (`∀ (from, to, equivalent): debt[to→from] ≤ limit`, with no `active` condition, for statuses
`active | frozen | closed`) and §11.5.2 (whose first reaction to a violation is to freeze the line):

* `active` and `frozen` - the debt is compared with the stored limit;
* `closed`, or no live line at all - the permitted debt is zero.

Freezing means the line offers no NEW routing capacity; that is routing's job
(`app/core/payments/router.py`, `PaymentEngine._get_segment_capacity_and_reserved_usage`), not the
invariant's. The controls below keep the rule from sliding into its two wrong neighbours: excluding
frozen lines from the check would hide the very breach §11.5.2 froze the line for, and counting a
closed line at its stored limit would let history authorise debt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

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


def _equivalent(nonce: str) -> Equivalent:
    return Equivalent(
        code=("Q" + nonce[:15]).upper(),
        symbol="Q",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )


def _participant(label: str, nonce: str) -> Participant:
    return Participant(
        pid=label + nonce,
        display_name=label,
        public_key=f"pk{label}-{nonce}",
        type="person",
        status="active",
        profile={},
    )


async def _line_with_debt(
    db_session,
    *,
    status: str | None,
    limit: str,
    debt: str,
) -> tuple[Equivalent, Participant, Participant]:
    """Creditor trusts debtor on a line of `status` (None: no line at all); debtor owes `debt`."""
    nonce = uuid.uuid4().hex[:10]
    eq = _equivalent(nonce)
    creditor = _participant("C", nonce)
    debtor = _participant("D", nonce)
    db_session.add_all([eq, creditor, debtor])
    await db_session.flush()

    if status is not None:
        db_session.add(
            TrustLine(
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=eq.id,
                limit=Decimal(limit),
                status=status,
            )
        )
    async with debt_fixture_setup(db_session, label="setup"):
        db_session.add(
            Debt(
                debtor_id=debtor.id,
                creditor_id=creditor.id,
                equivalent_id=eq.id,
                amount=Decimal(debt),
            )
        )
    await db_session.commit()
    return eq, creditor, debtor


# --- the defect ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_debt_within_the_limit_of_a_frozen_line_is_not_a_violation(db_session):
    eq, _creditor, _debtor = await _line_with_debt(
        db_session, status="frozen", limit="100", debt="42"
    )

    assert await InvariantChecker(db_session).check_trust_limits(equivalent_id=eq.id) == []


@pytest.mark.asyncio
async def test_a_frozen_line_within_its_limit_leaves_the_checkpoint_healthy(db_session):
    eq, _creditor, _debtor = await _line_with_debt(
        db_session, status="frozen", limit="100", debt="42"
    )

    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)

    assert cp.invariants_status["checks"]["trust_limits"] == {"passed": True, "violations": 0}
    assert cp.invariants_status["status"] == "healthy"
    assert cp.invariants_status["alerts"] == []
    assert cp.invariants_status["passed"] is True


async def _prepared_payment(
    db_session,
    *,
    eq: Equivalent,
    sender: Participant,
    receiver: Participant,
    amount: str,
) -> str:
    tx_id = "tx-" + uuid.uuid4().hex
    db_session.add(
        Transaction(
            tx_id=tx_id,
            type="PAYMENT",
            initiator_id=sender.id,
            payload={
                "from": sender.pid,
                "to": receiver.pid,
                "amount": amount,
                "equivalent": eq.code,
                "path": [sender.pid, receiver.pid],
            },
            signatures=[],
            state="PREPARED",
        )
    )
    # prepare_locks.tx_id references transactions.tx_id with no ORM relationship, so the flush
    # does not order the two inserts; write the transaction first.
    await db_session.flush()
    db_session.add(
        PrepareLock(
            tx_id=tx_id,
            participant_id=sender.id,
            effects={
                "flows": [
                    {
                        "from": str(sender.id),
                        "to": str(receiver.id),
                        "amount": amount,
                        "equivalent": str(eq.id),
                    }
                ]
            },
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.commit()
    return tx_id


def _engine_that_survives_flow_retries(db_session, monkeypatch) -> PaymentEngine:
    # Same harness as `test_payment_commit_writes_integrity_audit_log_on_success` in
    # tests/unit/test_invariants.py: expiring the identity map after each flow is what a
    # stale-data retry does, and the audit path must survive it.
    engine = PaymentEngine(db_session)
    original_apply_flow = engine._apply_flow

    async def _apply_flow_and_expire(*args, **kwargs):
        await original_apply_flow(*args, **kwargs)
        db_session.expire_all()

    monkeypatch.setattr(engine, "_apply_flow", _apply_flow_and_expire)
    return engine


@pytest.mark.asyncio
async def test_a_payment_beside_a_frozen_line_is_recorded_as_verified(db_session, monkeypatch):
    # A frozen line with debt inside its limit, in the SAME equivalent as the payment but on an
    # unrelated pair: the commit-time check is scoped to the payment's pairs and passes, while the
    # audit row is computed from the whole equivalent's checkpoint.
    eq, _creditor, _debtor = await _line_with_debt(
        db_session, status="frozen", limit="100", debt="42"
    )
    nonce = uuid.uuid4().hex[:10]
    a = _participant("A", nonce)
    b = _participant("B", nonce)
    db_session.add_all([a, b])
    await db_session.flush()
    # Debt(A->B) is controlled by trustline(B->A).
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
    tx_id = await _prepared_payment(db_session, eq=eq, sender=a, receiver=b, amount="1")

    engine = _engine_that_survives_flow_retries(db_session, monkeypatch)
    assert await engine.commit(tx_id) is True

    log = (
        await db_session.execute(
            select(IntegrityAuditLog).where(
                IntegrityAuditLog.operation_type == "PAYMENT",
                IntegrityAuditLog.tx_id == tx_id,
            )
        )
    ).scalar_one()
    assert log.verification_passed is True, log.error_details
    assert log.error_details is None


@pytest.mark.asyncio
async def test_a_partial_repayment_of_debt_on_a_frozen_line_commits(db_session, monkeypatch):
    # The creditor pays the debtor 10 against a debt of 42 on the frozen line the creditor
    # extended: `_apply_flow` reduces the debt to 32. Before the fix the commit-time check read the
    # remaining 32 against a limit of zero and ABORTED the payment - so a debt on a frozen line
    # could only be reduced by paying all of it in one payment.
    eq, creditor, debtor = await _line_with_debt(
        db_session, status="frozen", limit="100", debt="42"
    )
    # Plain values: the harness expires the identity map during commit.
    eq_id, creditor_id, debtor_id = eq.id, creditor.id, debtor.id
    tx_id = await _prepared_payment(
        db_session, eq=eq, sender=creditor, receiver=debtor, amount="10"
    )

    engine = _engine_that_survives_flow_retries(db_session, monkeypatch)
    assert await engine.commit(tx_id) is True

    remaining = (
        await db_session.execute(
            select(Debt.amount).where(
                Debt.debtor_id == debtor_id,
                Debt.creditor_id == creditor_id,
                Debt.equivalent_id == eq_id,
            )
        )
    ).scalar_one()
    assert Decimal(str(remaining)) == Decimal("32")


# --- controls: the rule must not slide into its wrong neighbours ----------------------------


@pytest.mark.asyncio
async def test_a_frozen_line_over_its_limit_is_still_a_violation_against_that_limit(db_session):
    # §11.5.2 freezes a line BECAUSE its debt exceeds the limit. Excluding frozen lines from the
    # check would make that freeze erase the evidence of the breach it responds to.
    eq, creditor, debtor = await _line_with_debt(
        db_session, status="frozen", limit="100", debt="150"
    )

    with pytest.raises(IntegrityViolationException) as exc_info:
        await InvariantChecker(db_session).check_trust_limits(equivalent_id=eq.id)

    assert exc_info.value.details["invariant"] == "TRUST_LIMIT_VIOLATION"
    (violation,) = exc_info.value.details["violations"]
    assert violation["creditor_id"] == str(creditor.id)
    assert violation["debtor_id"] == str(debtor.id)
    assert Decimal(violation["trust_limit"]) == Decimal("100")
    assert Decimal(violation["violation_amount"]) == Decimal("50")

    cp = await compute_integrity_checkpoint_for_equivalent(db_session, equivalent_id=eq.id)
    assert cp.invariants_status["status"] == "critical"
    assert cp.invariants_status["alerts"] == ["trust_limits"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["closed", None], ids=["closed-line", "no-line"])
async def test_a_debt_without_a_live_line_is_a_violation_against_zero(db_session, status):
    # A closed line is history, not authority: its stored limit of 100 must not permit the 42.
    eq, _creditor, _debtor = await _line_with_debt(
        db_session, status=status, limit="100", debt="42"
    )

    with pytest.raises(IntegrityViolationException) as exc_info:
        await InvariantChecker(db_session).check_trust_limits(equivalent_id=eq.id)

    (violation,) = exc_info.value.details["violations"]
    assert Decimal(violation["trust_limit"]) == Decimal("0")
    assert Decimal(violation["debt_amount"]) == Decimal("42")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("debt", "violates"), [("100", False), ("100.01", True)], ids=["at-limit", "over-limit"]
)
async def test_an_active_line_is_compared_with_its_stored_limit(db_session, debt, violates):
    eq, _creditor, _debtor = await _line_with_debt(
        db_session, status="active", limit="100", debt=debt
    )
    checker = InvariantChecker(db_session)

    if not violates:
        assert await checker.check_trust_limits(equivalent_id=eq.id) == []
        return

    with pytest.raises(IntegrityViolationException) as exc_info:
        await checker.check_trust_limits(equivalent_id=eq.id)
    (violation,) = exc_info.value.details["violations"]
    assert Decimal(violation["trust_limit"]) == Decimal("100")
