"""Programme 023, slice (a): correctness of the MTCS planner (`app/core/clearing/flow_planner.py`), pure.

Spec Verification plan §2 and §5: the small exhaustive oracle, feasibility, reconstruction of the decomposition,
the optimality certificate with its negative controls, large atoms, determinism, incremental executability.

The oracle (`tests/p023_support.py::oracle_max_volume`) enumerates every integer circulation and shares no
code or idea with the planner. Every negative control is paired with the positive one on the same data, so a
check that rejects everything cannot pass here either.
"""

from __future__ import annotations

import random
import uuid

import pytest

from app.core.clearing.flow_planner import (
    PlanEdge,
    PlanIntegrityError,
    PlannedCycle,
    atoms_of,
    check_certificate,
    check_decomposition,
    check_feasible,
    plan_clearing,
)
from tests.p023_support import oracle_max_volume

_SEED = 20230926


def _v(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"p023:{name}")


def _edge(n: int, a: str, b: str, atoms: int) -> PlanEdge:
    return PlanEdge(uuid.UUID(int=(0x2300 << 64) | n), _v(a), _v(b), atoms)


def _ring(names: list[str], amounts: list[int], first_id: int) -> list[PlanEdge]:
    return [
        _edge(first_id + i, names[i], names[(i + 1) % len(names)], amounts[i]) for i in range(len(names))
    ]


def _random_graph(rnd: random.Random, *, max_cap: int = 3, scale: int = 1) -> list[PlanEdge]:
    n = rnd.randint(3, 8)
    vertices = [uuid.UUID(int=rnd.getrandbits(128)) for _ in range(n)]
    pairs, edges = set(), []
    for _ in range(rnd.randint(n, 2 * n + 2)):
        a, b = rnd.sample(range(n), 2)
        if (a, b) in pairs or (b, a) in pairs:
            continue  # no parallel debts, no opposing debts: the book's own invariants
        pairs.add((a, b))
        edges.append(PlanEdge(uuid.UUID(int=rnd.getrandbits(128)), vertices[a], vertices[b], rnd.randint(1, max_cap) * scale))
    return edges


def _oracle(edges: list[PlanEdge]) -> int:
    return oracle_max_volume([(e.debt_id, e.debtor_id, e.creditor_id, e.atoms) for e in edges])[0]


def _shared_edge_graph() -> list[PlanEdge]:
    # triangle A-B-C all 2; 5-cycle A-B-D-E-F sharing A->B (id 1), its own edges 1 (final.md P2-3).
    tri = _ring(["A", "B", "C"], [2, 2, 2], 1)
    five = _ring(["A", "B", "D", "E", "F"], [2, 1, 1, 1, 1], 10)
    return tri + five[1:]


# ------------------------------------------------------------------------------------------------ oracle


def test_the_oracle_itself_on_known_optima() -> None:
    assert _oracle(_shared_edge_graph()) == 8
    assert _oracle(_ring([f"r{k}" for k in range(11)], [1] * 11, 100)) == 11
    assert _oracle([_edge(1, "a", "b", 5), _edge(2, "b", "c", 5)]) == 0  # a path is not a circulation


def test_the_planner_matches_the_exhaustive_oracle_on_random_small_graphs() -> None:
    rnd = random.Random(_SEED)
    cases = nonzero = partial = 0
    for _ in range(400):
        edges = _random_graph(rnd)
        plan = plan_clearing(edges)
        expected = _oracle(edges)
        assert plan.v_edge == expected, (edges, plan.v_edge, expected)
        assert len(plan.cycles) <= len(edges)
        cases += 1
        nonzero += expected > 0
        partial += 0 < expected < sum(e.atoms for e in edges)
    # Anti-vacuum: the sample is not dominated by trivial (acyclic or fully clearable) graphs.
    assert cases == 400 and nonzero >= 120 and partial >= 120, (cases, nonzero, partial)  # measured 154, 151


# ------------------------------------------------------------------------------------- named structures


def test_shared_edge_gives_eight_not_six() -> None:
    edges = _shared_edge_graph()
    plan = plan_clearing(edges)
    assert plan.v_edge == 8 and plan.v_cyc == 2  # V_edge = 3·1 + 5·1; V_cyc = 1 + 1: different numbers
    assert sorted((len(c.edges), c.atoms) for c in plan.cycles) == [(3, 1), (5, 1)]
    left = {e.debt_id: plan.remaining[e.debt_id] for e in edges if plan.remaining[e.debt_id]}
    assert left == {edges[1].debt_id: 1, edges[2].debt_id: 1}  # B->C and C->A at 1; A->B exhausted


