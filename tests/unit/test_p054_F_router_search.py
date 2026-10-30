"""054-F-1: `PaymentRouter.find_flow_routes` on a hand-built capacity graph (no database).

Protocol §6.3.2/§6.3.4: the first path is the one of the largest capacity, the split goes by capacity. Shortest paths
of any capacity first spent `max_paths` on thin hops and refused a payment two wide paths carry.
"""

from decimal import Decimal

from app.core.payments.router import PaymentRouter


def _router(edges) -> PaymentRouter:
    router = PaymentRouter(None)
    graph: dict = {}
    for u, v, cap in edges:
        graph.setdefault(u, {})[v] = Decimal(cap)
        graph.setdefault(v, {})
    router.graph = graph
    return router


# Thin: P->Q, P->A->Q, P->B->Q (capacity 1 each). Wide: P->C->D->Q and P->E->F->Q (capacity 6 each).
THIN_AND_WIDE = [
    ("P", "Q", "1"), ("P", "A", "1"), ("A", "Q", "1"), ("P", "B", "1"), ("B", "Q", "1"),
    ("P", "C", "6"), ("C", "D", "6"), ("D", "Q", "6"),
    ("P", "E", "6"), ("E", "F", "6"), ("F", "Q", "6"),
]


def _check_within_capacity(router: PaymentRouter, routes) -> None:
    used: dict = {}
    for path, amount in routes:
        for u, v in zip(path, path[1:]):
            used[(u, v)] = used.get((u, v), Decimal("0")) + amount
    for (u, v), total in used.items():
        assert total <= router.graph[u][v], (u, v, total)


def test_no_single_path_carries_it_widest_paths_are_taken_before_thin_short_ones():
    router = _router(THIN_AND_WIDE)
    routes = router.find_flow_routes("P", "Q", Decimal("10"), max_hops=6, max_paths=3, timeout_ms=5_000)
    assert routes == [(["P", "C", "D", "Q"], Decimal("6")), (["P", "E", "F", "Q"], Decimal("4"))]
    _check_within_capacity(router, routes)


def test_one_path_carries_it_the_shortest_such_path_is_taken_whole():
    router = _router(THIN_AND_WIDE)
    assert router.find_flow_routes("P", "Q", Decimal("5"), max_hops=6, max_paths=3, timeout_ms=5_000) == [
        (["P", "C", "D", "Q"], Decimal("5"))
    ]
    # Single-path mode (`max_paths=1`, multipath off): the one path must carry all of it.
    assert router.find_flow_routes("P", "Q", Decimal("6"), max_hops=6, max_paths=1, timeout_ms=5_000) == [
        (["P", "C", "D", "Q"], Decimal("6"))
    ]
    assert router.find_flow_routes("P", "Q", Decimal("7"), max_hops=6, max_paths=1, timeout_ms=5_000) == []


def test_the_shortest_path_is_kept_when_it_carries_the_payment():
    router = _router(THIN_AND_WIDE)
    assert router.find_flow_routes("P", "Q", Decimal("1"), max_hops=6, max_paths=3, timeout_ms=5_000) == [
        (["P", "Q"], Decimal("1"))
    ]


def test_more_than_the_paths_carry_is_refused_and_max_hops_still_binds():
    router = _router(THIN_AND_WIDE)
    # 6 + 6 + 1 = 13 is the most three paths carry.
    routes = router.find_flow_routes("P", "Q", Decimal("13"), max_hops=6, max_paths=3, timeout_ms=5_000)
    assert sorted(a for _, a in routes) == [Decimal("1"), Decimal("6"), Decimal("6")]
    _check_within_capacity(router, routes)
    assert router.find_flow_routes("P", "Q", Decimal("13.01"), max_hops=6, max_paths=3, timeout_ms=5_000) == []
    # Within two hops only the thin paths exist: 3 at most.
    assert router.find_flow_routes("P", "Q", Decimal("4"), max_hops=2, max_paths=3, timeout_ms=5_000) == []
    assert len(router.find_flow_routes("P", "Q", Decimal("3"), max_hops=2, max_paths=3, timeout_ms=5_000)) == 3


