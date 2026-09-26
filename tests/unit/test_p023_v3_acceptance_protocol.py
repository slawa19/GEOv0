"""023 protocol v3: the target-family generator keeps its declared properties, and the runner's gate judges every
call against the 5 000 ms ceiling and nothing else on time (spec 023, Verification plan §4; section
«Проспективная правка приёмки»).

WHAT THIS DOES NOT SEE: timings (none are taken here), the database build, the child processes. The planner is run
only as a pure function on the generated eligible edges, to show that every non-empty scope has something to
clear (anti-vacuum of the matrix), never timed.
"""

from __future__ import annotations

import math
import os
from functools import reduce

import pytest

from app.core.clearing.flow_planner import plan_clearing
from scripts import p023_target_family as target

#: sha256 of each target graph (structure and planted amounts), frozen with the generator before timing.
FROZEN_GRAPH_SHA256 = {
    "t100e300u": "1944e49945b460a520943415d2489fa1c1fa48a8b6ccc01b80d856b035875362",
    "t100e300s": "3d850169d77c0ee7710b2e7903a9a4e03562a2706d2803741276128d27a0b132",
    "t100e500u": "c35bdbda4f533162231150d931cf2f7b3005b5e96ad6ab0cf17c33d0e78aa60e",
    "t100e500s": "9f0216e562859fc5e6c52eade382d3f931af46752077e1c9a96c92d3e937f6af",
    "t200e500u": "348fa93031689f605b33f959ba2b9bcc09b1cb925c14af190ea9366415edb242",
    "t200e500s": "83059806b2034e206ee0e47e3aad51f9ef2394e5e7dcf87636d2f5753977cb11",
    "t200e1000u": "87cea51214af1716e9376fb9edd295029dc2a210c0607a7bc3e50315dfa44b8f",
    "t200e1000s": "f9541c77f49fd12a35971a268d3854bb9084befa7e1e6fda57c8b373ffcbbc1f",
}


@pytest.fixture(scope="module")
def graphs() -> dict:
    return {g: target.generate(g) for g in target.graph_ids()}


def test_the_matrix_is_the_frozen_one() -> None:
    assert target.graph_ids() == list(FROZEN_GRAPH_SHA256)
    assert target.SCOPES == ("global", "perim_hubs", "perim_nohubs", "empty")
    assert target.TARGET_SEED == 20260926


def test_counts_are_the_declared_ones_and_the_graph_is_a_book_state(graphs) -> None:
    for g, graph in graphs.items():
        n, eligible = target.SIZES[g[:-1]]
        m = graph["manifest"]
        assert (m["participants_total"], m["eligible_edges"]) == (n, eligible), g
        assert len(graph["vertices"]) == n
        pairs = [(e["debtor"], e["creditor"]) for e in graph["edges"]]
        assert len(set(pairs)) == len(pairs), f"{g}: two debts on one pair"
        assert not {(c, d) for d, c in pairs} & set(pairs), f"{g}: opposing debts (the book nets them)"
        assert all(d != c for d, c in pairs)
        assert sum(m["excluded_edges"].values()) == m["edges_total"] - eligible > 0, f"{g}: no excluded edges"


def test_degree_distributions_differ_as_declared(graphs) -> None:
    for size in target.SIZES:
        u, s = graphs[f"{size}u"]["manifest"], graphs[f"{size}s"]["manifest"]
        assert s["hub_eligible_edge_share"] >= 0.35, size
        assert u["hub_eligible_edge_share"] <= 0.25, size
        assert s["total_degree"]["max"] > u["total_degree"]["max"], size
        assert len(s["hubs"]) == len(u["hubs"]) == round(target.SIZES[size][0] * target.HUB_FRACTION)


def test_planted_structures_are_present_and_eligible(graphs) -> None:
    for g, graph in graphs.items():
        n = target.SIZES[g[:-1]][0]
        planted = graph["manifest"]["planted"]
        assert planted == {"bottleneck_edge": 1, "bottleneck_triangle": 2 * target.BOTTLENECK_TRIANGLES,
                           "long_ring": n // target.LONG_RING_DIVISOR, "shared_edge": 1, "shared_five": 4,
                           "shared_triangle": 2}, g
        assert all(target.eligible(e) for e in graph["edges"] if e["planted"])
        ring = graph["roles"]["long_ring"]
        ring_edges = {(e["debtor"], e["creditor"]) for e in graph["edges"] if e["planted"] == "long_ring"}
        assert ring_edges == {(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))}
        sources, sinks = set(graph["roles"]["sources"]), set(graph["roles"]["sinks"])
        assert sources and sinks
        assert not any(e["creditor"] in sources or e["debtor"] in sinks for e in graph["edges"])