def test_a_ring_longer_than_any_depth_is_one_cycle() -> None:
    edges = _ring([f"r{k}" for k in range(11)], [3] * 11, 200)
    plan = plan_clearing(edges)
    assert [(len(c.edges), c.atoms) for c in plan.cycles] == [(11, 3)]
    assert (plan.v_edge, plan.v_cyc, plan.longest_cycle) == (33, 3, 11)


def test_an_acyclic_snapshot_and_an_empty_one_plan_nothing() -> None:
    for edges in ([], [_edge(1, "a", "b", 5), _edge(2, "b", "c", 7), _edge(3, "a", "c", 1)]):
        plan = plan_clearing(edges)
        assert plan.cycles == () and plan.v_edge == 0 and plan.v_cyc == 0


# ---------------------------------------------------------------------------------- feasibility checks


def test_feasibility_rejects_an_extra_atom_on_an_edge() -> None:
    edges = _shared_edge_graph()
    plan = plan_clearing(edges)
    check_feasible(edges, plan.remaining)  # positive control
    for e in edges:
        if plan.remaining[e.debt_id] > 0:
            tampered = dict(plan.remaining)
            tampered[e.debt_id] -= 1  # one more atom cleared on this edge alone
            with pytest.raises(PlanIntegrityError, match="not a circulation"):
                check_feasible(edges, tampered)
    over = dict(plan.remaining)
    over[edges[0].debt_id] = -1
    with pytest.raises(PlanIntegrityError, match="outside"):
        check_feasible(edges, over)


# -------------------------------------------------------------------------------------- decomposition


def test_decomposition_reconstructs_the_flow_and_is_deterministic() -> None:
    rnd = random.Random(_SEED + 1)
    for _ in range(200):
        edges = _random_graph(rnd, max_cap=9)
        plan = plan_clearing(edges)
        reduced = {e.debt_id: 0 for e in edges}
        for cycle in plan.cycles:
            assert len(cycle.edges) >= 3 and cycle.atoms > 0
            assert cycle.edges[0].debt_id == min((e.debt_id for e in cycle.edges), key=str)
            assert len({e.debtor_id for e in cycle.edges}) == len(cycle.edges)
            for e in cycle.edges:
                reduced[e.debt_id] += cycle.atoms
        assert all(reduced[e.debt_id] == e.atoms - plan.remaining[e.debt_id] for e in edges)
        shuffled = list(edges)
        rnd.shuffle(shuffled)
        assert plan_clearing(shuffled) == plan


def test_decomposition_check_rejects_a_tampered_plan() -> None:
    edges = _shared_edge_graph()
    plan = plan_clearing(edges)
    check_decomposition(edges, plan.remaining, plan.cycles)  # positive control
    with pytest.raises(PlanIntegrityError):
        check_decomposition(edges, plan.remaining, plan.cycles[1:])  # a cycle missing
    first = plan.cycles[0]
    with pytest.raises(PlanIntegrityError):
        check_decomposition(edges, plan.remaining, (PlannedCycle(first.edges, first.atoms + 1),) + plan.cycles[1:])
    with pytest.raises(PlanIntegrityError, match="closed walk"):
        check_decomposition(edges, plan.remaining, (PlannedCycle(first.edges[::-1], first.atoms),) + plan.cycles[1:])


def test_every_prefix_of_the_plan_leaves_the_rest_executable() -> None:
    rnd = random.Random(_SEED + 2)
    for _ in range(200):
        edges = _random_graph(rnd, max_cap=9)
        plan = plan_clearing(edges)
        amounts = {e.debt_id: e.atoms for e in edges}
        for i, cycle in enumerate(plan.cycles):
            for later in plan.cycles[i:]:
                assert all(amounts[e.debt_id] >= later.atoms for e in later.edges)
            for e in cycle.edges:
                amounts[e.debt_id] -= cycle.atoms
        assert amounts == plan.remaining


# ------------------------------------------------------------------------------------------ certificate


