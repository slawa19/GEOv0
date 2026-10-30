"""054-F: `PaymentRouter` searches on hand-built capacity graphs (no database).

F-1 (protocol §6.3.2: the first path is the one of the largest capacity): a payment one path carries whole goes on it;
otherwise the split before 054 runs unchanged - so no payment routed before is refused now. F-3: max-flow's 0 after a
policy refusal is checked by one search over every simple path. H-2: `payment_targets` in one linear search.
"""

import random

from decimal import Decimal

from app.core.payments.capacity import route_breaks_policy
from app.core.payments.router import PaymentRouter
from app.utils.exceptions import TimeoutException


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


def _assert_a_valid_split(router: PaymentRouter, routes, amount: Decimal, *, payee: str) -> None:
    """§8: the paths sum to the amount, no hop carries more than its capacity, every path respects the policy."""

    assert routes and sum(a for _, a in routes) == amount, routes
    _check_within_capacity(router, routes)
    for path, _ in routes:
        assert route_breaks_policy(path, router._hop_rules, payee=payee) is None, path


def _the_split_before_054(router: PaymentRouter, monkeypatch, *args, **kwargs):
    """`find_flow_routes` with step 1 finding nothing: what remains is the algorithm before 054, unchanged."""

    with monkeypatch.context() as m:
        m.setattr(router, "_bfs_carrying", lambda *a, **k: None)
        return router.find_flow_routes(*args, **kwargs)


def test_f1_one_path_carries_it_the_payment_goes_on_it_whole():
    router = _router(THIN_AND_WIDE)
    assert router.find_flow_routes("P", "Q", Decimal("5"), max_hops=6, max_paths=3, timeout_ms=5_000) == [
        (["P", "C", "D", "Q"], Decimal("5"))
    ]
    # The split before 054 spent the three paths on the thin hops and refused the same payment.
    assert router.find_flow_routes("P", "Q", Decimal("1"), max_hops=6, max_paths=3, timeout_ms=5_000) == [
        (["P", "Q"], Decimal("1"))
    ]
    # Single-path mode (`max_paths=1`, multipath off): the one path must carry all of it.
    assert router.find_flow_routes("P", "Q", Decimal("6"), max_hops=6, max_paths=1, timeout_ms=5_000) == [
        (["P", "C", "D", "Q"], Decimal("6"))
    ]
    assert router.find_flow_routes("P", "Q", Decimal("7"), max_hops=6, max_paths=1, timeout_ms=5_000) == []


def test_f1_no_path_carries_it_the_split_before_054_answers(monkeypatch):
    router = _router(THIN_AND_WIDE)
    for amount, hops in ((Decimal("3"), 2), (Decimal("4"), 2), (Decimal("7"), 6), (Decimal("13.01"), 6)):
        args = ("P", "Q", amount)
        kwargs = dict(max_hops=hops, max_paths=3, timeout_ms=5_000)
        assert router.find_flow_routes(*args, **kwargs) == _the_split_before_054(router, monkeypatch, *args, **kwargs)
    routes = router.find_flow_routes("P", "Q", Decimal("3"), max_hops=2, max_paths=3, timeout_ms=5_000)
    assert routes == [(["P", "Q"], Decimal("1")), (["P", "A", "Q"], Decimal("1")), (["P", "B", "Q"], Decimal("1"))]


def _random_policy_graph(rng, *, blocked: float) -> PaymentRouter:
    nodes = [f"N{i}" for i in range(rng.randint(3, 8))]
    router = PaymentRouter(None)
    router.graph = {x: {} for x in nodes}
    for u in nodes:
        for v in nodes:
            if u != v and rng.random() < 0.45:
                router.graph[u][v] = Decimal(rng.choice([1, 2, 3, 5, 8]))
                if rng.random() < 0.2:
                    router.edge_no_transit.setdefault(u, {})[v] = frozenset(rng.sample([u, v], 1))
                if rng.random() < 0.1:
                    router.edge_can_be_intermediate.setdefault(u, {})[v] = False
                if rng.random() < blocked:
                    router.edge_blocked_participants.setdefault(u, {})[v] = set(rng.sample(nodes, 1))
    return router


