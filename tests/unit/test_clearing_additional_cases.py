import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.clearing.service import ClearingService
from app.core.invariants import InvariantChecker
from app.core.payments.router import PaymentRouter
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import GeoException


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


async def _setup_committed_triangle(
    db_session,
    *,
    code_prefix: str,
    amount: Decimal = Decimal("10"),
    auto_clearing: bool = True,
) -> tuple[Equivalent, list[Debt]]:
    eq = _mk_eq(code_prefix)
    a, b, c = _mk_participant("A"), _mk_participant("B"), _mk_participant("C")
    db_session.add_all([eq, a, b, c])
    await db_session.flush()
    debts = [
        Debt(
            debtor_id=debtor.id,
            creditor_id=creditor.id,
            equivalent_id=eq.id,
            amount=amount,
        )
        for debtor, creditor in [(a, b), (b, c), (c, a)]
    ]
    db_session.add_all(debts)
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(a, b), (b, c), (c, a)],
        policy={"auto_clearing": auto_clearing},
    )
    await db_session.commit()
    return eq, debts


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_site",
    ["execute", "execute_raw", "find", "find_raw"],
)
async def test_auto_clear_surfaces_sanitized_failure_after_partial_progress(
    db_session,
    monkeypatch,
    caplog,
    failure_site,
):
    service = ClearingService(db_session)
    cycle = [{"debt_id": str(uuid.uuid4())}]
    execute_calls = 0
    find_calls = 0

    async def _find_cycles(*_args, **_kwargs):
        nonlocal find_calls
        find_calls += 1
        if failure_site in {"find", "find_raw"} and find_calls == 2:
            if failure_site == "find_raw":
                raise RuntimeError("private raw discovery detail")
            raise GeoException("private discovery detail")
        return [cycle]

    async def _execute_clearing(_cycle):
        nonlocal execute_calls
        execute_calls += 1
        if execute_calls == 1 or failure_site in {"find", "find_raw"}:
            return True
        if failure_site == "execute_raw":
            raise RuntimeError("private raw lock detail")
        raise GeoException("private database detail")

    monkeypatch.setattr(service, "find_cycles", _find_cycles)
    monkeypatch.setattr(service, "execute_clearing", _execute_clearing)

    with pytest.raises(GeoException) as exc_info:
        await service.auto_clear("USD")

    assert execute_calls == (1 if failure_site in {"find", "find_raw"} else 2)
    assert exc_info.value.code == "E010"
    assert exc_info.value.status_code == 500
    assert exc_info.value.details == {"cleared_cycles": 1, "partial": True}
    assert "private database detail" not in str(exc_info.value.to_dict())
    assert "private discovery detail" not in str(exc_info.value.to_dict())
    assert "private raw lock detail" not in str(exc_info.value.to_dict())
    assert "private raw discovery detail" not in str(exc_info.value.to_dict())
    if failure_site in {"execute", "execute_raw"}:
        assert "event=clearing.auto_clear_execute_failed" in caplog.text
    if failure_site in {"find", "find_raw"}:
        assert "event=clearing.auto_clear_find_failed" in caplog.text


@pytest.mark.asyncio
async def test_execute_clearing_unexpected_failure_rolls_back_and_surfaces_sanitized_error(
    db_session,
    monkeypatch,
):
    eq = _mk_eq("U")
    a, b, c = _mk_participant("A"), _mk_participant("B"), _mk_participant("C")
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    debts = [
        Debt(
            debtor_id=a.id,
            creditor_id=b.id,
            equivalent_id=eq.id,
            amount=Decimal("10"),
        ),
        Debt(
            debtor_id=b.id,
            creditor_id=c.id,
            equivalent_id=eq.id,
            amount=Decimal("10"),
        ),
        Debt(
            debtor_id=c.id,
            creditor_id=a.id,
            equivalent_id=eq.id,
            amount=Decimal("10"),
        ),
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
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles
    eq_code = eq.code
    eq_id = eq.id

    async def _unexpected_failure(*_args, **_kwargs):
        raise RuntimeError("private database detail")

    monkeypatch.setattr(
        InvariantChecker,
        "verify_clearing_neutrality",
        _unexpected_failure,
    )
    stale_cache_entry = (0.0, {}, {}, {}, {}, {})
    PaymentRouter._graph_cache[eq_code] = stale_cache_entry

    try:
        with pytest.raises(GeoException) as exc_info:
            await service.execute_clearing_with_amount(cycles[0])

        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "E010"
        assert "private database detail" not in exc_info.value.message

        amounts = (
            await db_session.execute(
                select(Debt.amount).where(Debt.equivalent_id == eq_id)
            )
        ).scalars().all()
        assert sorted(Decimal(str(amount)) for amount in amounts) == [
            Decimal("10"),
            Decimal("10"),
            Decimal("10"),
        ]
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.type == "CLEARING")
            )
            == 0
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(IntegrityAuditLog)
                .where(IntegrityAuditLog.operation_type == "CLEARING")
            )
            == 0
        )
        assert PaymentRouter._graph_cache[eq_code] is stale_cache_entry
    finally:
        PaymentRouter.invalidate_cache(eq_code)


