"""T1551 on PostgreSQL: a cycle reduces the over-limit debt on a frozen trust line.

The default-tier module `tests/unit/test_p015_t1551_clearing_reduces_debt_on_a_frozen_line.py`
carries the argument, the controls and the mutations. This one exists because two parts of the
change are dialect-bound and SQLite cannot speak for them: the raw triangle query is compiled for
PostgreSQL here, and on PostgreSQL `execute_clearing_with_amount` runs through the payment/clearing
interlock on its own pinned connection, where execution-time revalidation re-reads consent - so a
frozen line admitted by discovery but refused there would show up as `auto_clear` returning 0.

The data is committed, because the interlock path opens a connection of its own and cannot see a
fixture that lives in an uncommitted test transaction; the `finally` block removes it.
"""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.clearing.service import ClearingService
from app.core.invariants import InvariantChecker
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import IntegrityViolationException
from tests.debt_setup import debt_fixture_setup, purge_test_ledger

pytestmark = pytest.mark.postgres


async def test_clearing_reduces_the_over_limit_debt_on_a_frozen_line_postgres() -> None:
    from tests.conftest import TestingSessionLocal, _ensure_schema_initialized

    # No `db_session` here, so nothing else would build the schema when this test runs alone.
    await _ensure_schema_initialized()

    nonce = uuid.uuid4().hex[:8].upper()
    code = f"FZ{nonce}"
    equivalent_id = uuid.uuid4()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    debt_ids = [uuid.uuid4() for _ in range(3)]
    a, b, c = participant_ids
    # A owes B 30, B owes C 30, C owes A 150. The last is the subject: its controlling line A -> C
    # is FROZEN with a limit of 100, so it is over its limit by 50 - the breach §11.5.2 freezes for.
    ring = [
        (debt_ids[0], a, b, Decimal("30"), "active", Decimal("1000")),
        (debt_ids[1], b, c, Decimal("30"), "active", Decimal("1000")),
        (debt_ids[2], c, a, Decimal("150"), "frozen", Decimal("100")),
    ]

    try:
        async with TestingSessionLocal() as setup:
            setup.add(Equivalent(id=equivalent_id, code=code, symbol="FZ", precision=2))
            setup.add_all(
                [
                    Participant(
                        id=pid,
                        pid=f"geo:{label}:{nonce}",
                        display_name=label,
                        public_key=uuid.uuid4().hex * 2,
                        type="person",
                        status="active",
                    )
                    for pid, label in zip(participant_ids, ("A", "B", "C"), strict=True)
                ]
            )
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=creditor,
                        to_participant_id=debtor,
                        equivalent_id=equivalent_id,
                        limit=limit,
                        policy={"auto_clearing": True},
                        status=status,
                    )
                    for _, debtor, creditor, _, status, limit in ring
                ]
            )
            async with debt_fixture_setup(setup, label="setup"):
                setup.add_all(
                    [
                        Debt(
                            id=debt_id,
                            debtor_id=debtor,
                            creditor_id=creditor,
                            equivalent_id=equivalent_id,
                            amount=amount,
                        )
                        for debt_id, debtor, creditor, amount, _, _ in ring
                    ]
                )
            await setup.commit()

        async with TestingSessionLocal() as before:
            checker = InvariantChecker(before)
            positions_before = {
                pid: await checker._calculate_net_position(pid, equivalent_id)
                for pid in participant_ids
            }
            triangles = await ClearingService(before).find_triangles_sql(equivalent_id)
        assert frozenset(str(i) for i in debt_ids) in {
            frozenset(ClearingService._debt_id_key(edge["debt_id"]) for edge in cycle)
            for cycle in triangles
        }, "the PostgreSQL triangle query must admit the frozen line"

        async with TestingSessionLocal() as worker:
            cleared = await ClearingService(worker).auto_clear(code, max_depth=3)
        assert cleared == 1

        async with TestingSessionLocal() as verify:
            checker = InvariantChecker(verify)
            positions_after = {
                pid: await checker._calculate_net_position(pid, equivalent_id)
                for pid in participant_ids
            }
            amounts = {
                debt_id: amount
                for debt_id, amount in (
                    await verify.execute(
                        select(Debt.id, Debt.amount).where(Debt.equivalent_id == equivalent_id)
                    )
                ).all()
            }
            with pytest.raises(IntegrityViolationException) as exc_info:
                await checker.check_trust_limits(equivalent_id=equivalent_id)

        assert positions_after == positions_before
        assert amounts == {debt_ids[2]: Decimal("120")}
        (violation,) = exc_info.value.details["violations"]
        assert Decimal(violation["violation_amount"]) == Decimal("20")
    finally:
        primary_error = sys.exc_info()[1]
        try:
            async with TestingSessionLocal() as cleanup:
                await purge_test_ledger(cleanup, equivalent_ids=[equivalent_id])
                await cleanup.execute(
                    delete(IntegrityAuditLog).where(IntegrityAuditLog.equivalent_code == code)
                )
                await cleanup.execute(
                    delete(Transaction).where(Transaction.initiator_id.in_(participant_ids))
                )
                await cleanup.execute(
                    delete(TrustLine).where(TrustLine.equivalent_id == equivalent_id)
                )
                await cleanup.execute(
                    delete(Participant).where(Participant.id.in_(participant_ids))
                )
                await cleanup.execute(delete(Equivalent).where(Equivalent.id == equivalent_id))
                await cleanup.commit()
        except Exception:
            if primary_error is None:
                raise
