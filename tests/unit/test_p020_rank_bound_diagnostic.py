"""DIAGNOSTIC (programme 020 stage 2, consultation 2026-09-26): the rank-bound DFS against a brute-force oracle.

Pure Python, no database: `detect_dfs_in_memory` (branch-and-bound on the complete ranking key,
`scripts/p020_experimental_detectors.py::_detect_rank_bound`) on in-memory edge lists, compared with an
oracle that shares none of its ideas - every simple path from every vertex, cycles de-duplicated by edge SET
(no canonical root), ranked by (amount DESC, sorted id tuple ASC), cut at the limit.

What varies, because the bound depends on it: the UUID assignment (random, ascending along the graph,
descending), the insertion order of the edge list, the amount distribution (all equal, two values, a
plateau, distinct), fewer cycles than the limit, limits 1 / 5 / 17 / 100, depths 3..10, and a family of
cycles that all share the SAME minimum debt id (the root rule 3 has to separate them by later ids).

This is a correctness diagnostic, not acceptance: the acceptance of the refinement is the frozen 160-cell
matrix (`scripts/measure_p020_dfs_acceptance.py`) and the PostgreSQL oracle module
`tests/integration/test_p020_experimental_detectors_postgres.py`.
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest

from scripts.p020_experimental_detectors import detect_dfs_in_memory


def _oracle(rows, max_depth, limit):
    out_of: dict = {}
    for r in rows:
        out_of.setdefault(r[1], []).append(r)
    found: dict = {}

    def walk(start, node, path, seen):
        for e in out_of.get(node, ()):
            if e[2] == start:
                cyc = path + [e]
                if 3 <= len(cyc) <= max_depth:
                    found.setdefault(frozenset(x[0] for x in cyc), cyc)
                continue
            if e[2] in seen or len(path) + 1 >= max_depth:
                continue
            walk(start, e[2], path + [e], seen | {e[2]})

    for v in list(out_of):
        walk(v, v, [], {v})
    ranked = sorted(
        (-min(x[3] for x in cyc), tuple(sorted(str(x[0]) for x in cyc))) for cyc in found.values()
    )
    return [(-a, ident) for a, ident in ranked[:limit]], len(found)


def _graph(seed: int, n: int, m: int, amounts: str, ids: str, order: str):
    rnd = random.Random(seed)
    nodes = [uuid.UUID(int=rnd.getrandbits(128)) for _ in range(n)]
    pairs, rows = set(), []
    while len(rows) < m:
        a, b = rnd.choice(nodes), rnd.choice(nodes)
        if a == b or (a, b) in pairs or (b, a) in pairs:
            continue
        pairs.add((a, b))
        if amounts == "equal":
            amt = Decimal(10)
        elif amounts == "two":
            amt = Decimal(rnd.choice([10, 11]))
        elif amounts == "plateau":
            amt = Decimal(50) if rnd.random() < 0.6 else Decimal(rnd.randrange(1, 100))
        else:
            amt = Decimal(rnd.randrange(1, 10_000)) / 100
        rows.append([None, a, b, amt])
    if ids == "random":
        for r in rows:
            r[0] = uuid.UUID(int=rnd.getrandbits(128))
    elif ids == "ascending":
        for k, r in enumerate(rows):
            r[0] = uuid.UUID(int=(1 << 100) + k)
    else:  # descending along the generation order
        for k, r in enumerate(rows):
            r[0] = uuid.UUID(int=(1 << 100) + (len(rows) - k))
    rows = [tuple(r) for r in rows]
    if order == "shuffled":
        rnd.shuffle(rows)
    elif order == "reversed":
        rows.reverse()
    return rows


def _shared_min_root(seed: int):
    """Many equal-amount cycles through ONE edge with the globally smallest id: they share their root.

    a -> b is the root (id 1). b feeds six middle vertices, each middle vertex returns to a, and middle
    vertices link forward (i -> j for i < j): every cycle is a -> b -> (increasing chain of middles) -> a,
    63 of them up to length 8. A second component (a ring with chords, larger ids) competes for places.
    """

    rnd = random.Random(seed)
    a, b = uuid.UUID(int=rnd.getrandbits(128)), uuid.UUID(int=rnd.getrandbits(128))
    mids = [uuid.UUID(int=rnd.getrandbits(128)) for _ in range(6)]

    def rid():
        return uuid.UUID(int=rnd.getrandbits(120) + 2)

    rows = [(uuid.UUID(int=1), a, b, Decimal(10))]
    rows += [(rid(), b, m, Decimal(10)) for m in mids]
    rows += [(rid(), m, a, Decimal(10)) for m in mids]
    rows += [(rid(), mids[i], mids[j], Decimal(10)) for i in range(6) for j in range(i + 1, 6)]
    ring = [uuid.UUID(int=rnd.getrandbits(128)) for _ in range(6)]
    rows += [(rid(), ring[k], ring[(k + 1) % 6], Decimal(10)) for k in range(6)]
    rows += [(rid(), ring[k], ring[(k + 3) % 6], Decimal(10)) for k in range(3)]
    rnd.shuffle(rows)
    return rows


def _result(cycles):
    return [(c.amount, c.identity) for c in cycles]


_CASES = [
    (seed, amounts, ids, order)
    for seed, (amounts, ids, order) in enumerate(
        [
            ("equal", "random", "as_built"),
            ("equal", "ascending", "shuffled"),
            ("equal", "descending", "reversed"),
            ("two", "random", "shuffled"),
            ("two", "descending", "as_built"),
            ("plateau", "random", "reversed"),
            ("plateau", "ascending", "as_built"),
            ("distinct", "random", "shuffled"),
            ("distinct", "descending", "shuffled"),
        ]
    )
]


@pytest.mark.parametrize("seed,amounts,ids,order", _CASES)
@pytest.mark.parametrize("limit", [1, 5, 17, 100])
def test_diagnostic_rank_bound_matches_the_oracle(seed, amounts, ids, order, limit) -> None:
    rows = _graph(seed, n=9, m=28, amounts=amounts, ids=ids, order=order)
    for depth in (3, 4, 6, 7, 10):
        expected, total = _oracle(rows, depth, limit)
        got = _result(detect_dfs_in_memory(rows, depth, limit=limit))
        assert got == expected, f"{amounts}/{ids}/{order} limit {limit} depth {depth}: differs from the oracle"
    # Anti-vacuum: at depth 10 the limit binds for the small limits, and not at all for 100 on some graphs.
    assert total > 17


def test_diagnostic_fewer_cycles_than_the_limit() -> None:
    rows = _graph(99, n=7, m=9, amounts="equal", ids="random", order="shuffled")
    for depth in (3, 6, 10):
        expected, total = _oracle(rows, depth, 100)
        assert total < 100
        assert _result(detect_dfs_in_memory(rows, depth, limit=100)) == expected


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("limit", [1, 3, 9])
def test_diagnostic_ties_sharing_the_minimum_uuid(seed, limit) -> None:
    rows = _shared_min_root(seed)
    for depth in (4, 6, 10):
        expected, total = _oracle(rows, depth, limit)
        got = _result(detect_dfs_in_memory(rows, depth, limit=limit))
        assert got == expected, f"seed {seed} limit {limit} depth {depth}"
    # Anti-vacuum: at depth 10 more cycles than the limit share the root and the amount - the tie that only
    # the later ids of the identity (rule 3) can order.
    everything, _ = _oracle(rows, 10, 10**6)
    shared = [ident for amount, ident in everything if ident[0] == str(uuid.UUID(int=1))]
    assert len(shared) > limit, f"only {len(shared)} cycles share the minimum id"
