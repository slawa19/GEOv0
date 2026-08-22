"""RT-010-5: a run's payment must not route through another run's participant.

Program 010, finding `F-010-3`, payment half.

`action_payment_real` scopes both ENDS of the payment to the run perimeter
(`app/api/v1/simulator.py:1546-1556`) - that part program 009 closed.  But
`PaymentRouter` builds its capacity graph from the whole equivalent
(`app/core/payments/router.py:197-210`), so the hops in between are unrestricted.  A payment
from a1 to a2, both inside run A, is routed through b1 of run B: b1's `Debt` rows are
created, b1's trust capacity is consumed, and the route - including b1's pid - is echoed
back into run A's SSE stream.

Edge direction is the thing to get right here, so it is stated explicitly:
`creditor_id = tl.from_participant_id` (the one who trusts) and
`debtor_id = tl.to_participant_id` (the one who may owe), and capacity is added as
`debtor -> creditor` (`app/core/payments/router.py:294-295`, `:334`).  So a `TrustLine(from=Y,
to=X)` produces the graph edge `X -> Y`: trust runs against the direction of payment.  Get
this backwards and the stand has no route at all, the test passes before the fix, and it
proves nothing - which is what the anti-vacuum cases below are for.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.simulator.models import RunRecord
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

_EQ = "RPY"


def _register_run(monkeypatch, *, run_id: str, pids: list[str]):
    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda rid: SimpleNamespace(
            run_id=str(rid),
            state="running",
            owner_id="",
            _real_seeded=True,
            _real_seeding_lock=None,
        ),
    )
    run = RunRecord(run_id=run_id, scenario_id="scn", mode="real", state="running")
    run._scenario_raw = {
        "participants": [
            {"id": p, "name": p.upper(), "type": "person", "status": "active"} for p in pids
        ],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, run_id, run)
    # `_topology_cache` has no TTL and no autouse reset, so a neighbouring test's topology
    # would otherwise decide NO_ROUTE vs INSUFFICIENT_CAPACITY here.
    PaymentRouter.invalidate_cache()
    return simulator_module


async def _seed(db_session, *, direct_edge: bool = False):
    """a1 -> b1 -> a2 only; no direct a1 -> a2 unless asked for."""

    eq = Equivalent(code=_EQ, precision=2, is_active=True)
    db_session.add(eq)

    people: dict[str, Participant] = {}
    for pid in ("a1", "a2", "b1"):
        p = Participant(
            id=uuid.uuid4(),
            pid=pid,
            display_name=pid.upper(),
            public_key=pid * 20,
            type="person",
            status="active",
            profile={},
        )
        people[pid] = p
        db_session.add(p)
    await db_session.commit()

    # TrustLine(from=Y, to=X) == graph edge X -> Y.
    edges = [("b1", "a1"), ("a2", "b1")]  # a1 -> b1, b1 -> a2
    if direct_edge:
        edges.append(("a2", "a1"))  # a1 -> a2
    for trusts, owes in edges:
        db_session.add(
            TrustLine(
                from_participant_id=people[trusts].id,
                to_participant_id=people[owes].id,
                equivalent_id=eq.id,
                limit=Decimal("1000"),
                status="active",
            )
        )
    await db_session.commit()
    return eq, people


async def _debts_touching(db_session, participant_id) -> int:
    rows = (
        await db_session.execute(
            select(Debt.id).where(
                (Debt.debtor_id == participant_id) | (Debt.creditor_id == participant_id)
            )
        )
    ).scalars().all()
    return len(rows)


async def _pay(client, run_id: str, amount: str = "50"):
    return await client.post(
        f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json={
            "from_pid": "a1",
            "to_pid": "a2",
            "equivalent": _EQ,
            "amount": amount,
            "client_action_id": f"rt_010_5_{run_id}",
        },
    )


@pytest.mark.asyncio
async def test_payment_does_not_route_through_another_runs_participant(
    client, db_session, monkeypatch
):
    _register_run(monkeypatch, run_id="run-a", pids=["a1", "a2"])
    _eq, people = await _seed(db_session)
    b1_id = people["b1"].id

    resp = await _pay(client, "run-a")

    touched = await _debts_touching(db_session, b1_id)
    assert touched == 0, (
        "the payment ran through a participant of another run: b1 now carries "
        f"{touched} debt rows created by a run that does not contain them. "
        f"response was {resp.status_code} {resp.text}"
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "NO_ROUTE", resp.text


@pytest.mark.asyncio
async def test_a_run_that_contains_the_hop_still_pays_through_it(
    client, db_session, monkeypatch
):
    """Anti-vacuum: the perimeter must refuse strangers, not multi-hop routing.

    It also proves the edge direction of the stand: if the trustlines were written the wrong
    way round there would be no route here either, and the case above would pass for a
    reason that has nothing to do with the perimeter.
    """

    _register_run(monkeypatch, run_id="run-ab", pids=["a1", "b1", "a2"])
    _eq, people = await _seed(db_session)

    resp = await _pay(client, "run-ab")

    assert resp.status_code == 200, resp.text
    assert await _debts_touching(db_session, people["b1"].id) > 0, (
        "the hop was inside the run, so it should have been used"
    )


@pytest.mark.asyncio
async def test_a_direct_edge_inside_the_perimeter_still_pays(
    client, db_session, monkeypatch
):
    """Anti-vacuum 2: narrowing must not disable single-hop payments."""

    _register_run(monkeypatch, run_id="run-a", pids=["a1", "a2"])
    _eq, people = await _seed(db_session, direct_edge=True)

    resp = await _pay(client, "run-a")

    assert resp.status_code == 200, resp.text
    assert await _debts_touching(db_session, people["b1"].id) == 0, (
        "the direct edge was available, so the foreign hop should not have been used"
    )


# The two service-side layers - narrowing the graph, and the postcondition on the chosen
# route - each close the route on their own, so the route-level cases above pass with either
# one disabled. That is the same trap the clearing half hit, and it needs the same answer:
# a case per layer.


@pytest.mark.asyncio
async def test_narrowing_layer_removes_the_foreign_hop_from_the_graph(db_session):
    from app.core.payments.service import PaymentService

    _eq, _people = await _seed(db_session)
    PaymentRouter.invalidate_cache()

    router = PaymentRouter(db_session)
    await router.build_graph(_EQ)

    unconfined = router.find_flow_routes("a1", "a2", Decimal("50"), max_hops=6)
    assert [p for p, _a in unconfined] == [["a1", "b1", "a2"]], (
        f"the stand must have exactly the three-hop route, got {unconfined}"
    )

    PaymentService._confine_router_to_perimeter(router, {"a1", "a2"})

    assert router.find_flow_routes("a1", "a2", Decimal("50"), max_hops=6) == [], (
        "the foreign hop is still reachable in the narrowed graph"
    )
    assert "b1" not in router.graph, router.graph
    assert all("b1" not in adj for adj in router.graph.values()), router.graph


@pytest.mark.asyncio
async def test_postcondition_refuses_a_route_that_escapes_the_perimeter(
    db_session, monkeypatch
):
    """Even if the graph narrowing were removed, a route through a stranger must not commit."""

    from app.core.payments.service import PaymentService
    from app.utils.exceptions import RoutingException

    _eq, people = await _seed(db_session)
    PaymentRouter.invalidate_cache()

    # Stand in for a broken narrowing: the router hands back a route through b1 anyway.
    def _route_through_the_stranger(*_args, **_kwargs):
        return [(["a1", "b1", "a2"], Decimal("50"))]

    monkeypatch.setattr(
        PaymentRouter, "find_flow_routes", staticmethod(_route_through_the_stranger)
    )

    service = PaymentService(db_session)
    with pytest.raises(RoutingException):
        await service.create_payment_internal(
            people["a1"].id,
            to_pid="a2",
            equivalent=_EQ,
            amount="50",
            idempotency_key=None,
            commit=True,
            allowed_participant_pids={"a1", "a2"},
        )

    assert await _debts_touching(db_session, people["b1"].id) == 0, (
        "the escaping route was refused, but debts were still created"
    )


@pytest.mark.asyncio
async def test_an_empty_perimeter_admits_nobody(db_session):
    from app.core.payments.service import PaymentService
    from app.utils.exceptions import RoutingException

    _eq, people = await _seed(db_session, direct_edge=True)
    PaymentRouter.invalidate_cache()

    service = PaymentService(db_session)
    with pytest.raises(RoutingException):
        await service.create_payment_internal(
            people["a1"].id,
            to_pid="a2",
            equivalent=_EQ,
            amount="50",
            idempotency_key=None,
            commit=True,
            allowed_participant_pids=set(),
        )


@pytest.mark.asyncio
async def test_narrowing_does_not_poison_the_shared_graph_cache(db_session, monkeypatch):
    """The cache is shared across runs; a scoped call must not leave a scoped graph in it.

    The cache-read branch is dead by default (`ROUTING_GRAPH_CACHE_TTL_SECONDS` is 0), so
    nothing in the suite exercises it - which is exactly why the copying it relies on
    deserves a case of its own.
    """

    from app.config import settings as app_settings
    from app.core.payments.service import PaymentService

    monkeypatch.setattr(app_settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 300, raising=False)
    _eq, _people = await _seed(db_session)
    PaymentRouter.invalidate_cache()

    warm = PaymentRouter(db_session)
    await warm.build_graph(_EQ)
    assert _EQ in PaymentRouter._graph_cache

    scoped = PaymentRouter(db_session)
    await scoped.build_graph(_EQ)
    PaymentService._confine_router_to_perimeter(scoped, {"a1", "a2"})
    assert "b1" not in scoped.graph

    fresh = PaymentRouter(db_session)
    await fresh.build_graph(_EQ)
    assert "b1" in fresh.graph, (
        "a scoped call poisoned the shared cache: the next unscoped caller lost a "
        "participant that has nothing to do with that run"
    )
