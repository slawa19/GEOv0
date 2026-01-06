import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_clearing_deletes_zero_debts(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("Z" + nonce[:15]).upper(), symbol="Z", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    c = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    # Debts: A->B->C->A
    d_ab = Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10"))
    d_bc = Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10"))
    d_ca = Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10"))
    db_session.add_all([d_ab, d_bc, d_ca])

    # Auto-clearing consent is controlled by creditor->debtor trustline for each debt.
    db_session.add_all(
        [
            TrustLine(from_participant_id=b.id, to_participant_id=a.id, equivalent_id=eq.id, limit=Decimal("100"), status="active", policy={"auto_clearing": True}),
            TrustLine(from_participant_id=c.id, to_participant_id=b.id, equivalent_id=eq.id, limit=Decimal("100"), status="active", policy={"auto_clearing": True}),
            TrustLine(from_participant_id=a.id, to_participant_id=c.id, equivalent_id=eq.id, limit=Decimal("100"), status="active", policy={"auto_clearing": True}),
        ]
    )

    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles

    ok = await service.execute_clearing(cycles[0])
    assert ok is True

    tx = (
        await db_session.execute(
            select(Transaction).where(Transaction.type == "CLEARING")
        )
    ).scalars().one()

    assert tx.state == "COMMITTED"
    assert tx.payload["equivalent"] == eq.code
    assert tx.payload["amount"] == "10"
    assert isinstance(tx.payload.get("edges"), list)
    assert len(tx.payload["edges"]) == 3
    for edge in tx.payload["edges"]:
        assert set(edge.keys()) == {"debt_id", "debtor", "creditor", "amount"}
        assert edge["amount"] == "10"

    audit = (
        await db_session.execute(
            select(IntegrityAuditLog).where(IntegrityAuditLog.operation_type == "CLEARING")
        )
    ).scalars().one()

    assert audit.tx_id == tx.tx_id
    assert audit.equivalent_code == eq.code
    assert isinstance(audit.affected_participants, dict)
    assert isinstance(audit.affected_participants.get("participants"), list)
    assert set(audit.affected_participants["participants"]) == {a.pid, b.pid, c.pid}
    assert isinstance(audit.affected_participants.get("edges"), list)
    assert len(audit.affected_participants["edges"]) == 3

    remaining = (
        await db_session.execute(
            select(Debt).where(Debt.equivalent_id == eq.id)
        )
    ).scalars().all()

    assert remaining == []
