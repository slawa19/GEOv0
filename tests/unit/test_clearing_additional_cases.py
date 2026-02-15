import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


def _mk_eq(code_prefix: str) -> Equivalent:
    nonce = uuid.uuid4().hex[:10]
    return Equivalent(
        code=(code_prefix + nonce[:15]).upper(),
        symbol=code_prefix.upper(),
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )


def _mk_participant(pid: str) -> Participant:
    nonce = uuid.uuid4().hex[:8]
    return Participant(
        pid=f"{pid}{nonce}",
        display_name=pid,
        public_key=f"pk_{pid}_{nonce}",
        type="person",
        status="active",
        profile={},
    )


async def _add_controlling_trustlines(
    db_session,
    *,
    eq_id: uuid.UUID,
    edges: list[tuple[Participant, Participant]],
    policy: dict | None = None,
    status: str = "active",
):
    # For a debt edge debtor->creditor, the controlling trustline is creditor->debtor.
    seen_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for debtor, creditor in edges:
        pair = (creditor.id, debtor.id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        db_session.add(
            TrustLine(
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=eq_id,
                limit=Decimal("100"),
                policy=policy,
                status=status,
            )
        )


@pytest.mark.asyncio
async def test_all_cycles_blocked_by_policy_returns_empty(db_session):
    eq = _mk_eq("T")
    a, b, c, d, e = (
        _mk_participant("A"),
        _mk_participant("B"),
        _mk_participant("C"),
        _mk_participant("D"),
        _mk_participant("E"),
    )
    db_session.add_all([eq, a, b, c, d, e])
    await db_session.flush()

    # Triangle: A->B->C->A
    tri_edges = [(a, b), (b, c), (c, a)]
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )

    # Quadrangle: A->B->D->E->A
    quad_edges = [(a, b), (b, d), (d, e), (e, a)]
    db_session.add_all(
        [
            Debt(debtor_id=b.id, creditor_id=d.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=d.id, creditor_id=e.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=e.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )

    # Block all controlling trustlines.
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=tri_edges + quad_edges,
        policy={"auto_clearing": False},
    )
    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=4)
    assert cycles == []


@pytest.mark.asyncio
async def test_partially_blocked_returns_allowed_cycle(db_session):
    eq = _mk_eq("P")
    a, b, c, d = (
        _mk_participant("A"),
        _mk_participant("B"),
        _mk_participant("C"),
        _mk_participant("D"),
    )
    db_session.add_all([eq, a, b, c, d])
    await db_session.flush()

    # Two triangles share edge A->B.
    # T1: A->B->C->A (blocked by policy on controlling TL C->B)
    # T2: A->B->D->A (allowed)
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=d.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=d.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )

    # Controlling TLs (creditor->debtor). Block only the controlling TL for edge B->C (i.e. C->B).
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(a, b), (c, a), (b, d), (d, a)],
        policy={"auto_clearing": True},
    )
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(b, c)],
        policy={"auto_clearing": False},
    )

    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles, "Expected at least one allowed triangle"

    # Should NOT return the blocked A->B->C->A.
    returned_pairs = [{(e["debtor"], e["creditor"]) for e in cycle} for cycle in cycles]
    blocked = {(a.pid, b.pid), (b.pid, c.pid), (c.pid, a.pid)}
    allowed = {(a.pid, b.pid), (b.pid, d.pid), (d.pid, a.pid)}

    assert blocked not in returned_pairs
    assert allowed in returned_pairs


@pytest.mark.asyncio
async def test_cycles_scoped_to_equivalent(db_session):
    eq1 = _mk_eq("E")
    eq2 = _mk_eq("F")

    a, b, c = _mk_participant("A"), _mk_participant("B"), _mk_participant("C")
    x, y, z = _mk_participant("X"), _mk_participant("Y"), _mk_participant("Z")
    db_session.add_all([eq1, eq2, a, b, c, x, y, z])
    await db_session.flush()

    # Triangle in eq1: A->B->C->A
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq1.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq1.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq1.id, amount=Decimal("10")),
        ]
    )
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq1.id,
        edges=[(a, b), (b, c), (c, a)],
        policy={"auto_clearing": True},
    )

    # Triangle in eq2: X->Y->Z->X
    db_session.add_all(
        [
            Debt(debtor_id=x.id, creditor_id=y.id, equivalent_id=eq2.id, amount=Decimal("10")),
            Debt(debtor_id=y.id, creditor_id=z.id, equivalent_id=eq2.id, amount=Decimal("10")),
            Debt(debtor_id=z.id, creditor_id=x.id, equivalent_id=eq2.id, amount=Decimal("10")),
        ]
    )
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq2.id,
        edges=[(x, y), (y, z), (z, x)],
        policy={"auto_clearing": True},
    )

    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq1.code, max_depth=3)
    assert cycles

    pids = {a.pid, b.pid, c.pid}
    for cycle in cycles:
        for edge in cycle:
            assert edge["debtor"] in pids
            assert edge["creditor"] in pids