def test_non_clearable_structure_is_present_but_does_not_dominate(graphs) -> None:
    for g, graph in graphs.items():
        m = graph["manifest"]
        assert 0 < m["non_clearable_eligible_edges"]
        assert m["non_clearable_share"] <= target.NON_CLEARABLE_MAX_SHARE, g
        assert m["cyclic_components"] >= 3, g  # the region and the two gadgets at least


def test_perimeters_include_and_exclude_hubs_and_cut_cycles(graphs) -> None:
    for g, graph in graphs.items():
        hubs = set(graph["roles"]["hubs"])
        p = graph["perimeters"]
        assert p["global"] is None and p["empty"] == []
        assert hubs <= set(p["perim_hubs"]) and not hubs & set(p["perim_nohubs"])
        for scope in ("perim_hubs", "perim_nohubs"):
            info = graph["manifest"]["perimeters"][scope]
            assert info["long_ring_cut"] and info["eligible_edges_cut"] > 0, (g, scope)


def test_every_non_empty_scope_has_something_to_clear_and_empty_has_nothing(graphs) -> None:
    for g, graph in graphs.items():
        for scope in target.SCOPES:
            plan = plan_clearing(target.plan_edges(graph, scope))
            if scope == "empty":
                assert (len(plan.edges), len(plan.cycles)) == (0, 0)
            else:
                assert len(plan.cycles) > 0, (g, scope)
        assert plan_clearing(target.plan_edges(graph, "global")).longest_cycle >= len(graph["roles"]["long_ring"])


def test_the_generator_is_deterministic_and_frozen(graphs) -> None:
    for g, graph in graphs.items():
        assert target.generate(g)["manifest"]["graph_sha256"] == graph["manifest"]["graph_sha256"]
        assert graph["manifest"]["graph_sha256"] == FROZEN_GRAPH_SHA256[g], f"{g}: the frozen generator changed"


def test_mixed_amounts_span_every_magnitude_with_gcd_one() -> None:
    def never(*_):
        raise AssertionError("the mixed variant must not use the existing variants")

    for g in target.graph_ids():
        m = target.variant_graph(g, target.MIXED, never, never)["manifest"]
        assert m["eligible_atoms_gcd"] == 1 and m["precision"] == 8
        assert m["eligible_atoms_min"] < 10**3 and m["eligible_atoms_max"] > 10**18
        assert len(m["eligible_magnitudes"]) >= 15, g
        assert m["eligible_atoms_max"] <= target.MAX_ATOMS


# ================================================================================================ the runner


@pytest.fixture(scope="module")
def v3():
    """The runner (and the v1/v2/020 modules under it) rewrite DATABASE_URL at import; restore the environment."""

    saved = dict(os.environ)
    try:
        from scripts import measure_p023_planner_acceptance_v3 as module
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return module


def _messages(*, cold=100.0, warmup=(100.0, 100.0), samples=None, digest="d1", plan=None, extra=()):
    samples = [100.0] * 20 if samples is None else samples
    out = [{"kind": "ready"}, {"kind": "cold", "ms": cold, "statements": 2, "digest": digest}]
    out += [{"kind": "warmup", "ms": ms, "statements": 2, "digest": digest} for ms in warmup]
    out += [{"kind": "sample", "ms": ms, "statements": 2, "digest": digest} for ms in samples]
    out += list(extra)
    out.append(plan if plan is not None else {"kind": "plan", "cycles": 5, "eligible_edges": 30, "longest_cycle": 4})
    out.append({"kind": "done"})
    return out


MEASURED = {"memory": "MEASURED", "peak_python_bytes": 1, "memory_traced_ms": 1.0, "memory_digest": "d1"}