@pytest.mark.asyncio
async def test_execute_clearing_policy_lookup_failure_rolls_back_without_effects(
    db_session,
    monkeypatch,
):
    eq, _ = await _setup_committed_triangle(db_session, code_prefix="F")
    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles
    eq_code = eq.code
    eq_id = eq.id
    original_rollback = db_session.rollback
    rollback_calls = 0

    async def _fail_policy_lookup(*_args, **_kwargs):
        raise RuntimeError("private policy database detail")

    async def _track_rollback():
        nonlocal rollback_calls
        rollback_calls += 1
        await original_rollback()

    monkeypatch.setattr(service, "_cycle_respects_auto_clearing", _fail_policy_lookup)
    monkeypatch.setattr(db_session, "rollback", _track_rollback)
    stale_graph_entry = (0.0, {}, {}, {}, {}, {})
    stale_topology_entry = {"sentinel": {"neighbor"}}
    PaymentRouter._graph_cache[eq_code] = stale_graph_entry
    PaymentRouter._topology_cache[eq_code] = stale_topology_entry

    try:
        with pytest.raises(GeoException) as exc_info:
            await service.execute_clearing_with_amount(cycles[0])

        assert rollback_calls == 1
        assert exc_info.value.status_code == 500
        assert exc_info.value.code == "E010"
        assert "private policy database detail" not in exc_info.value.message

        amounts = (
            await db_session.execute(
                select(Debt.amount).where(Debt.equivalent_id == eq_id)
            )
        ).scalars().all()
        assert sorted(Decimal(str(amount)) for amount in amounts) == [
            Decimal("10"),
            Decimal("10"),
            Decimal("10"),
        ]
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.type == "CLEARING")
            )
            == 0
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(IntegrityAuditLog)
                .where(IntegrityAuditLog.operation_type == "CLEARING")
            )
            == 0
        )
        assert PaymentRouter._graph_cache[eq_code] is stale_graph_entry
        assert PaymentRouter._topology_cache[eq_code] is stale_topology_entry
    finally:
        PaymentRouter.invalidate_cache(eq_code)


@pytest.mark.asyncio
async def test_execute_clearing_policy_skip_remains_non_exceptional(db_session):
    eq = _mk_eq("K")
    a, b, c = _mk_participant("A"), _mk_participant("B"), _mk_participant("C")
    db_session.add_all([eq, a, b, c])
    await db_session.flush()

    debts = [
        Debt(
            debtor_id=a.id,
            creditor_id=b.id,
            equivalent_id=eq.id,
            amount=Decimal("4"),
        ),
        Debt(
            debtor_id=b.id,
            creditor_id=c.id,
            equivalent_id=eq.id,
            amount=Decimal("4"),
        ),
        Debt(
            debtor_id=c.id,
            creditor_id=a.id,
            equivalent_id=eq.id,
            amount=Decimal("4"),
        ),
    ]
    db_session.add_all(debts)
    await _add_controlling_trustlines(
        db_session,
        eq_id=eq.id,
        edges=[(a, b), (b, c), (c, a)],
        policy={"auto_clearing": False},
    )
    await db_session.commit()

    cycle = [
        {"debt_id": str(debt.id)}
        for debt in debts
    ]
    result = await ClearingService(db_session).execute_clearing_with_amount(cycle)

    assert result is None


@pytest.mark.asyncio
async def test_execute_clearing_commit_failure_rolls_back_without_visible_effects(
    db_session,
    monkeypatch,
):
    eq, _ = await _setup_committed_triangle(db_session, code_prefix="C")
    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles
    eq_id = eq.id

    async def _fail_commit():
        raise RuntimeError("commit failed with private detail")

    monkeypatch.setattr(db_session, "commit", _fail_commit)

    with pytest.raises(GeoException) as exc_info:
        await service.execute_clearing_with_amount(cycles[0])

    assert exc_info.value.code == "E010"
    assert "private detail" not in exc_info.value.message
    amounts = (
        await db_session.execute(
            select(Debt.amount).where(Debt.equivalent_id == eq_id)
        )
    ).scalars().all()
    assert sorted(Decimal(str(amount)) for amount in amounts) == [
        Decimal("10"),
        Decimal("10"),
        Decimal("10"),
    ]
    assert await db_session.scalar(select(func.count()).select_from(Transaction)) == 0
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrityAuditLog))
        == 0
    )


@pytest.mark.asyncio
async def test_execute_clearing_rollback_failure_keeps_original_error_sanitized(
    db_session,
    monkeypatch,
    caplog,
):
    eq, _ = await _setup_committed_triangle(db_session, code_prefix="R")
    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles
    original_rollback = db_session.rollback

    async def _fail_execution(*_args, **_kwargs):
        raise RuntimeError("execution private detail")

    async def _fail_rollback():
        raise RuntimeError("rollback private detail")

    monkeypatch.setattr(
        InvariantChecker,
        "verify_clearing_neutrality",
        _fail_execution,
    )
    monkeypatch.setattr(db_session, "rollback", _fail_rollback)

    try:
        with pytest.raises(GeoException) as exc_info:
            await service.execute_clearing_with_amount(cycles[0])

        assert exc_info.value.code == "E010"
        assert "private detail" not in exc_info.value.message
        assert any(
            "event=clearing.rollback_failed" in record.getMessage()
            for record in caplog.records
        )
    finally:
        await original_rollback()


@pytest.mark.asyncio
async def test_execute_clearing_checkpoint_failure_is_explicitly_best_effort(
    db_session,
    monkeypatch,
    caplog,
):
    eq, _ = await _setup_committed_triangle(db_session, code_prefix="B")
    service = ClearingService(db_session)
    cycles = await service.find_cycles(eq.code, max_depth=3)
    assert cycles
    calls = 0

    async def _fail_second_checkpoint(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("checkpoint unavailable")
        return None

    monkeypatch.setattr(
        "app.core.clearing.service.compute_integrity_checkpoint_for_equivalent",
        _fail_second_checkpoint,
    )

    result = await service.execute_clearing_with_amount(cycles[0])

    assert result == Decimal("10")
    assert calls == 2
    assert any(
        "event=clearing.checkpoint_after_failed" in record.getMessage()
        for record in caplog.records
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.state == "COMMITTED")
        )
        == 1
    )
