import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


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

    # Controlling trustlines (creditor -> debtor) must exist for auto-clearing consent.
    db_session.add_all(
        [
            TrustLine(from_participant_id=b.id, to_participant_id=a.id, equivalent_id=eq.id, limit=Decimal("100")),
            TrustLine(from_participant_id=c.id, to_participant_id=b.id, equivalent_id=eq.id, limit=Decimal("100")),
            TrustLine(from_participant_id=a.id, to_participant_id=c.id, equivalent_id=eq.id, limit=Decimal("100")),
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

    db_session.add_all(
        [
            TrustLine(from_participant_id=b.id, to_participant_id=a.id, equivalent_id=eq.id, limit=Decimal("100")),
            TrustLine(from_participant_id=c.id, to_participant_id=b.id, equivalent_id=eq.id, limit=Decimal("100")),
            TrustLine(from_participant_id=d.id, to_participant_id=c.id, equivalent_id=eq.id, limit=Decimal("100")),
            TrustLine(from_participant_id=a.id, to_participant_id=d.id, equivalent_id=eq.id, limit=Decimal("100")),
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


@pytest.mark.asyncio
async def test_find_cycles_filters_auto_clearing_policy_and_falls_back_to_quadrangles(
    db_session,
):
    """Regression: if triangles exist but are rejected by auto-clearing policy,
    find_cycles(max_depth>=4) must still try quadrangles instead of returning a
    non-executable candidate that would stop the clearing loop early.
    """

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("P" + nonce[:15]).upper(), symbol="P", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    c = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    d = Participant(pid="D" + nonce, display_name="D", public_key="pkD-" + nonce, type="person", status="active", profile={})
    e = Participant(pid="E" + nonce, display_name="E", public_key="pkE-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b, c, d, e])
    await db_session.flush()

    # Triangle (A->B, B->C, C->A) exists, but we will block it via one controlling trustline.
    # Quadrangle (A->B, B->D, D->E, E->A) exists and is fully consenting.
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=d.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=d.id, creditor_id=e.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=e.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )

    # Controlling trustlines are creditor->debtor for each debt edge.
    db_session.add_all(
        [
            TrustLine(from_participant_id=b.id, to_participant_id=a.id, equivalent_id=eq.id, limit=Decimal("100"), policy={"auto_clearing": True}),
            TrustLine(from_participant_id=c.id, to_participant_id=b.id, equivalent_id=eq.id, limit=Decimal("100"), policy={"auto_clearing": False}),
            TrustLine(from_participant_id=a.id, to_participant_id=c.id, equivalent_id=eq.id, limit=Decimal("100"), policy={"auto_clearing": True}),
            TrustLine(from_participant_id=d.id, to_participant_id=b.id, equivalent_id=eq.id, limit=Decimal("100"), policy={"auto_clearing": True}),
            TrustLine(from_participant_id=e.id, to_participant_id=d.id, equivalent_id=eq.id, limit=Decimal("100"), policy={"auto_clearing": True}),
            TrustLine(from_participant_id=a.id, to_participant_id=e.id, equivalent_id=eq.id, limit=Decimal("100"), policy={"auto_clearing": True}),
        ]
    )
    await db_session.commit()

    # Sanity: ensure our policy was actually persisted as False on the controlling edge.
    blocking_tl = (
        (
            await db_session.execute(
                select(TrustLine).where(
                    TrustLine.equivalent_id == eq.id,
                    TrustLine.from_participant_id == c.id,
                    TrustLine.to_participant_id == b.id,
                )
            )
        )
        .scalars()
        .one()
    )
    assert (blocking_tl.policy or {}).get("auto_clearing") is False
    assert blocking_tl.status == "active"

    service = ClearingService(db_session)

    # With SQL pre-filtering (JOIN trust_lines + policy predicate), non-consenting triangles
    # are excluded already at SQL stage.
    triangles = await service.find_triangles_sql(eq.id)
    assert triangles == []

    cycles_depth3 = await service.find_cycles(eq.code, max_depth=3)
    assert cycles_depth3 == [], "Triangle should be filtered out by policy"

    cycles_depth4 = await service.find_cycles(eq.code, max_depth=4)
    assert cycles_depth4, "Expected quadrangle cycle fallback"

    cycle = cycles_depth4[0]
    assert len(cycle) == 4
    pairs = {(edge["debtor"], edge["creditor"]) for edge in cycle}
    assert pairs == {(a.pid, b.pid), (b.pid, d.pid), (d.pid, e.pid), (e.pid, a.pid)}


@pytest.mark.asyncio
async def test_find_quadrangles_sql_rejects_repeated_vertex_b_equals_d(db_session):
    """Regression: exclude non-simple 4-edge patterns like A->B->C->B->A.

    Such patterns repeat a vertex (B==D) and are not a simple quadrangle.
    """

    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("R" + nonce[:15]).upper(), symbol="R", description=None, precision=2, metadata_={}, is_active=True)
    a = Participant(pid="A" + nonce, display_name="A", public_key="pkA-" + nonce, type="person", status="active", profile={})
    b = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    c = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    # Pattern: A -> B -> C -> B -> A (B repeats as the 4th vertex).
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )
    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_quadrangles_sql(eq.id)
    assert cycles == []
