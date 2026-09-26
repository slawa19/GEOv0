"""Programme 023, protocol v3: the TARGET-SCALE graph family and its manifest (spec 023, Verification plan §4,
«Целевая матрица v3»). Frozen before the first v3 timing, together with `measure_p023_planner_acceptance_v3.py`.

WHY A SEPARATE GENERATOR. The 020 generator (`scripts/measure_p020_detector_cost.py::generate`) adds its planted
structures ON TOP of the random vertices (h4k: 400 random + 89 planted = 489 vertices), so shrinking it would not
produce the declared participant counts. It stays unchanged and keeps generating the 20 stress cells. This module
generates graphs whose TOTAL participant count is exactly the declared one, with the planted structures placed on
those participants.

WHAT A GRAPH IS (all frozen below; one graph per size x degree, seed string `"{TARGET_SEED}:{graph id}"`):

* SIZES - (participants, eligible edges): 100/300, 100/500, 200/500 and the sensitivity case 200/1 000 (the
  density of 100/500). The eligible-edge count is exact: random edges are drawn until it is reached;
  excluded edges (closed line, refused consent) come on top and are counted in the manifest.
* DEGREES - `u` uniform endpoints; `s` hub-skewed: 5 % of participants are hubs and half of the random edges have
  a hub endpoint (direction random). For `u` the perimeter's "hubs" are the 5 % highest total-degree participants.
* STRUCTURE - a random cyclic region (random edges, 020 status/consent mix); one long ring through n/4
  participants embedded in that region; a shared-edge gadget (the R-020-1 shape: a triangle and a five-ring
  sharing one edge); a bottleneck gadget (six triangles through one shared edge); non-clearable structure:
  3 % pure debtors (sources) and 3 % pure creditors (sinks), whose edges cannot lie on a cycle. The two gadgets
  are on their own participants, which get no random edges. Non-clearable eligible edges (outside every
  non-trivial strongly connected component) must stay below NON_CLEARABLE_MAX_SHARE - checked, not assumed.
* PERIMETERS - `global`; `perim_hubs` (hubs + non-hub participants with an even index); `perim_nohubs` (non-hub
  participants with an odd index - hubs excluded); `empty` (no participant: the correctness control, whose plan
  must be empty). Both non-trivial perimeters cut the long ring and the random region.
* AMOUNTS - the planted amounts (`planted`), the existing variants through the caller's `draw_amount` (the v1
  runner's function: plateau, narrow, allequal, largeatoms), and `mixed`: magnitudes log-uniform over 1 atom ..
  10^20-1 atoms at scale 8, with the GCD of the eligible amounts in atoms asserted to be 1.

This module imports nothing from the 020 or 023 runners (they rewrite `DATABASE_URL` at import); the amount
variants are passed in. The consent decision is the production parser, imported lazily.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from decimal import Decimal
from functools import reduce

TARGET_SEED = 20260926
SIZES: dict[str, tuple[int, int]] = {
    "t100e300": (100, 300),
    "t100e500": (100, 500),
    "t200e500": (200, 500),
    "t200e1000": (200, 1000),  # sensitivity: the 100/500 density at 200 participants
}
DEGREES = ("u", "s")
HUB_FRACTION = 0.05
HUB_SHARE = 0.5
SOURCE_FRACTION = SINK_FRACTION = 0.03
LONG_RING_DIVISOR = 4
BOTTLENECK_TRIANGLES = 6
NON_CLEARABLE_MAX_SHARE = 0.34  # "non-clearable structure without allowing it to dominate"

#: 020's random-edge mix (`measure_p020_detector_cost.EDGE_MIX`, restated: that module is not imported here).
EDGE_MIX = [
    (0.82, "active", True),
    (0.06, "frozen", True),
    (0.04, "closed", True),
    (0.04, "active", "REFUSE"),
    (0.04, "active", "LEGACY"),
]
REFUSING_ENCODINGS = [False, "false", "0", "off", 0, " No "]
LEGACY_CONSENTING_ENCODINGS = ["<missing-key>", "<null-policy>", "yes", 1, "on"]
AMOUNT_CENTS = (1, 100000)
RING_AMOUNT = "7777.77"
SHARED_AMOUNTS = {"shared": "9000.00", "triangle_own": "900.00", "five_own": "9000.00"}
BOTTLENECK_AMOUNTS = {"shared": "2500.00", "own": "1000.00"}

MAX_ATOMS = 99999999999999999999  # 999999999999.99999999, the validator's door at scale 8
ATOM_SCALE = 10**8
SCOPES = ("global", "perim_hubs", "perim_nohubs", "empty")
MIXED = "mixed"


def graph_ids() -> list[str]:
    return [f"{size}{degree}" for size in SIZES for degree in DEGREES]


def _consents(consent) -> bool:
    from app.core.clearing.service import ClearingService

    policy = None if consent == "<null-policy>" else ({} if consent == "<missing-key>" else {"auto_clearing": consent})
    return ClearingService._policy_flag(policy, "auto_clearing", default=True)


def eligible(edge: dict) -> bool:
    from app.core.clearing.service import _CLEARABLE_TRUSTLINE_STATUSES

    return edge["status"] in _CLEARABLE_TRUSTLINE_STATUSES and _consents(edge["consent"])


def generate(graph_id: str) -> dict:
    """One target graph as plain data (the shape `measure_p023_planner_acceptance.build` fills), deterministic."""

    size, degree = graph_id[:-1], graph_id[-1]
    n, target = SIZES[size]
    if degree not in DEGREES:
        raise ValueError(graph_id)
    rnd = random.Random(f"{TARGET_SEED}:{graph_id}")
    vertices = [f"{graph_id}v{i:03d}" for i in range(n)]
    hub_count = round(n * HUB_FRACTION)
    designated_hubs = vertices[:hub_count] if degree == "s" else []
    rest = [v for v in vertices if v not in designated_hubs]
    picked = rnd.sample(rest, round(n * SOURCE_FRACTION) + round(n * SINK_FRACTION) + 6 + 2 + BOTTLENECK_TRIANGLES)
    k_src = round(n * SOURCE_FRACTION)
    k_snk = round(n * SINK_FRACTION)
    sources, sinks = set(picked[:k_src]), set(picked[k_src : k_src + k_snk])
    gadget = picked[k_src + k_snk :]
    shared_v, bottleneck_v = gadget[:6], gadget[6:]
    gadget_set = set(gadget)
    region = [v for v in vertices if v not in gadget_set]
    ring_candidates = [v for v in region if v not in sources and v not in sinks and v not in designated_hubs]
    ring_v = rnd.sample(ring_candidates, n // LONG_RING_DIVISOR)

    edges: list[dict] = []
    pairs: set[tuple[str, str]] = set()

    def add(debtor, creditor, amount, planted, status="active", consent=True):
        assert debtor != creditor and (debtor, creditor) not in pairs and (creditor, debtor) not in pairs
        pairs.add((debtor, creditor))
        edges.append({"debtor": debtor, "creditor": creditor, "amount": amount, "status": status,
                      "consent": consent, "planted": planted})

    for i, v in enumerate(ring_v):
        add(v, ring_v[(i + 1) % len(ring_v)], RING_AMOUNT, "long_ring")
    a, b, c = shared_v[:3]
    add(a, b, SHARED_AMOUNTS["shared"], "shared_edge")
    add(b, c, SHARED_AMOUNTS["triangle_own"], "shared_triangle")
    add(c, a, SHARED_AMOUNTS["triangle_own"], "shared_triangle")
    five = [a, b] + shared_v[3:]
    for i in range(1, 5):
        add(five[i], five[(i + 1) % 5], SHARED_AMOUNTS["five_own"], "shared_five")
    bb, bc = bottleneck_v[:2]
    add(bb, bc, BOTTLENECK_AMOUNTS["shared"], "bottleneck_edge")
    for x in bottleneck_v[2:]:
        add(x, bb, BOTTLENECK_AMOUNTS["own"], "bottleneck_triangle")
        add(bc, x, BOTTLENECK_AMOUNTS["own"], "bottleneck_triangle")

    refuse_i = legacy_i = 0

    def mix():
        nonlocal refuse_i, legacy_i
        roll, acc = rnd.random(), 0.0
        for share, status, consent in EDGE_MIX:
            acc += share
            if roll < acc:
                break
        if consent == "REFUSE":
            consent = REFUSING_ENCODINGS[refuse_i % len(REFUSING_ENCODINGS)]
            refuse_i += 1
        elif consent == "LEGACY":
            consent = LEGACY_CONSENTING_ENCODINGS[legacy_i % len(LEGACY_CONSENTING_ENCODINGS)]
            legacy_i += 1
        return status, consent

    count = sum(1 for e in edges if eligible(e))
    attempts = 0
    while count < target:
        attempts += 1
        if attempts > 1_000_000:
            raise RuntimeError(f"{graph_id}: cannot place {target} eligible edges")
        if designated_hubs and rnd.random() < HUB_SHARE:
            h, o = rnd.choice(designated_hubs), rnd.choice(region)
            x, y = (h, o) if rnd.random() < 0.5 else (o, h)
        else:
            x, y = rnd.choice(region), rnd.choice(region)
        if x in sinks or y in sources:
            x, y = y, x
        if x == y or x in sinks or y in sources or (x, y) in pairs or (y, x) in pairs:
            continue
        status, consent = mix()
        add(x, y, str(Decimal(rnd.randrange(*AMOUNT_CENTS)) / 100), None, status, consent)
        if eligible(edges[-1]):
            count += 1

    eligible_edges = [e for e in edges if eligible(e)]
    total_degree = {v: 0 for v in vertices}
    for e in eligible_edges:
        total_degree[e["debtor"]] += 1
        total_degree[e["creditor"]] += 1
    if degree == "s":
        hubs = list(designated_hubs)
    else:
        hubs = sorted(vertices, key=lambda v: (-total_degree[v], v))[:hub_count]
    hub_set = set(hubs)
    non_hubs = [v for v in vertices if v not in hub_set]
    perimeters = {
        "global": None,
        "perim_hubs": sorted(hub_set | {v for v in non_hubs if int(v[-3:]) % 4 != 0}),
        "perim_nohubs": sorted(v for v in non_hubs if int(v[-3:]) % 4 != 1),
        "empty": [],
    }
    graph = {"family": graph_id, "vertices": vertices, "edges": edges, "perimeters": perimeters,
             "roles": {"hubs": hubs, "sources": sorted(sources), "sinks": sorted(sinks), "long_ring": ring_v,
                       "shared_gadget": shared_v, "bottleneck_gadget": bottleneck_v}}
    graph["manifest"] = structure_manifest(graph_id, graph)
    return graph


# --------------------------------------------------------------------------------------------- manifest


def _dist(values) -> dict:
    s = sorted(values)
    return {"min": s[0], "p50": s[len(s) // 2], "p95": s[math.ceil(0.95 * len(s)) - 1], "max": s[-1],
            "mean": round(sum(s) / len(s), 2)}


def strongly_connected(vertices, edges) -> list[list[str]]:
    """Tarjan, iterative. Returns the components (each a list of vertices)."""

    adj: dict[str, list[str]] = {v: [] for v in vertices}
    for d, c in edges:
        adj[d].append(c)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on, stack, comps = set(), [], []
    counter = 0
    for root in vertices:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on.add(v)
            recurse = False
            for j in range(i, len(adj[v])):
                w = adj[v][j]
                if w not in index:
                    work.append((v, j + 1))
                    work.append((w, 0))
                    recurse = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                comps.append(comp)
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
    return comps


def structure_manifest(graph_id: str, graph: dict) -> dict:
    vertices, edges = graph["vertices"], graph["edges"]
    elig = [e for e in edges if eligible(e)]
    pairs = [(e["debtor"], e["creditor"]) for e in elig]
    comps = strongly_connected(vertices, pairs)
    comp_of = {v: k for k, comp in enumerate(comps) for v in comp}
    cyclic = [comp for comp in comps if len(comp) > 1]
    clearable = sum(1 for d, c in pairs if comp_of[d] == comp_of[c] and len(comps[comp_of[d]]) > 1)
    outd = {v: 0 for v in vertices}
    ind = {v: 0 for v in vertices}
    for d, c in pairs:
        outd[d] += 1
        ind[c] += 1
    hubs = set(graph["roles"]["hubs"])
    excluded = {"closed_line": 0, "consent_refused": 0}
    for e in edges:
        if not eligible(e):
            excluded["closed_line" if e["status"] == "closed" else "consent_refused"] += 1
    ring = graph["roles"]["long_ring"]
    perims = {}
    for name, members in graph["perimeters"].items():
        if members is None:
            continue
        inside = set(members)
        perims[name] = {
            "participants": len(inside),
            "hubs_inside": len(hubs & inside),
            "eligible_edges_inside": sum(1 for d, c in pairs if d in inside and c in inside),
            "eligible_edges_cut": sum(1 for d, c in pairs if (d in inside) != (c in inside)),
            "long_ring_cut": not set(ring) <= inside,
        }
    size, degree = graph_id[:-1], graph_id[-1]
    return {
        "graph": graph_id,
        "seed": f"{TARGET_SEED}:{graph_id}",
        "declared_participants": SIZES[size][0],
        "declared_eligible_edges": SIZES[size][1],
        "degree_distribution": {"u": "uniform", "s": "hub-skewed"}[degree],
        "participants_total": len(vertices),
        "edges_total": len(edges),
        "eligible_edges": len(elig),
        "excluded_edges": excluded,
        "hubs": sorted(hubs),
        "hub_eligible_edge_share": round(sum(1 for d, c in pairs if d in hubs or c in hubs) / len(pairs), 3),
        "out_degree": _dist(outd.values()),
        "in_degree": _dist(ind.values()),
        "total_degree": _dist(outd[v] + ind[v] for v in vertices),
        "cyclic_components": len(cyclic),
        "largest_cyclic_component": max((len(c) for c in cyclic), default=0),
        "participants_in_cyclic_components": sum(len(c) for c in cyclic),
        "eligible_edges_in_cyclic_components": clearable,
        "non_clearable_eligible_edges": len(elig) - clearable,
        "non_clearable_share": round((len(elig) - clearable) / len(elig), 3),
        "planted": {tag: sum(1 for e in edges if e["planted"] == tag)
                    for tag in sorted({e["planted"] for e in edges if e["planted"]})},
        "long_ring_length": len(ring),
        "sources": len(graph["roles"]["sources"]),
        "sinks": len(graph["roles"]["sinks"]),
        "perimeters": perims,
        "graph_sha256": hashlib.sha256(
            json.dumps({"edges": edges, "perimeters": graph["perimeters"]}, sort_keys=True, default=str).encode()
        ).hexdigest(),
    }


# ----------------------------------------------------------------------------------------------- amounts


def draw_mixed(rnd: random.Random) -> str:
    """Log-uniform magnitude: 1 .. 10^20-1 atoms at scale 8."""

    k = rnd.randrange(20)
    atoms = rnd.randrange(10**k, min(10 ** (k + 1), MAX_ATOMS + 1))
    return format(Decimal(atoms).scaleb(-8), "f")


def precision_of(variant: str, existing_precision_of) -> int:
    return 8 if variant == MIXED else existing_precision_of(variant)


def variant_graph(graph_id: str, variant: str, existing_draw_amount, existing_precision_of) -> dict:
    """The graph with its amounts redrawn for `variant` - every edge, as the v1 runner does for the stress cells."""

    graph = generate(graph_id)
    rnd = random.Random(f"{TARGET_SEED}:{graph_id}:{variant}")
    for e in graph["edges"]:
        e["amount"] = draw_mixed(rnd) if variant == MIXED else existing_draw_amount(variant, rnd, e["amount"])
    atoms = [int(Decimal(e["amount"]).scaleb(8)) for e in graph["edges"] if eligible(e)]
    gcd = reduce(math.gcd, atoms)
    if variant == MIXED and gcd != 1:
        raise RuntimeError(f"{graph_id}/{variant}: GCD of the eligible atoms is {gcd}, the frozen variant needs 1")
    graph["manifest"].update({
        "variant": variant,
        "precision": precision_of(variant, existing_precision_of),
        "distinct_amounts": len({e["amount"] for e in graph["edges"]}),
        "eligible_atoms_min": min(atoms),
        "eligible_atoms_max": max(atoms),
        "eligible_atoms_gcd": gcd,
        "eligible_magnitudes": sorted({len(str(a)) for a in atoms}),
        "variant_sha256": hashlib.sha256(json.dumps(graph["edges"], sort_keys=True, default=str).encode()).hexdigest(),
    })
    return graph


def plan_edges(graph: dict, scope: str):
    """The eligible edges a planner call would read for `scope` - for property tests without a database."""

    import uuid

    from app.core.clearing.flow_planner import PlanEdge, atoms_of

    ns = uuid.UUID("5f0b3c1e-0230-4d23-9a23-0000000f2303")
    members = graph["perimeters"][scope]
    inside = None if members is None else set(members)
    out = []
    for e in graph["edges"]:
        if not eligible(e):
            continue
        if inside is not None and not (e["debtor"] in inside and e["creditor"] in inside):
            continue
        out.append(PlanEdge(uuid.uuid5(ns, f"{e['debtor']}>{e['creditor']}"), uuid.uuid5(ns, e["debtor"]),
                            uuid.uuid5(ns, e["creditor"]), atoms_of(Decimal(e["amount"]))))
    return out