def test_the_runner_imports_only_on_the_frozen_planner_and_judged_calls(v3) -> None:
    from scripts import measure_p023_planner_acceptance_v2 as v2

    assert v3.JUDGED_CHILD is v2.child_cell and v3.MEMORY_CHILD is v2.child_memory
    v3.check_sources()  # the tree's planner, v1 and v2 hash as at ef3c640
    real = v3.REPO_ROOT.joinpath("app/core/clearing/flow_planner.py").read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="flow_planner.py is not the ef3c640 source"):
        v3.check_sources(lambda rel: real + "\n# edited\n" if rel.endswith("flow_planner.py") else
                         v3.REPO_ROOT.joinpath(rel).read_text(encoding="utf-8"))


def test_the_gate_is_the_5000_ms_ceiling_on_every_call(v3) -> None:
    assert v3.judge(_messages(), MEASURED)["verdict"] == "PASS"
    assert v3.judge(_messages(samples=[100.0] * 19 + [4999.0]), MEASURED)["verdict"] == "PASS"
    assert v3.judge(_messages(samples=[100.0] * 19 + [5000.0]), MEASURED)["verdict"] == "PASS"
    row = v3.judge(_messages(samples=[100.0] * 19 + [5001.0]), MEASURED)
    assert row["verdict"] == "FAIL" and row["fail_reasons"] == ["calls over 5000 ms: [5001.0]"]
    assert v3.judge(_messages(cold=5001.0), MEASURED)["verdict"] == "FAIL"
    assert v3.judge(_messages(warmup=(100.0, 5001.0)), MEASURED)["verdict"] == "FAIL"


def test_no_percentile_gate_remains(v3) -> None:
    # p95 far above the historical 1 000 / 2 000 ms bars, every call under the ceiling: the deliberate relaxation.
    row = v3.judge(_messages(samples=[4900.0] * 20), MEASURED)
    assert row["verdict"] == "PASS" and row["p95_ms"] == 4900.0


def test_every_other_failure_still_fails_the_cell(v3) -> None:
    timeout = {"kind": "timeout", "error": "killed: silent for 10 s"}
    error = {"kind": "error", "error": "PlanIntegrityError: debt x: residual forward arc with reduced cost -1 < 0"}
    cases = {
        "timeout": v3.judge(_messages(samples=[100.0] * 3, extra=[timeout]), MEASURED),
        "integrity error": v3.judge(_messages(extra=[error]), MEASURED),
        "19 samples": v3.judge(_messages(samples=[100.0] * 19), MEASURED),
        "no cold": v3.judge([m for m in _messages() if m["kind"] != "cold"], MEASURED),
        "two digests": v3.judge(_messages()[:-3] + [{"kind": "sample", "ms": 1.0, "statements": 2, "digest": "d2"}]
                                + _messages()[-2:], MEASURED),
        "no plan": v3.judge([m for m in _messages() if m["kind"] != "plan"], MEASURED),
        "memory not measured": v3.judge(_messages(), {"memory": "NOT MEASURED", "peak_python_bytes": None,
                                                      "memory_reason": "TIMEOUT"}),
        "memory other plan": v3.judge(_messages(), {**MEASURED, "memory_digest": "d9"}),
        "empty control not empty": v3.judge(_messages(), MEASURED, empty_control=True),
    }
    for name, row in cases.items():
        assert row["verdict"] == "FAIL" and row["fail_reasons"], name
    empty_plan = {"kind": "plan", "cycles": 0, "eligible_edges": 0, "longest_cycle": 0}
    assert v3.judge(_messages(plan=empty_plan), MEASURED, empty_control=True)["verdict"] == "PASS"


def test_the_existing_variants_come_from_the_v1_runner_unchanged(v3) -> None:
    graph = v3.target_graph("t100e300u", "largeatoms")
    atoms = [int(e["amount"].replace(".", "")) for e in graph["edges"]]
    assert graph["manifest"]["precision"] == 8
    assert min(atoms) >= v3.v1.MAX_ATOMS - v3.v1.LARGE_SPREAD_ATOMS and max(atoms) <= v3.v1.MAX_ATOMS
    assert reduce(math.gcd, atoms) == 1
    assert len(v3.TARGET_GRAPHS) * len(v3.TARGET_VARIANTS) * len(v3.TARGET_SCOPES) == 192
