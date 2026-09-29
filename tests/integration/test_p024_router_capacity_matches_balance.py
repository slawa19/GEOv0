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
from sqlalchemy import select

from app.core.balance.service import BalanceService
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.exceptions import RoutingException
from tests.debt_setup import debt_fixture_setup
from tests.p019_support import TargetMismatch, require_target, target_xfail

_RED = target_xfail("024 T2415.2", "the router builds no edge from a counter-debt without a line")


async def _seed(session, *, debt_b_a="50", line_b_a=None, line_c_b=None):
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
                    status="active",
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


@_RED
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


@_RED
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


@_RED
@pytest.mark.asyncio
async def test_a_debt_only_edge_is_an_intermediate_hop_as_the_core_allows(db_session):
    # C trusts B: B may pay C. A -> B is the counter-debt only; the core checks no role per hop.
    eq, people = await _seed(db_session, line_c_b="100")
    service = PaymentService(db_session)
    await service._bind_payment(
        f"hop-{uuid.uuid4()}", [([people[n].pid for n in "ABC"], Decimal("30"))], eq.id
    )
    router = await _router(db_session, eq)
    assert not _routable(router, people, "50.01", to="C")
    require_target(_routable(router, people, "30", to="C"), "router has no A -> B -> C route")
