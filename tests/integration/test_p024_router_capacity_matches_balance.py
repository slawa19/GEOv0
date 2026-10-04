"""R-024-1 (024 `T2415.2`, F-024-3): the router, the core and `/balance` apply ONE capacity rule.

Owner decision 2026-09-29 (spec, П1, variant (b)): a payment A -> B may go up to
`limit(B -> A) - debt[A -> B] + debt[B -> A]`; the payee's debt to the payer is offset even without a
line from the payee, new debt of the payer needs that line. Stand: A trusts B (line A -> B, 100) and
B owes A 50. The core (`_segment_capacity`) and `/balance` are the controls; the router is the target.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update

from app.config import settings
from app.core.balance.service import BalanceService
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.exceptions import ConflictException, RoutingException
from tests.debt_setup import debt_fixture_setup
from tests.conftest import MODE_B, sessionmaker_of
from tests.p019_support import TargetMismatch, require_target


async def _seed(session, *, debt_b_a="50", line_b_a=None, line_c_b=None, a_b_status="active"):
    code = f"C{uuid.uuid4().hex[:6].upper()}"
    eq = Equivalent(code=code, precision=2)
    people = {
        name: Participant(pid=f"{name}-{code}", display_name=name, public_key=f"pk-{name}-{code}")
        for name in ("A", "B", "C")
    }
    session.add_all([eq, *people.values()])
    await session.flush()
    lines = [("A", "B", "100"), ("B", "A", line_b_a), ("C", "B", line_c_b)]
    for creditor, debtor, limit in lines:
        if limit is not None:
            session.add(
                TrustLine(
                    from_participant_id=people[creditor].id,
                    to_participant_id=people[debtor].id,
                    equivalent_id=eq.id,
                    limit=Decimal(limit),
                    status=a_b_status if (creditor, debtor) == ("A", "B") else "active",
                )
            )
    if debt_b_a is not None:
        async with debt_fixture_setup(session, label="setup"):
            session.add(
                Debt(
                    debtor_id=people["B"].id,
                    creditor_id=people["A"].id,
                    equivalent_id=eq.id,
                    amount=Decimal(debt_b_a),
                )
            )
    await session.commit()
    return eq, people


async def _router(session, eq) -> PaymentRouter:
    router = PaymentRouter(session)
    await router.build_graph(eq.code, use_shared_cache=False)
    return router


async def _core_and_balance(session, eq, people) -> tuple[Decimal, Decimal]:
    core = await PaymentService(session)._segment_capacity(
        sender_id=people["A"].id, receiver_id=people["B"].id, equivalent_id=eq.id
    )
    summary = await BalanceService(session).get_summary(people["A"].id)
    spend = {e.code: Decimal(e.available_to_spend) for e in summary.equivalents}
    return core, spend.get(eq.code, Decimal("0"))


def _routable(router, people, amount: str, *, to: str = "B") -> bool:
    routes = router.find_flow_routes(people["A"].pid, people[to].pid, Decimal(amount), max_paths=1)
    return bool(routes)


async def _agree(db_session, *, capacity: str, **seed) -> None:
    eq, people = await _seed(db_session, **seed)
    # Controls: the core and `/balance` already give the owner's number.
    assert await _core_and_balance(db_session, eq, people) == (Decimal(capacity), Decimal(capacity))
    router = await _router(db_session, eq)
    beyond = str(Decimal(capacity) + Decimal("0.01"))
    assert not _routable(router, people, beyond), f"router allows {beyond} beyond {capacity}"
    require_target(
        _routable(router, people, "30") and _routable(router, people, capacity),
        f"router refuses a payment the core and /balance allow up to {capacity}",
    )


@pytest.mark.asyncio
async def test_a_counter_debt_alone_carries_a_payment_up_to_the_debt(db_session):
    await _agree(db_session, capacity="50")


@pytest.mark.asyncio
async def test_under_a_line_the_counter_debt_is_counted_once(db_session):
    # 10 of the line plus 50 of the counter-debt; 60.01 would mean the debt was counted twice.
    await _agree(db_session, capacity="60", line_b_a="10")


@pytest.mark.asyncio
async def test_without_a_debt_or_a_line_there_is_no_route(db_session):
    eq, people = await _seed(db_session, debt_b_a=None)
    assert await _core_and_balance(db_session, eq, people) == (Decimal("0"), Decimal("0"))
    assert not _routable(await _router(db_session, eq), people, "0.01")


@pytest.mark.asyncio
async def test_a_payment_offsets_the_counter_debt_and_creates_no_debt(db_session):
    eq, people = await _seed(db_session)
    try:
        result = await PaymentService(db_session).create_payment_internal(
            people["A"].id, to_pid=people["B"].pid, equivalent=eq.code, amount="30"
        )
    except RoutingException as exc:
        raise TargetMismatch(f"payment A->B 30 refused: {exc}") from exc
    assert result.status == "COMMITTED", result
    rows = (
        await db_session.execute(
            select(Debt.debtor_id, Debt.amount).where(Debt.equivalent_id == eq.id)
        )
    ).all()
    assert {(debtor, amount) for debtor, amount in rows} == {(people["B"].id, Decimal("20"))}


@pytest.mark.asyncio
async def test_an_offset_hop_over_an_active_permissive_pair_is_a_transit_hop(db_session):
    # GEO transit (owner decision (2), 2026-09-29): A -> B offsets B's debt over A's active line.
    eq, people = await _seed(db_session, line_c_b="100")
    await PaymentService(db_session)._bind_payment(
        f"hop-{uuid.uuid4()}", [([people[n].pid for n in "ABC"], Decimal("30"))], eq.id
    )
    router = await _router(db_session, eq)
    assert not _routable(router, people, "50.01", to="C")
    assert _routable(router, people, "30", to="C")


async def _chain(session, lines, debts):
    """Lines creditor -> debtor (limit 100, given policy) and debts debtor -> creditor (50) among W..Z."""
    eq, _ = await _seed(session, debt_b_a=None)
    p = {n: Participant(pid=f"{n}-{eq.code}", display_name=n, public_key=f"pk-{n}-{eq.code}") for n in "WXYZ"}
    session.add_all(p.values())
    await session.flush()
    for creditor, debtor, policy in lines:
        policy = {k: [p[n].pid for n in v] if k == "blocked_participants" else v for k, v in policy.items()}
        session.add(TrustLine(from_participant_id=p[creditor].id, to_participant_id=p[debtor].id,
                              equivalent_id=eq.id, limit=Decimal("100"), status="active", policy=policy))
    async with debt_fixture_setup(session, label="setup"):
        session.add_all([Debt(debtor_id=p[d].id, creditor_id=p[c].id, equivalent_id=eq.id,
                              amount=Decimal("50")) for d, c in debts])
    await session.commit()
    return eq, p


_CASES = {  # variant: (lines, debts, payee); every policy below must hold, "permissive" is the control
    "can_be_intermediate": ([("W", "X", {}), ("X", "Y", {"can_be_intermediate": False})], "XW YX", "Y"),
    "blocked_participants": ([("W", "X", {}), ("X", "Y", {"blocked_participants": ["Y"]}), ("Y", "Z", {})],
                             "XW YX ZY", "Z"),
    "payer_side_of_two_lines": ([("W", "X", {}), ("X", "Y", {"max_hop_usage": 0}), ("Y", "X", {})], "XW", "Y"),
    "payee_side_of_two_lines": ([("W", "X", {}), ("X", "Y", {}), ("Y", "X", {"can_be_intermediate": False}),
                                 ("Z", "Y", {})], "XW", "Z"),
    "permissive": ([("W", "X", {}), ("X", "Y", {})], "XW YX", "Y"),
}


@pytest.mark.parametrize("through_core", [False, True], ids=["router", "core_bypassing_router"])
@pytest.mark.parametrize("variant", list(_CASES))
@pytest.mark.asyncio
async def test_every_active_line_of_a_pair_keeps_its_policy(db_session, variant, through_core):
    # §15 P1 (2026-09-29): W -> X -> Y(-> Z) over offset hops made X a mediator against its own line.
    lines, debts, payee = _CASES[variant]
    debts = debts.split()
    eq, p = await _chain(db_session, lines, debts)
    service = PaymentService(db_session)
    routed = (await _router(db_session, eq)).find_flow_routes(p["W"].pid, p[payee].pid, Decimal("30"))
    assert bool(routed) == (variant == "permissive"), f"router: {routed}"
    if through_core:  # the core must refuse a route it is handed, whoever chose it
        path = [p[n].pid for n in "WXYZ"[: "WXYZ".index(payee) + 1]]
        service.router.find_flow_routes = lambda *_a, **_k: [(path, Decimal("30"))]
    status = "refused"
    try:
        result = await service.create_payment_internal(
            p["W"].id, to_pid=p[payee].pid, equivalent=eq.code, amount="30"
        )
        status = f"{result.status} via {[r.path for r in result.routes or []]}"
    except RoutingException:
        pass
    left = sorted(a for (a,) in (await db_session.execute(
        select(Debt.amount).where(Debt.equivalent_id == eq.id))).all())
    ok = variant == "permissive"
    expected = ("COMMITTED" if ok else "refused", [Decimal("20" if ok else "50")] * len(debts))
    require_target((status.split(" ")[0], left) == expected,
                   f"payment W -> {payee} 30: {status}; debts now {[str(a) for a in left]}")


@pytest.mark.parametrize("change", ["freeze", "forbid_mediation"])
@pytest.mark.asyncio
async def test_a_line_changed_after_routing_is_refused_by_the_core(db_session, monkeypatch, change):
    # The router may route over a cached graph; the core decides on the lines as they are in its transaction.
    monkeypatch.setattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 3600)
    eq, p = await _chain(db_session, [("W", "X", {}), ("X", "Y", {})], ["XW", "YX"])
    await PaymentRouter(db_session).build_graph(eq.code, use_shared_cache=True)
    line = (await db_session.execute(select(TrustLine).where(TrustLine.from_participant_id == p["X"].id))).scalar_one()
    if change == "freeze":  # 028 `F-028-28`/`F-028-29`: a freeze is the participant's (the line stays active)
        p["X"].status = "suspended"
    else:
        line.policy = {"can_be_intermediate": False}
    await db_session.commit()
    stale = PaymentRouter(db_session)
    await stale.build_graph(eq.code, use_shared_cache=True)
    assert stale.find_flow_routes(p["W"].pid, p["Y"].pid, Decimal("30")), "control: the cached graph still routes"
    with pytest.raises((RoutingException, ConflictException)):
        await PaymentService(db_session).create_payment_internal(
            p["W"].id, to_pid=p["Y"].pid, equivalent=eq.code, amount="30"
        )
    left = (await db_session.execute(select(Debt.amount).where(Debt.equivalent_id == eq.id))).scalars().all()
    assert sorted(left) == [Decimal("50"), Decimal("50")]


@pytest.mark.parametrize("hops, forbids", [("0", True), (0, True), (0.0, True), ("0.5", False), ("0.0", True),
                                            ("0.00", True), ("-0.0", True), ("0e0", True), (0.5, False)], ids=repr)
@pytest.mark.asyncio
async def test_max_hop_usage_forbids_mediation_exactly_at_zero(db_session, hops, forbids):
    # §15 round 2, P1: the API stores numeric strings; owner decision B (2026-09-29): forbid iff exactly zero.
    # A float 0.5 cannot pass the signed API (canonical_json refuses floats); the stand writes the column directly.
    eq, p = await _chain(db_session, [("X", "W", {"max_hop_usage": hops}), ("Y", "X", {})], [])
    outcome = "refused"
    try:
        result = await PaymentService(db_session).create_payment_internal(
            p["W"].id, to_pid=p["Y"].pid, equivalent=eq.code, amount="30"
        )
        outcome = f"{result.status} via {[r.path for r in result.routes or []]}"
    except RoutingException:
        pass
    left = (await db_session.execute(select(Debt.amount).where(Debt.equivalent_id == eq.id))).scalars().all()
    require_target((outcome == "refused") == forbids and len(left) == (0 if forbids else 2),
                   f"max_hop_usage {hops!r}: payment W -> Y 30: {outcome}; debts {[str(a) for a in left]}")


@MODE_B
@pytest.mark.asyncio
async def test_a_freeze_committed_between_routing_and_binding(db_session, monkeypatch, caplog):
    # §15 round 2, P2: P routes (its snapshot is taken), F freezes the pair's only line and COMMITS, then P binds.
    # 028 `F-028-28`/`F-028-29`: F suspends the payee (no line is frozen any more); the core reads it under its lock.
    # Owner decision A (T2415.3): the core's FOR SHARE fails with 40001, the retry refuses on a fresh snapshot.
    eq, people = await _seed(db_session)
    bind, seen = PaymentService._bind_payment, []

    async def freeze_then_bind(self, *args, **kwargs):
        if not seen:
            async with sessionmaker_of(db_session)() as other:
                await other.execute(update(Participant).where(Participant.id == people["B"].id).values(status="suspended"))
                await other.commit()
        seen.append(await self.session.scalar(text("SHOW transaction_isolation")))
        return await bind(self, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_bind_payment", freeze_then_bind)
    async with sessionmaker_of(db_session)() as session:
        try:
            result = await PaymentService(session).create_payment_internal(
                people["A"].id, to_pid=people["B"].pid, equivalent=eq.code, amount="30")
            outcome = result.status
        except (RoutingException, ConflictException) as exc:
            outcome = f"refused {exc}"
    assert seen and seen[0] == "read committed", seen
    left = (await db_session.execute(select(Debt.amount).where(Debt.equivalent_id == eq.id))).scalars().all()
    retried = [r.getMessage() for r in caplog.records if "payment.attempt_retry" in r.getMessage()]
    require_target(not outcome.startswith("COMMITTED"),
                   f"after the freeze: {outcome}, attempts {len(seen)}, debts {[str(a) for a in left]}")
    # The mechanism, not only the outcome: the lock failed with 40001, the fresh attempt refused (re-routing).
    assert not retried and left == [Decimal("50")], (outcome, retried, left)  # 027: read after the lock, no 40001