@pytest.mark.asyncio
async def test_no_trustline_means_no_consent(db_session):
    eq = _mk_eq("N")
    a, b, c = _mk_participant("A"), _mk_participant("B"), _mk_participant("C")
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

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
    assert cycles == []


@pytest.mark.asyncio
async def test_frozen_trustline_blocks_clearing(db_session):
    eq = _mk_eq("Z")
    a, b, c = _mk_participant("A"), _mk_participant("B"), _mk_participant("C")
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
            Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
        ]
    )

    # Two active TLs + one frozen controlling TL should block the cycle.
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(a, b), (b, c)],
        policy={"auto_clearing": True},
        status="active",
    )
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(c, a)],
        policy={"auto_clearing": True},
        status="frozen",
    )
    await db_session.commit()

    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles == []


@pytest.mark.asyncio
async def test_sql_and_dfs_produce_same_cycles(db_session, monkeypatch):
    eq = _mk_eq("S")
    a, b, c = _mk_participant("A"), _mk_participant("B"), _mk_participant("C")
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    debts = [
        Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("10")),
        Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("10")),
        Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("10")),
    ]
    db_session.add_all(debts)

    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(a, b), (b, c), (c, a)],
        policy={"auto_clearing": True},
    )
    await db_session.commit()

    service = ClearingService(db_session)

    sql_cycles = await service.find_triangles_sql(eq.id)
    sql_cycles = service._deduplicate_cycles(sql_cycles)
    sql_cycles = await service._filter_cycles_by_auto_clearing_policy_sql(
        sql_cycles, equivalent_id=eq.id
    )

    # Force DFS fallback by making SQL detectors fail.
    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "find_triangles_sql", _boom)
    monkeypatch.setattr(service, "find_quadrangles_sql", _boom)

    dfs_cycles = await service.find_cycles(eq.code, max_depth=3)

    def _norm_debt_id(val: str) -> str:
        return uuid.UUID(str(val)).hex

    sql_keys = {
        tuple(sorted(_norm_debt_id(e["debt_id"]) for e in cycle)) for cycle in sql_cycles
    }
    dfs_keys = {
        tuple(sorted(_norm_debt_id(e["debt_id"]) for e in cycle)) for cycle in dfs_cycles
    }

    assert sql_keys
    assert sql_keys == dfs_keys


@pytest.mark.asyncio
async def test_self_loop_not_allowed(db_session):
    eq = _mk_eq("L")
    a = _mk_participant("A")
    db_session.add_all([eq, a])
    await db_session.flush()

    db_session.add(
        Debt(
            debtor_id=a.id,
            creditor_id=a.id,
            equivalent_id=eq.id,
            amount=Decimal("1"),
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_auto_clear_clears_multiple_independent_cycles(db_session):
    eq = _mk_eq("M")
    a, b, c, x, y, z = (
        _mk_participant("A"),
        _mk_participant("B"),
        _mk_participant("C"),
        _mk_participant("X"),
        _mk_participant("Y"),
        _mk_participant("Z"),
    )
    db_session.add_all([eq, a, b, c, x, y, z])
    await db_session.flush()

    # Cycle1: A->B->C->A
    db_session.add_all(
        [
            Debt(debtor_id=a.id, creditor_id=b.id, equivalent_id=eq.id, amount=Decimal("5")),
            Debt(debtor_id=b.id, creditor_id=c.id, equivalent_id=eq.id, amount=Decimal("5")),
            Debt(debtor_id=c.id, creditor_id=a.id, equivalent_id=eq.id, amount=Decimal("5")),
        ]
    )
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(a, b), (b, c), (c, a)],
        policy={"auto_clearing": True},
    )

    # Cycle2: X->Y->Z->X
    db_session.add_all(
        [
            Debt(debtor_id=x.id, creditor_id=y.id, equivalent_id=eq.id, amount=Decimal("7")),
            Debt(debtor_id=y.id, creditor_id=z.id, equivalent_id=eq.id, amount=Decimal("7")),
            Debt(debtor_id=z.id, creditor_id=x.id, equivalent_id=eq.id, amount=Decimal("7")),
        ]
    )
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(x, y), (y, z), (z, x)],
        policy={"auto_clearing": True},
    )

    await db_session.commit()

    service = ClearingService(db_session)
    cleared = await service.auto_clear(eq.code, max_depth=3)
    assert cleared == 2

    remaining = (
        (
            await db_session.execute(
                select(Debt).where(Debt.equivalent_id == eq.id, Debt.amount > 0)
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []
