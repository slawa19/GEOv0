"""Programme 020, stage 2: the experimental CTE and DFS detectors against the declared contract.

The detectors live in `scripts/p020_experimental_detectors.py`, outside the production path. This module
checks them against an INDEPENDENT oracle written here, not against each other and not against the current
`find_cycles` (spec, Verification plan §4: "the new detector found what the old one found" is not a
criterion - the old ones disagree):

* ADMISSION ORACLE - eligibility computed in Python from the seeded edge specs with the production consent
  parser `ClearingService._policy_flag(..., default=True)` and the production status tuple. The SQL relation
  both detectors read must admit exactly the same debt ids, over every consent encoding the column can hold
  (booleans, numbers incl. `0.0`, trimmed/cased strings, unknown strings, arrays, objects, a missing key, a
  NULL policy) and every line status. This is the "SQL may not silently widen consent" check.
* CYCLE ORACLE - every simple cycle found by brute force (all simple paths from every vertex, de-duplicated by
  edge SET - no canonical-start trick, so it does not share the detectors' key idea), filtered to 3..depth,
  sorted by amount DESC then the full identity, cut at the limit.

The graphs: the three R-020-1 graphs, the retention graphs, the t1210 shared-edge and triangle+quadrangle
graphs, and a seeded random graph with a small amount palette (many equal-amount ties) and a 2-cycle, run
with a small limit so the bound and the tie key both do work. Depths 3, 4, 6, 7, 10; global and scoped.
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest
from app.core.clearing.service import _CLEARABLE_TRUSTLINE_STATUSES, ClearingService
from scripts.p020_experimental_detectors import (
    detect_cte,
    detect_dfs,
    load_eligible_edges,
    render_for_find_cycles,
)
from tests.conftest import MODE_B
from tests.p020_support import (
    MISSING_KEY,
    NULL_POLICY,
    Edge,
    debt_uuid,
    participant_uuid,
    ring,
    seed_graph,
)

_DEPTHS = [3, 4, 6, 7, 10]


def _eligible_oracle(edges: list[Edge], scope: set[str] | None) -> set[uuid.UUID]:
    out = set()
    for e in edges:
        if Decimal(e.amount) <= 0 or e.status not in _CLEARABLE_TRUSTLINE_STATUSES:
            continue
        policy = None if e.consent == NULL_POLICY else ({} if e.consent == MISSING_KEY else {"auto_clearing": e.consent})
        if not ClearingService._policy_flag(policy, "auto_clearing", default=True):
            continue
        if scope is not None and not (e.debtor in scope and e.creditor in scope):
            continue
        out.add(e.debt_id)
    return out


def _cycle_oracle(edges: list[Edge], eligible: set[uuid.UUID], max_depth: int, limit: int):
    usable = [e for e in edges if e.debt_id in eligible]
    out_of: dict[str, list[Edge]] = {}
    for e in usable:
        out_of.setdefault(e.debtor, []).append(e)
    found: dict[frozenset, list[Edge]] = {}

    def walk(start, node, path, seen):
        for e in out_of.get(node, ()):
            if e.creditor == start:
                cyc = path + [e]
                if 3 <= len(cyc) <= max_depth:
                    found.setdefault(frozenset(x.debt_id for x in cyc), cyc)
                continue
            if e.creditor in seen or len(path) + 1 >= max_depth:
                continue
            walk(start, e.creditor, path + [e], seen | {e.creditor})

    for v in sorted(out_of):
        walk(v, v, [], {v})
    ranked = sorted(
        (
            (-min(Decimal(x.amount) for x in cyc), tuple(sorted(str(x.debt_id) for x in cyc)))
            for cyc in found.values()
        )
    )
    return [(-neg, ident) for neg, ident in ranked[:limit]]


def _as_result(cycles):
    return [(c.amount, c.identity) for c in cycles]


def _assert_canonical(cycles) -> None:
    for c in cycles:
        ids = [e[0] for e in c.edges]
        assert ids[0] == min(ids), "rotation must start at the smallest debt id"
        for a, b in zip(c.edges, c.edges[1:] + c.edges[:1]):
            assert a[2] == b[1], "consecutive edges must chain creditor -> debtor"
        assert len({e[1] for e in c.edges}) == len(c.edges), "a cycle may not repeat a vertex"
        assert c.amount == min(e[3] for e in c.edges)


# ---------------------------------------------------------------------------------------- graph builders


def _g_overflow():
    edges = []
    for i in range(101):
        edges += ring(
            [f"p020a{i:03d}{v}" for v in "xyz"],
            [str(100 + i), str(5000 + i), str(5000 + i)],
            [debt_uuid(0xA, i * 4 + k) for k in range(3)],
        )
    return edges, None


def _g_ladder():
    g, shared = 0xB5, debt_uuid(0xB5, 1)
    tri = ring(["la", "lb", "lc"], ["100", "10", "10"], [shared, debt_uuid(g, 2), debt_uuid(g, 3)])
    five = ring(["la", "lb", "l0", "l1", "l2"], ["100"] * 5, [shared] + [debt_uuid(g, 10 + k) for k in range(4)])
    return tri + five[1:], None


def _g_ties():
    edges = []
    for tag, amount, g, fl, fi, sl, si in [
        ("30", "30", 0xC3, 3, [50, 51], 3, [20, 21]),
        ("20", "20", 0xC2, 3, [20, 21], 4, [50, 51, 52]),
        ("10", "10", 0xC1, 3, [50, 51], 4, [20, 21, 22]),
    ]:
        shared = debt_uuid(g, 1)
        first = ring([f"c{tag}a", f"c{tag}b"] + [f"c{tag}f{k}" for k in range(fl - 2)], [amount] * fl, [shared] + [debt_uuid(g, n) for n in fi])
        second = ring([f"c{tag}a", f"c{tag}b"] + [f"c{tag}s{k}" for k in range(sl - 2)], [amount] * sl, [shared] + [debt_uuid(g, n) for n in si])
        edges += first + second[1:]
    return edges, None


def _g_reach():
    edges = []
    for n in range(3, 11):
        edges += ring([f"r{n:02d}{k}" for k in range(n)], ["10"] * n, [debt_uuid(0xD0 + n, k) for k in range(n)])
    return edges, None


def _g_admission(kind):
    def build():
        edges = []
        for i in range(12):
            tri = ring([f"x{i:02d}{v}" for v in "abc"], [str(1000 + i)] * 3, [debt_uuid(0xE0, i * 4 + k) for k in range(3)])
            if kind == "consent":
                tri = [Edge(e.debt_id, e.debtor, e.creditor, e.amount, consent=False) for e in tri]
            elif kind == "closed":
                e0 = tri[0]
                tri = [Edge(e0.debt_id, e0.debtor, e0.creditor, e0.amount, status="closed")] + tri[1:]
            edges += tri
        edges += ring(["xoka", "xokb", "xokc"], ["5"] * 3, [debt_uuid(0xE1, k) for k in range(3)])
        scope = None
        if kind == "perimeter":
            scope = {"xoka", "xokb", "xokc"} | {f"x{i:02d}{v}" for i in range(12) for v in "ab"}
        return edges, scope

    return build


def _g_t1210_shared_edge():
    spec = [("a", "b", "100"), ("b", "c", "10"), ("c", "a", "10"), ("b", "d", "100"), ("d", "a", "100")]
    return [Edge(debt_uuid(0x12, k), d, c, amt) for k, (d, c, amt) in enumerate(spec)], None


def _g_t1210_tri_quad():
    return (
        ring(["t1", "t2", "t3"], ["10"] * 3, [debt_uuid(0x13, k) for k in range(3)])
        + ring(["q1", "q2", "q3", "q4"], ["10"] * 4, [debt_uuid(0x14, k) for k in range(4)]),
        None,
    )


_CONSENTS = [
    True, False, "false", " False ", "0", "no", "OFF", "yes", "on", "1", "maybe", "",
    0, 1, 0.0, 2.5, None, [], [1], {}, {"a": 1}, MISSING_KEY, NULL_POLICY,
]


def _g_random(scoped: bool):
    def build():
        rnd = random.Random(20)
        pids = [f"n{k:02d}" for k in range(12)]
        pairs, edges, n = set(), [], 0
        while len(edges) < 34:
            a, b = rnd.choice(pids), rnd.choice(pids)
            if a == b or (a, b) in pairs:
                continue
            pairs.add((a, b))
            roll = rnd.random()
            status = "closed" if roll < 0.08 else ("frozen" if roll < 0.2 else "active")
            consent = True if rnd.random() < 0.6 else rnd.choice(_CONSENTS)
            edges.append(Edge(debt_uuid(0x20, rnd.randrange(1 << 40) * 64 + n), a, b, rnd.choice(["1", "2", "3", "5"]), status, consent))
            n += 1
        # a 2-cycle with consent on both lines: not a cycle of 3..depth, must never be returned
        edges += [Edge(debt_uuid(0x21, 1), "m1", "m2", "7"), Edge(debt_uuid(0x21, 2), "m2", "m1", "7")]
        # the consent catalogue: one line per encoding the column can hold, on its own pair, so the
        # admission check sees every encoding whatever the random draw above produced
        for k, consent in enumerate(_CONSENTS):
            edges.append(Edge(debt_uuid(0x22, k), f"k{k:02d}a", f"k{k:02d}b", "4", "active", consent))
        scope = set(pids[:9]) | {"m1", "m2"} | {f"k{k:02d}{v}" for k in range(len(_CONSENTS)) for v in "ab"} if scoped else None
        return edges, scope

    return build


_GRAPHS = {
    "r020_overflow": (_g_overflow, 100),
    "r020_ladder": (_g_ladder, 100),
    "r020_ties": (_g_ties, 100),
    "retention_reach": (_g_reach, 100),
    "admission_consent": (_g_admission("consent"), 100),
    "admission_closed": (_g_admission("closed"), 100),
    "admission_perimeter": (_g_admission("perimeter"), 100),
    "t1210_shared_edge": (_g_t1210_shared_edge, 100),
    "t1210_triangle_quadrangle": (_g_t1210_tri_quad, 100),
    "random_ties_global": (_g_random(False), 7),
    "random_ties_scoped": (_g_random(True), 7),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("graph", sorted(_GRAPHS))
async def test_experimental_detectors_meet_the_contract(db_session, graph) -> None:
    build, limit = _GRAPHS[graph]
    edges, scope = build()
    eq = await seed_graph(db_session, "PZE", edges)
    scope_ids = None if scope is None else {participant_uuid(p) for p in scope}

    eligible = _eligible_oracle(edges, scope)
    loaded = {row[0] for row in await load_eligible_edges(db_session, eq.id, scope_ids=scope_ids)}
    assert loaded == eligible, (
        f"{graph}: the SQL relation admits {sorted(map(str, loaded - eligible))} beyond the production "
        f"consent/status/perimeter rule and misses {sorted(map(str, eligible - loaded))}"
    )

    for depth in _DEPTHS:
        expected = _cycle_oracle(edges, eligible, depth, limit)
        cte = await detect_cte(db_session, eq.id, depth, scope_ids=scope_ids, limit=limit)
        dfs = await detect_dfs(db_session, eq.id, depth, scope_ids=scope_ids, limit=limit)
        dfs_x = await detect_dfs(db_session, eq.id, depth, scope_ids=scope_ids, limit=limit, bounded=False)
        for name, got in (("cte", cte), ("dfs", dfs), ("dfs_exhaustive", dfs_x)):
            _assert_canonical(got)
            assert _as_result(got) == expected, f"{graph} depth {depth}: {name} differs from the contract oracle"


@pytest.mark.asyncio
async def test_the_random_graph_exercises_what_it_claims(db_session) -> None:
    """Anti-vacuum for the random stand: it has excluded edges, equal-amount ties and more cycles than the limit."""

    edges, _ = _g_random(False)()
    eligible = _eligible_oracle(edges, None)
    assert len(eligible) < len(edges), "no edge was excluded"
    all_cycles = _cycle_oracle(edges, eligible, 10, 10_000)
    assert len(all_cycles) > 7, f"only {len(all_cycles)} cycles - the limit never binds"
    amounts = [a for a, _ in all_cycles]
    assert len(set(amounts)) < len(amounts), "no equal-amount tie"
    consents = {repr(e.consent) for e in edges}
    assert {repr(c) for c in _CONSENTS} <= consents, "a consent encoding is missing from the stand"
    assert {repr(c) for c in _CONSENTS if not ClearingService._policy_flag(
        None if c == NULL_POLICY else ({} if c == MISSING_KEY else {"auto_clearing": c}), "auto_clearing", default=True
    )} and eligible, "the catalogue must hold both refusing and consenting encodings"


@pytest.mark.asyncio
async def test_an_empty_perimeter_admits_nobody(db_session) -> None:
    edges, _ = _g_t1210_tri_quad()
    eq = await seed_graph(db_session, "PZE", edges)
    assert await detect_cte(db_session, eq.id, 6, scope_ids=set()) == []
    assert await detect_dfs(db_session, eq.id, 6, scope_ids=set()) == []
    # Control: the same graph unscoped has both cycles.
    assert len(await detect_cte(db_session, eq.id, 6)) == 2


async def amount_first_auto_clear(service: ClearingService, detect, equivalent, max_depth: int) -> int:
    """The stage-3 `auto_clear` semantics over an experimental detector: full depth on every detection,
    candidates in order until the first success, then detect again; the 101-success ceiling kept."""

    equivalent_id, precision = equivalent.id, equivalent.precision  # read once: execution expires the row
    count = 0
    while True:
        cycles = await detect(service.session, equivalent_id, max_depth)
        rendered = await render_for_find_cycles(service.session, cycles, precision=precision)
        executed = False
        for cycle in rendered:
            if await service.execute_clearing(cycle):
                executed = True
                count += 1
                break
        if not executed or count > 100:
            return count


@MODE_B
@pytest.mark.asyncio
@pytest.mark.parametrize("detector", ["cte", "dfs"])
async def test_the_r020_ladder_graph_under_amount_first_auto_clear(db_session, detector) -> None:
    """R-020-1's ladder part, driven by the experimental detector: one occurrence, triangle edges left at 10."""

    from sqlalchemy import func, select

    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction

    edges, _ = _g_ladder()
    eq = await seed_graph(db_session, "PZL", edges)
    eq_id = eq.id
    detect = detect_cte if detector == "cte" else detect_dfs
    cleared = await amount_first_auto_clear(ClearingService(db_session), detect, eq, 6)

    db_session.expire_all()
    remaining = sorted(
        (str(d.id), Decimal(d.amount))
        for d in (await db_session.execute(select(Debt).where(Debt.equivalent_id == eq_id))).scalars()
    )
    occurrences = (
        await db_session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.type == "CLEARING", Transaction.state == "COMMITTED"
            )
        )
    ).scalar_one()
    assert cleared == occurrences == 1
    assert remaining == [(str(debt_uuid(0xB5, 2)), Decimal("10")), (str(debt_uuid(0xB5, 3)), Decimal("10"))]


# ------------------------------------------------- review P2-1: amount distributions where the bound is weak

_PALETTES = {
    # a plateau of one amount holding most edges, some above and some below it
    "plateau": lambda rnd: "5" if rnd.random() < 0.6 else rnd.choice(["6", "7", "8", "1", "2", "3"]),
    "narrow": lambda rnd: rnd.choice(["10.00", "10.01", "10.02"]),
    "allequal": lambda rnd: "4",  # the amount bound never prunes: strict `<` against an equal floor
}


def _g_dense_small(palette: str):
    def build():
        rnd = random.Random(f"p2-1:{palette}")
        pids = [f"s{k:02d}" for k in range(9)]
        pairs, edges = set(), []
        while len(edges) < 30:
            a, b = rnd.choice(pids), rnd.choice(pids)
            if a == b or (a, b) in pairs or (b, a) in pairs:
                continue
            pairs.add((a, b))
            edges.append(Edge(debt_uuid(0x40, rnd.randrange(1 << 40) * 64 + len(edges)), a, b, _PALETTES[palette](rnd)))
        return edges, None

    return build


@pytest.mark.asyncio
@pytest.mark.parametrize("palette", sorted(_PALETTES))
@pytest.mark.parametrize("limit", [1, 5, 17])
async def test_the_bound_is_exact_on_weak_amount_distributions(db_session, palette, limit) -> None:
    """Small dense graphs whose amounts are a plateau, a narrow palette or all equal, against the oracle."""

    edges, _ = _g_dense_small(palette)()
    eq = await seed_graph(db_session, "PZD", edges)
    eligible = _eligible_oracle(edges, None)
    for depth in _DEPTHS:
        expected = _cycle_oracle(edges, eligible, depth, limit)
        full = _cycle_oracle(edges, eligible, depth, 10_000)
        if depth >= 6:
            # Anti-vacuum: the limit binds, and the cutoff sits on a tie spanning it.
            assert len(full) > limit, f"{palette} d{depth}: only {len(full)} cycles, the limit never binds"
        got = await detect_dfs(db_session, eq.id, depth, limit=limit)
        _assert_canonical(got)
        assert _as_result(got) == expected, f"{palette} limit {limit} depth {depth}: dfs differs from the oracle"


def _g_tie_at_cutoff():
    """Three equal-amount (5) triangles; limit 2. The one with the SMALLEST identity is found LAST.

    DFS starts are taken amount-DESC. Triangles A and B have their minimum-id edge (their start) at amount 9,
    so they are found first and fill the two places at amount 5 - the floor is now 5. Triangle C has the
    smallest minimum id of all, but its start edge carries 5, so it is reached last, when the floor is
    already 5. Its amount EQUALS the floor: a strict `amount < floor` keeps it searchable and it must
    displace the worse of A and B. A bound written `amount <= floor` prunes it and returns A and B.
    """

    g = 0x50
    a = ring(["ta1", "ta2", "ta3"], ["9", "5", "5"], [debt_uuid(g, 30), debt_uuid(g, 31), debt_uuid(g, 32)])
    b = ring(["tb1", "tb2", "tb3"], ["9", "5", "5"], [debt_uuid(g, 20), debt_uuid(g, 21), debt_uuid(g, 22)])
    c = ring(["tc1", "tc2", "tc3"], ["5", "9", "9"], [debt_uuid(g, 10), debt_uuid(g, 11), debt_uuid(g, 12)])
    return a, b, c


@pytest.mark.asyncio
async def test_a_later_equal_amount_candidate_replaces_an_earlier_one_at_the_cutoff(db_session) -> None:
    a, b, c = _g_tie_at_cutoff()
    eq = await seed_graph(db_session, "PZT", a + b + c)
    ident = {k: tuple(sorted(str(e.debt_id) for e in cyc)) for k, cyc in (("a", a), ("b", b), ("c", c))}
    # Controls: all three tie on amount; C has the smallest identity; C's start edge is the lowest-amount start.
    assert ident["c"] < ident["b"] < ident["a"]
    assert min(Decimal(e.amount) for e in c) == min(Decimal(e.amount) for e in a) == Decimal(5)

    got = await detect_dfs(db_session, eq.id, 3, limit=2)
    assert [x.identity for x in got] == [ident["c"], ident["b"]], (
        "the equal-amount triangle reached last must displace the worse of the two found first"
    )
    assert _as_result(got) == _cycle_oracle(a + b + c, _eligible_oracle(a + b + c, None), 3, 2)


@pytest.mark.asyncio
async def test_the_wrapper_keeps_the_public_find_cycles_form(db_session) -> None:
    from app.utils.exceptions import GeoException
    from scripts.p020_experimental_detectors import find_cycles_single_dfs

    edges, _ = _g_t1210_tri_quad()
    await seed_graph(db_session, "PZF", edges)
    cycles = await find_cycles_single_dfs(db_session, "PZF", 6)
    assert sorted(len(c) for c in cycles) == [3, 4]
    edge = cycles[0][0]
    assert set(edge) == {"debt_id", "debtor", "creditor", "amount"}
    assert edge["debtor"] in {"t1", "t2", "t3", "q1", "q2", "q3", "q4"} and edge["amount"] == "10.00"
    assert str(uuid.UUID(edge["debt_id"])) == edge["debt_id"]
    assert await find_cycles_single_dfs(db_session, "PZF", 6, allowed_participant_pids=set()) == []
    assert await find_cycles_single_dfs(db_session, "PZF", 6, allowed_participant_pids={"nobody"}) == []
    assert len(await find_cycles_single_dfs(db_session, "PZF", 6, allowed_participant_pids={"t1", "t2", "t3"})) == 1
    assert await find_cycles_single_dfs(db_session, "PZF", 2) == []
    with pytest.raises(GeoException):
        await find_cycles_single_dfs(db_session, "NOPE", 6)