def test_f1_every_payment_the_split_before_054_routed_is_routed(monkeypatch):
    """Seeded random graphs with every policy rule: whenever the algorithm before 054 routes a payment, so does
    `find_flow_routes`, with a valid split. Counter-checks: step 1 routes some payment the old split refused, and on
    some graph step 1 misses a path (a route-dependent policy) and the old split routes it - both branches occur."""

    rng = random.Random(5410)
    gained = step2 = 0
    for _ in range(1500):
        router = _random_policy_graph(rng, blocked=0.3)
        s, t = rng.sample(sorted(router.graph), 2)
        args = (s, t, Decimal(rng.choice([1, 2, 3, 5, 8, 10, 13])))
        kwargs = dict(max_hops=rng.randint(1, 6), max_paths=rng.randint(1, 3), timeout_ms=60_000)
        old = _the_split_before_054(router, monkeypatch, *args, **kwargs)
        new = router.find_flow_routes(*args, **kwargs)
        if old:
            _assert_a_valid_split(router, new, args[2], payee=t)
        if new:
            _assert_a_valid_split(router, new, args[2], payee=t)
            gained += not old
        whole = router._bfs_carrying(s, t, args[2], max_hops=kwargs["max_hops"], forbidden_nodes=set(),
                                     deadline=float("inf"))
        step2 += whole is None and bool(old)
    assert gained > 5 and step2 > 10, (gained, step2)


def _mesh_router() -> PaymentRouter:
    """Review F-a: s->t 5 and s->a->t 5 carry 10; a 14-clique M1..M14 of capacity 100 reaches t, but every Mj->t
    (j >= 2) blocks every other M - a route-dependent rule; a search over every simple path for a path carrying 10
    spends the whole deadline in the clique."""

    ms = [f"M{i}" for i in range(1, 15)]
    edges = [("s", "t", "5"), ("s", "a", "5"), ("a", "t", "5"), ("s", "M1", "100")]
    edges += [(x, y, "100") for x in ms for y in ms if x != y] + [(x, "t", "100") for x in ms[1:]]
    router = _router(edges)
    router.edge_blocked_participants = {x: {"t": {m for m in ms if m != x}} for x in ms[1:]}
    return router


def _codex_router() -> PaymentRouter:
    """Codex P2 #1: P->T 5 and P->B->T 5 carry 10; P->X 10 into a 30-clique A1..A30 of 10, each Ai->T 10, and every
    pair Ai-T blocks X."""

    a = [f"A{i}" for i in range(1, 31)]
    edges = [("P", "T", "5"), ("P", "B", "5"), ("B", "T", "5"), ("P", "X", "10")]
    edges += [(x, y, "10") for x in a for y in a if x != y] + [("X", x, "10") for x in a] + [(x, "T", "10") for x in a]
    router = _router(edges)
    router.edge_blocked_participants = {x: {"T": {"X"}} for x in a}
    return router


def test_f1_review_inputs_a_split_the_old_strategy_found_at_once_is_found_in_the_deadline():
    for router, payer, payee in ((_mesh_router(), "s", "t"), (_codex_router(), "P", "T")):
        routes = router.find_flow_routes(payer, payee, Decimal("10"), max_hops=6, max_paths=3, timeout_ms=500)
        _assert_a_valid_split(router, routes, Decimal("10"), payee=payee)