def test_the_linear_search_answers_exactly_as_the_exhaustive_one():
    """`_find_path` (054-F) against `_bfs_single_path` on seeded random graphs with both policy rules: the same path
    or the same None every time; so is the exact fallback `_exact_path`, and its filter - the local rules alone - finds
    nothing only where nothing exists. Counter-check: both the linear answer and the fallback (a route-dependent
    `blocked_participants` refusal) occur, and the filter both answers and lets the search through, so no branch
    passes vacuously."""

    import random

    rng = random.Random(54)
    exact_answers = fallbacks = filtered = 0
    for _ in range(1500):
        nodes = [f"N{i}" for i in range(rng.randint(3, 9))]
        router = PaymentRouter(None)
        router.graph = {x: {} for x in nodes}
        for u in nodes:
            for v in nodes:
                if u != v and rng.random() < 0.4:
                    router.graph[u][v] = Decimal(rng.choice([1, 2, 3, 5, 8]))
                    if rng.random() < 0.2:
                        router.edge_no_transit.setdefault(u, {})[v] = frozenset(rng.sample([u, v], 1))
                    if rng.random() < 0.4:
                        router.edge_blocked_participants.setdefault(u, {})[v] = set(rng.sample(nodes, 1))
        s, t = rng.sample(nodes, 2)
        amount, hops = Decimal(rng.choice([0, 1, 2, 3, 5])), rng.randint(1, 6)
        expected = router._bfs_single_path(s, t, amount, max_hops=hops, graph_override=router.graph)
        assert router._find_path(s, t, amount, max_hops=hops, graph=router.graph) == expected
        assert router._exact_path(s, t, amount, max_hops=hops, graph=router.graph) == expected
        relaxed, _ = router._bfs_by_node(s, t, amount, max_hops=hops, graph=router.graph, local_only=True)
        assert relaxed is not None or expected is None
        filtered += relaxed is None
        linear, exact = router._bfs_by_node(s, t, amount, max_hops=hops, graph=router.graph)
        if exact:
            assert linear == expected
            exact_answers += 1
        else:
            fallbacks += 1
    assert exact_answers > 1000 and fallbacks > 30 and 30 < filtered < 1470, (exact_answers, fallbacks, filtered)


def _random_policy_graph(rng, *, blocked: bool) -> PaymentRouter:
    nodes = [f"N{i}" for i in range(rng.randint(3, 8))]
    router = PaymentRouter(None)
    router.graph = {x: {} for x in nodes}
    for u in nodes:
        for v in nodes:
            if u != v and rng.random() < 0.45:
                router.graph[u][v] = Decimal(rng.choice([1, 2, 3, 5, 8]))
                if rng.random() < 0.2:
                    router.edge_no_transit.setdefault(u, {})[v] = frozenset(rng.sample([u, v], 1))
                if blocked and rng.random() < 0.15:
                    router.edge_blocked_participants.setdefault(u, {})[v] = set(rng.sample(nodes, 1))
    return router


def test_f3_max_flow_finds_the_path_a_node_visited_set_hid():
    """W2 on the graph alone: P->X->M is first to M and refused at M->Q (Q's line blocks X); P->Y->Z->M->Q carries
    10."""

    router = _router([("P", "X", "10"), ("P", "Y", "10"), ("X", "M", "10"), ("Y", "Z", "10"), ("Z", "M", "10"),
                      ("M", "Q", "10")])
    router.edge_blocked_participants = {"M": {"Q": {"X"}}}
    assert Decimal(router.calculate_max_flow("P", "Q").max_amount) == Decimal("10")


def test_f3_max_flow_never_falls_below_the_node_visited_search_and_costs_the_same_without_route_rules(monkeypatch):
    """The two invariants of 054-F-3 on seeded random graphs. With only local rules (no `blocked_participants`) the
    exact search (`_exact_path`) is never called - the work is the one before 054. With them, the answer is >= the
    one of the node-visited search alone (the algorithm before 054: the exact search answering None), and above it on
    some graphs (counter-check: the fallback is not vacuous)."""

    import random

    rng = random.Random(543)
    for _ in range(400):
        router = _random_policy_graph(rng, blocked=False)
        s, t = rng.sample(sorted(router.graph), 2)

        def _never(*a, **k):
            raise AssertionError("the exact search ran without a route-dependent refusal")

        monkeypatch.setattr(router, "_exact_path", _never)
        router.calculate_max_flow(s, t)

    higher = 0
    for _ in range(600):
        router = _random_policy_graph(rng, blocked=True)
        s, t = rng.sample(sorted(router.graph), 2)
        new = Decimal(router.calculate_max_flow(s, t).max_amount)
        monkeypatch.setattr(router, "_exact_path", lambda *a, **k: None)
        old = Decimal(router.calculate_max_flow(s, t).max_amount)
        assert new >= old, (new, old)
        higher += new > old
    assert higher > 0


