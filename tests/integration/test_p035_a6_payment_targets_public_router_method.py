"""035 A6 (`T3511`): `PaymentRouter` answers "whom can this participant pay" through PUBLIC names.

The simulator's `payment-targets` route (`app/api/v1/simulator.py`, 034 `F-034-11`) computes it out of two private
ones: it narrows the router with `PaymentService._confine_router_to_perimeter` and asks `PaymentRouter._bfs_single_path`
for a path to every vertex, then sorts and cuts. 034 S2 may not reach into the payments core; the public names are
made here and the route moves to them there.

`_as_the_route_computes` below is that route's own loop, copied (`simulator.py`, the block after `build_graph`): the
public method must give the same targets, hops and paths, in the same order, for every source, depth, limit and
perimeter of a populated graph. Routing and capacity are not changed - the method calls the same BFS.

What this does not see: the route itself (it still uses the private names until 034 S2), and `max_available`, which
the route takes from the already public `calculate_max_flow`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup

_HOPS = (1, 2, 3, 6, 8)
_LIMITS = (1, 3, 200)


def _as_the_route_computes(router: PaymentRouter, src: str, *, max_hops: int, limit: int):
    """The loop of `payment_targets` in `app/api/v1/simulator.py`, as it stands on `213c84e3`."""

    if src not in (router.graph or {}):
        return []
    results: list[tuple[int, str, list[str]]] = []
    for to_pid in (router.graph or {}).keys():
        dst = str(to_pid)
        if not dst or dst == src:
            continue
        path = router._bfs_single_path(src, dst, Decimal("0"), max_hops=int(max_hops))
        if not path:
            continue
        hops = max(0, len(path) - 1)
        if hops <= 0:
            continue
        results.append((hops, dst, path))
    results.sort(key=lambda x: (x[0], x[1]))
    if limit and len(results) > int(limit):
        results = results[: int(limit)]
    return results


def _narrow_as_on_213c84e3(router: PaymentRouter, allowed) -> None:
    """The body of `PaymentService._confine_router_to_perimeter` as it stood on `213c84e3`, copied: since A6 that
    method calls the public one, so comparing the two would compare a function with itself."""

    router.graph = {
        u: {v: cap for v, cap in adj.items() if v in allowed}
        for u, adj in router.graph.items()
        if u in allowed
    }
    router.edge_can_be_intermediate = {
        u: {v: flag for v, flag in adj.items() if v in allowed}
        for u, adj in router.edge_can_be_intermediate.items()
        if u in allowed
    }
    router.edge_blocked_participants = {
        u: {v: blocked for v, blocked in adj.items() if v in allowed}
        for u, adj in router.edge_blocked_participants.items()
        if u in allowed
    }


async def _world(db_session):
    """Twelve participants on a ring with chords and a spur of two more, in one equivalent. Every line `creditor -> debtor` lets the debtor
    pay the creditor. Some lines are spent by a debt, one hop may not be a transit, one line blocks a participant."""

    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"PT{n}", precision=2, is_active=True, metadata_={})
    people = [Participant(pid=f"pt{i:02d}_{n}", display_name=f"pt{i}", public_key=f"pk-pt{i}-{n}", type="person",
                          status="active", profile={}) for i in range(14)]
    db_session.add_all([eq, *people])
    await db_session.flush()

    def line(creditor: int, debtor: int, limit: str = "100", **policy) -> TrustLine:
        return TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                         equivalent_id=eq.id, limit=Decimal(limit), status="active",
                         policy={"can_be_intermediate": True, **policy})

    lines = [line((i + 1) % 12, i) for i in range(12)]                      # i can pay i+1: the ring, one way
    lines += [line(i, (i + 1) % 12, "50") for i in (0, 3, 6, 9)]            # a few edges back
    lines += [line(5, 0), line(9, 2), line(1, 7), line(11, 4)]              # chords
    lines += [line(8, 3, can_be_intermediate=False)]                        # 3 may pay 8, but not pass through
    lines += [line(10, 6, blocked_participants=[people[0].pid])]            # 6 -> 10, closed to pt00
    lines += [line(4, 10, "10"), line(2, 8, "0")]                           # one to be spent, one with no room
    # A spur off the ring: 3 -> 12 -> 13, and 12 may be paid over that hop but not passed through. So 13 has capacity
    # all the way from the ring and is still nobody's target but 12's - the case the hop policy decides.
    lines += [line(12, 3, can_be_intermediate=False), line(13, 12)]
    db_session.add_all(lines)
    debts = [Debt(debtor_id=people[10].id, creditor_id=people[4].id, equivalent_id=eq.id, amount=Decimal("10")),
             Debt(debtor_id=people[2].id, creditor_id=people[3].id, equivalent_id=eq.id, amount=Decimal("40"))]
    async with debt_fixture_setup(db_session, label="p035-a6"):
        db_session.add_all(debts)
    await db_session.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq.code, [p.pid for p in people]


async def _router(db_session, code: str) -> PaymentRouter:
    router = PaymentRouter(db_session)
    await router.build_graph(code)
    return router


@pytest.mark.asyncio
async def test_the_public_method_gives_the_targets_the_route_computes(db_session):
    code, pids = await _world(db_session)
    perimeters = {"the whole equivalent": None, "half of it": set(pids[:6]), "a strip": set(pids[2:11]),
                  "nobody else": {pids[0]}}

    seen = with_long_routes = cut_by_limit = 0
    answers: dict[str, set] = {}
    for name, allowed in perimeters.items():
        private, public = await _router(db_session, code), await _router(db_session, code)
        through_the_service = await _router(db_session, code)
        if allowed is not None:
            _narrow_as_on_213c84e3(private, allowed)
            public.confine_to_participants(allowed)
            PaymentService._confine_router_to_perimeter(through_the_service, allowed)  # the name its callers use
        for narrowed in (public, through_the_service):
            assert narrowed.graph == private.graph, name
            assert narrowed.edge_can_be_intermediate == private.edge_can_be_intermediate, name
            assert narrowed.edge_blocked_participants == private.edge_blocked_participants, name
        for src in [*pids, "nobody-of-this-equivalent"]:
            for max_hops in _HOPS:
                for limit in _LIMITS:
                    expected = _as_the_route_computes(private, src, max_hops=max_hops, limit=limit)
                    got = public.payment_targets(src, max_hops=max_hops, limit=limit)
                    assert [(t.hops, t.to_pid, list(t.path)) for t in got] == expected, (name, src, max_hops, limit)
                    seen += len(expected)
                    with_long_routes += sum(1 for hops, _, _ in expected if hops >= 3)
                    cut_by_limit += len(expected) == limit
        answers[name] = {t.to_pid for t in public.payment_targets(pids[2], max_hops=8, limit=200)}

    # Anti-vacuum: the stand has many targets, routes of three hops and more, lists the limit really cut, and the
    # perimeter, the depth and the hop policy each change an answer.
    assert seen > 500 and with_long_routes > 100 and cut_by_limit > 100, (seen, with_long_routes, cut_by_limit)
    assert answers["nobody else"] == set() and answers["half of it"] < answers["the whole equivalent"], answers
    whole = await _router(db_session, code)
    assert len(whole.payment_targets(pids[0], max_hops=1, limit=200)) < len(whole.payment_targets(pids[0], max_hops=8, limit=200))
    assert all(len(t.path) - 1 == t.hops and t.path[0] == pids[0] and t.path[-1] == t.to_pid
               for t in whole.payment_targets(pids[0], max_hops=8, limit=200))
    reachable_ignoring_policy = _plain_reachability(whole.graph, pids[0], 8)
    assert {t.to_pid for t in whole.payment_targets(pids[0], max_hops=8, limit=200)} <= reachable_ignoring_policy
    by_policy = sum(
        len(_plain_reachability(whole.graph, src, hops)) - len(whole.payment_targets(src, max_hops=hops, limit=200))
        for src in pids for hops in _HOPS)
    assert by_policy > 0, "no answer of this stand depends on a hop policy"
    assert pids[13] in _plain_reachability(whole.graph, pids[3], 8)
    assert pids[13] not in {t.to_pid for t in whole.payment_targets(pids[3], max_hops=8, limit=200)}
    assert [t.to_pid for t in whole.payment_targets(pids[12], max_hops=8, limit=200)] == [pids[13]]


def _plain_reachability(graph, src: str, max_hops: int) -> set[str]:
    """Vertices within `max_hops` over edges with capacity, with no policy at all."""

    frontier, found = {src}, set()
    for _ in range(max_hops):
        frontier = {v for u in frontier for v, cap in graph.get(u, {}).items() if cap > 0} - found - {src}
        found |= frontier
    return found


@pytest.mark.asyncio
async def test_no_limit_means_every_target(db_session):
    code, pids = await _world(db_session)
    router = await _router(db_session, code)
    everything = router.payment_targets(pids[0], max_hops=8)
    assert [(t.hops, t.to_pid, list(t.path)) for t in everything] == _as_the_route_computes(
        router, pids[0], max_hops=8, limit=10**6)
    assert len(everything) > 3 and router.payment_targets(pids[0], max_hops=8, limit=3) == everything[:3]