def test_f1_review_seed_291_the_split_before_054_is_kept():
    """Review F-c, fuzz seed 291 (no blocked lists): no one path carries 9; the split before 054 is 3 + 3 + 3."""

    edges = [("P0", "P3", "7"), ("P0", "P7", "3"), ("P1", "P2", "7"), ("P1", "P3", "7"), ("P1", "P6", "7"),
             ("P1", "P7", "3"), ("P2", "P1", "3"), ("P2", "P4", "7"), ("P2", "P6", "7"), ("P3", "P6", "3"),
             ("P4", "P1", "7"), ("P4", "P3", "3"), ("P4", "P6", "3"), ("P4", "P7", "3"), ("P5", "P1", "3"),
             ("P5", "P3", "3"), ("P5", "P7", "3"), ("P6", "P0", "3"), ("P6", "P1", "3"), ("P6", "P3", "3"),
             ("P7", "P1", "3"), ("P7", "P2", "3"), ("P7", "P4", "7"), ("P7", "P5", "3")]
    router = _router(edges)
    router.edge_can_be_intermediate = {"P1": {"P2": False}, "P4": {"P3": False, "P6": False, "P7": False},
                                       "P6": {"P1": False, "P3": False}}
    router.edge_no_transit = {"P1": {"P6": frozenset({"P6", "P1"})}, "P5": {"P1": frozenset({"P5", "P1"}),
                              "P3": frozenset({"P3", "P5"})}, "P6": {"P1": frozenset({"P1"})},
                              "P7": {"P2": frozenset({"P2"})}}
    routes = router.find_flow_routes("P7", "P3", Decimal("9"), max_hops=8, max_paths=3, timeout_ms=60_000)
    _assert_a_valid_split(router, routes, Decimal("9"), payee="P3")


# --- 054-F-3 -------------------------------------------------------------------------------------------------------

W2_EDGES = [("P", "X", "10"), ("P", "Y", "10"), ("X", "M", "10"), ("Y", "Z", "10"), ("Z", "M", "10"), ("M", "Q", "10")]


def _w2_router() -> PaymentRouter:
    """P->X->M is first to M and refused at M->Q (Q's line blocks X); P->Y->Z->M->Q carries 10."""

    router = _router(W2_EDGES)
    router.edge_blocked_participants = {"M": {"Q": {"X"}}}
    return router


def test_f3_max_flow_finds_the_path_a_node_visited_set_hid():
    assert Decimal(_w2_router().calculate_max_flow("P", "Q").max_amount) >= Decimal("10")


def test_f3_max_flow_never_falls_below_the_one_before_and_searches_only_after_a_policy_refusal(monkeypatch):
    """On seeded random graphs: without a policy refusal the search over every simple path never runs (the work is
    the one before 054); with them the answer is >= the one before (that search answering None), and above it on some
    graphs (counter-check)."""

    rng = random.Random(543)
    for _ in range(400):
        router = _random_policy_graph(rng, blocked=0)
        router.edge_no_transit, router.edge_can_be_intermediate = {}, {}
        s, t = rng.sample(sorted(router.graph), 2)

        def _never(*a, **k):
            raise AssertionError("the search over every simple path ran without a policy refusal")

        monkeypatch.setattr(router, "_bfs_single_path", _never)
        router.calculate_max_flow(s, t)

    higher = 0
    for _ in range(800):
        router = _random_policy_graph(rng, blocked=0.15)
        s, t = rng.sample(sorted(router.graph), 2)
        new = Decimal(router.calculate_max_flow(s, t).max_amount)
        monkeypatch.setattr(router, "_bfs_single_path", lambda *a, **k: None)
        old = Decimal(router.calculate_max_flow(s, t).max_amount)
        assert new >= old, (new, old)
        higher += new > old
    assert higher > 0


def test_f3_max_flow_out_of_time_answers_as_before(monkeypatch):
    """The search under its deadline: 0, the answer before 054 - no error, since a payment's refusal reads this value
    for its text (`service.py`, `max_available`)."""

    router = _w2_router()

    def _out_of_time(*a, **k):
        raise TimeoutException("Routing timed out")

    monkeypatch.setattr(router, "_bfs_single_path", _out_of_time)
    assert Decimal(router.calculate_max_flow("P", "Q").max_amount) == 0


# --- 054-F-H2 ------------------------------------------------------------------------------------------------------


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