def test_f3_max_flow_out_of_time_returns_the_flow_found_so_far(monkeypatch):
    """The exact search (`_exact_path`) under its deadline: max-flow stops adding and returns what it has - no
    error, since a payment's refusal reads this value for its text (`service.py`, `max_available`)."""

    from app.utils.exceptions import TimeoutException

    # P->Q carries 3 directly; to M the first path is via X, refused at M->Q, so the path via Y needs the fallback.
    router = _router([("P", "Q", "3"), ("P", "X", "10"), ("P", "Y", "10"), ("X", "M", "10"), ("Y", "Z", "10"),
                      ("Z", "M", "10"), ("M", "Q", "10")])
    router.edge_blocked_participants = {"M": {"Q": {"X"}}}
    assert Decimal(router.calculate_max_flow("P", "Q").max_amount) == Decimal("13")

    def _out_of_time(*a, **k):
        raise TimeoutException("Routing timed out")

    monkeypatch.setattr(router, "_exact_path", _out_of_time)
    assert Decimal(router.calculate_max_flow("P", "Q").max_amount) == Decimal("3")


def test_h2_payment_targets_answers_as_the_per_target_search():
    """054-F-H2: the one linear search of `payment_targets` against the per-target `_bfs_single_path` it replaced, on
    seeded random graphs with every policy rule, a zero-capacity hop and a neighbour that is not a graph key: the same
    targets, hops and paths. Counter-check: both the linear answer and the per-target fallback occur."""

    import random

    from app.core.payments.router import PaymentTarget

    rng = random.Random(5402)
    exact_answers = fallbacks = 0
    for _ in range(1500):
        nodes = [f"N{i}" for i in range(rng.randint(2, 9))]
        router = PaymentRouter(None)
        router.graph = {x: {} for x in nodes}
        for u in nodes:
            for v in nodes + ["OUT"]:
                if u != v and rng.random() < 0.35:
                    router.graph[u][v] = Decimal(rng.choice([0, 1, 2]))
                    if rng.random() < 0.25:
                        router.edge_no_transit.setdefault(u, {})[v] = frozenset(rng.sample([u, v], 1))
                    if rng.random() < 0.05:
                        router.edge_can_be_intermediate.setdefault(u, {})[v] = False
                    if rng.random() < 0.2:
                        router.edge_blocked_participants.setdefault(u, {})[v] = set(rng.sample(nodes, 1))
        s, hops = rng.choice(nodes), rng.randint(1, 6)
        expected = []
        for t in router.graph:
            path = None if t == s else router._bfs_single_path(s, t, Decimal("0"), max_hops=hops)
            if path:
                expected.append(PaymentTarget(t, len(path) - 1, tuple(path)))
        expected.sort(key=lambda target: (target.hops, target.to_pid))
        assert router.payment_targets(s, max_hops=hops, timeout_ms=60_000) == expected
        _, exact = router._reach_by_node(s, max_hops=hops, deadline=float("inf"))
        exact_answers += exact
        fallbacks += not exact
    assert exact_answers > 1000 and fallbacks > 30, (exact_answers, fallbacks)


def test_h2_payment_targets_out_of_time_raises_and_the_fallback_has_the_deadline(monkeypatch):
    """Out of time is `TimeoutException` (HTTP 504 E007), never a shorter list; the per-target fallback runs under the
    same deadline."""

    import pytest

    from app.utils.exceptions import TimeoutException

    router = _router([("P", "X", "10"), ("P", "Y", "10"), ("X", "M", "10"), ("Y", "Z", "10"), ("Z", "M", "10"),
                      ("M", "Q", "10")])
    router.edge_blocked_participants = {"M": {"Q": {"X"}}}  # route-dependent: the fallback runs
    deadlines = []

    def _recording(search):
        def call(*a, **k):
            deadlines.append((search.__name__, k.get("deadline")))
            return search(*a, **k)
        return call

    for name in ("_reach_by_node", "_bfs_single_path"):
        monkeypatch.setattr(router, name, _recording(getattr(router, name)))
    assert {t.to_pid: t.hops for t in router.payment_targets("P", max_hops=6)} == {
        "X": 1, "Y": 1, "M": 2, "Z": 2, "Q": 4}
    assert {name for name, _ in deadlines} == {"_reach_by_node", "_bfs_single_path"}
    assert all(d is not None for _, d in deadlines)

    calls = {"n": 0}

    def _clock() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 999.0  # the first call sets the deadline, every later one is past it

    monkeypatch.setattr("app.core.payments.router.time.perf_counter", _clock)
    with pytest.raises(TimeoutException):
        router.payment_targets("P", max_hops=6)
