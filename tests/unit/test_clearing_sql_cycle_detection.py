import uuid
from decimal import Decimal

import pytest

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant


@pytest.mark.asyncio
async def test_find_cycles_uses_sql_triangles(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("T" + nonce[:15]).upper(), symbol="T", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    c = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    # A -> B -> C -> A
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )
    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles, "Expected at least one triangle cycle"

    cycle = cycles[0]
    pairs = {(edge["debtor"], edge["creditor"]) for edge in cycle}
    assert pairs == {(a.pid, b.pid), (b.pid, c.pid), (c.pid, a.pid)}


@pytest.mark.asyncio
async def test_find_cycles_uses_sql_quadrangles(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("Q" + nonce[:15]).upper(), symbol="Q", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    c = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    d = Participant(pid="D" + nonce, display_name="D", public_key="pkD-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b, c, d])
    await db_session.flush()

    # A -> B -> C -> D -> A
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=d.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=d.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )
    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=4)
    assert cycles, "Expected at least one quadrangle cycle"

    cycle = cycles[0]
    assert len(cycle) == 4
    pairs = {(edge["debtor"], edge["creditor"]) for edge in cycle}
    assert pairs == {(a.pid, b.pid), (b.pid, c.pid), (c.pid, d.pid), (d.pid, a.pid)}