def test_certificate_rejects_a_feasible_but_suboptimal_flow() -> None:
    edges = _shared_edge_graph()
    plan = plan_clearing(edges)
    check_certificate(edges, plan.remaining, plan.potentials)  # positive control
    nothing = {e.debt_id: e.atoms for e in edges}  # T = 0: feasible, V_edge 0
    check_feasible(edges, nothing)
    with pytest.raises(PlanIntegrityError):
        check_certificate(edges, nothing, plan.potentials)
    # The greedy answer: the triangle at 2 (V_edge 6), feasible and suboptimal.
    greedy = {e.debt_id: e.atoms for e in edges}
    for e in edges[:3]:
        greedy[e.debt_id] = 0
    check_feasible(edges, greedy)
    with pytest.raises(PlanIntegrityError):
        check_certificate(edges, greedy, plan.potentials)
    # No potentials at all certify a suboptimal flow (LP duality), so random integer π fail on it too.
    rnd = random.Random(_SEED + 6)
    for _ in range(200):
        with pytest.raises(PlanIntegrityError):
            check_certificate(edges, greedy, {v: rnd.randint(-4, 4) for v in plan.potentials})


def test_certificate_rejects_a_shifted_potential() -> None:
    edges = _shared_edge_graph()
    plan = plan_clearing(edges)
    # Vertex C sits on the triangle's own edges, where 0 < M < L: their reduced cost must be exactly 0.
    c = _v("C")
    assert any(0 < plan.remaining[e.debt_id] < e.atoms and c in (e.debtor_id, e.creditor_id) for e in edges)
    for shift in (1, -1, 1000, -1000):
        corrupted = dict(plan.potentials)
        corrupted[c] += shift
        with pytest.raises(PlanIntegrityError):
            check_certificate(edges, plan.remaining, corrupted)


def test_certificate_holds_on_every_random_plan() -> None:
    rnd = random.Random(_SEED + 3)
    for _ in range(200):
        edges = _random_graph(rnd, max_cap=50)
        plan = plan_clearing(edges)
        check_certificate(edges, plan.remaining, plan.potentials)
        assert all(isinstance(p, int) for p in plan.potentials.values())


# ------------------------------------------------------------------------------------------ large atoms


_MAX_ATOMS = 99999999999999999999  # 999999999999.99999999, the Numeric(20,8) maximum


def test_large_atoms_scale_the_oracle_exactly() -> None:
    assert atoms_of("999999999999.99999999") == _MAX_ATOMS
    rnd = random.Random(_SEED + 4)
    k = _MAX_ATOMS // 3
    for _ in range(100):
        edges = _random_graph(rnd)
        big = [PlanEdge(e.debt_id, e.debtor_id, e.creditor_id, e.atoms * k) for e in edges]
        assert plan_clearing(big).v_edge == k * _oracle(edges)


def test_large_coprime_atoms_are_planned_and_certified() -> None:
    rnd = random.Random(_SEED + 5)
    for _ in range(100):
        edges = _random_graph(rnd)
        big = [
            PlanEdge(e.debt_id, e.debtor_id, e.creditor_id, _MAX_ATOMS - rnd.randrange(10**6)) for e in edges
        ]
        plan = plan_clearing(big)  # feasibility, decomposition and certificate are checked inside
        assert plan.v_edge == sum(len(c.edges) * c.atoms for c in plan.cycles)
        check_certificate(big, plan.remaining, plan.potentials)


def test_amounts_are_exact_atoms() -> None:
    assert atoms_of("0.00000001") == 1 and atoms_of("10.5") == 1050000000
    for bad in ("0.000000001", "NaN", "Infinity"):
        with pytest.raises(PlanIntegrityError):
            atoms_of(bad)


# ------------------------------------------------------------------------------------------ input refusal


def test_input_violating_the_book_invariants_is_refused() -> None:
    with pytest.raises(PlanIntegrityError, match="opposing"):
        plan_clearing([_edge(1, "a", "b", 5), _edge(2, "b", "a", 3)])
    with pytest.raises(PlanIntegrityError, match="twice"):
        plan_clearing([_edge(1, "a", "b", 5), _edge(1, "b", "c", 3)])
    with pytest.raises(PlanIntegrityError, match="positive integer"):
        plan_clearing([_edge(1, "a", "b", 0)])
    with pytest.raises(PlanIntegrityError, match="positive integer"):
        plan_clearing([_edge(1, "a", "b", 1.5)])
