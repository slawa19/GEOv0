"""Programme 020, stage 2: the two EXPERIMENTAL cycle detectors. NOT on the production path.

Nothing under `app/` imports this module, and `tests/unit/test_p020_experimental_detectors_are_not_imported_
by_production.py` keeps it that way. It exists so the stage-2 measurement (`scripts/measure_p020_detector_
cost.py`) and the contract tests (`tests/integration/test_p020_experimental_detectors_postgres.py`) can run
the candidate the spec chose (a recursive CTE) and its mandatory comparator (one DFS over one SQL-filtered
graph) side by side. Stage 3 (`T2003`) moves the winner into `app/core/clearing/service.py`; until then no
caller of `find_cycles` sees either.

THE CONTRACT BOTH IMPLEMENT (spec "Решения" -> detector and selection rule):

* one ELIGIBILITY RELATION over edges, `ELIGIBLE_EDGES_SQL` below - one equivalent; `amount > 0`; the
  controlling trust line is creditor -> debtor (`from = creditor`, `to = debtor`); its status is active or
  frozen; its `policy.auto_clearing` is effective consent with EXACTLY the semantics of
  `ClearingService._policy_flag(..., default=True)` (the SQL may not widen consent: a JSON number is `<> 0`,
  a string is trimmed and lowered against `false/0/no/off`, an array or object is its non-emptiness, a
  missing key / JSON null / SQL NULL policy consents); with a perimeter, BOTH endpoints of every edge are in
  it (an empty perimeter admits nobody). Both detectors read this one relation - the DFS loads it, the CTE
  recurses over it - so admission cannot differ between them by construction;
* SIMPLE directed cycles of 3..`max_depth` edges (no repeated vertex; 2-cycles are not cycles here);
* CANONICAL ROTATION - a cycle starts at its smallest debt UUID; enumeration only ever extends a path with
  edges whose id is greater than the start edge's, so each cycle is produced exactly once (debts are unique
  per ordered pair, so an edge set has one traversal);
* ORDER - clear amount (the smallest edge) DESC, then the FULL canonical identity: the sorted tuple of every
  debt UUID of the cycle, ascending; the LIMIT applies after this order, to unique cycles.

WHAT THEY DO NOT DO: render pids or money strings (`render_for_find_cycles` does that for execution), apply
the execution-time re-check, or take any lock. They are read-only.

THE DFS IS BOUNDED, AND THAT IS NOT A DIFFERENT CONTRACT. The comparator walks start edges in amount-DESC
order and prunes a path whose running minimum is already below the 100th-best amount found so far (equal
amounts are kept, because the identity may still win the tie). A cycle's amount is the minimum over its edges,
so no pruned path can close into a cycle that belongs in the answer: the output is the same top-`limit` the
exhaustive enumeration yields. `bounded=False` switches the bound off, for measurement only.
"""

from __future__ import annotations

import bisect
import json
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import AbstractSet, Sequence

from sqlalchemy import text

CLEARABLE_STATUSES = ("active", "frozen")
DEFAULT_LIMIT = 100


class DetectorTimeout(Exception):
    """The detector exceeded its deadline. A measurement counts it as a failure, never as empty."""


@dataclass(frozen=True)
class Cycle:
    amount: Decimal
    # (debt_id, debtor_id, creditor_id, amount) per edge, in canonical rotation (min debt id first).
    edges: tuple[tuple[uuid.UUID, uuid.UUID, uuid.UUID, Decimal], ...]

    @property
    def identity(self) -> tuple[str, ...]:
        return tuple(sorted(str(e[0]) for e in self.edges))


def _consent_sql(policy: str) -> str:
    """`ClearingService._policy_flag(policy, "auto_clearing", default=True)` as a jsonb predicate."""

    v = f"({policy} -> 'auto_clearing')"
    s = f"({policy} ->> 'auto_clearing')"
    return (
        f"(CASE jsonb_typeof({v}) "
        f"WHEN 'boolean' THEN {s}::boolean "
        f"WHEN 'number' THEN {s}::numeric <> 0 "
        f"WHEN 'string' THEN lower(btrim({s}, E' \\t\\n\\r\\f\\v')) NOT IN ('false', '0', 'no', 'off') "
        f"WHEN 'array' THEN jsonb_array_length({v}) > 0 "
        f"WHEN 'object' THEN {v} <> '{{}}'::jsonb "
        f"ELSE true END)"
    )


def eligible_edges_sql(*, scoped: bool, consent_in_sql: bool = True) -> str:
    """The eligibility relation. `scoped` adds the both-endpoints perimeter predicate.

    `consent_in_sql=False` (the DFS path, and the stage-3 contract) leaves consent OUT of the SQL and returns
    the raw `policy` as text; `load_eligible_edges` then applies the production parser to every row BEFORE
    anything is enumerated or limited. `consent_in_sql=True` is kept only for the rejected CTE, so that the
    recorded stage-2 measurement stays reproducible; its SQL predicate carries the review's P2-2 gap
    (ASCII-only trim) and is NOT maintained.
    """

    statuses = ", ".join(f"'{s}'" for s in CLEARABLE_STATUSES)
    scope = (
        "AND d.debtor_id = ANY(CAST(:scope AS uuid[])) AND d.creditor_id = ANY(CAST(:scope AS uuid[]))"
        if scoped
        else ""
    )
    consent = f"AND {_consent_sql('t.policy')}" if consent_in_sql else ""
    policy = "" if consent_in_sql else ", t.policy::text AS policy"
    return f"""
        SELECT d.id, d.debtor_id AS src, d.creditor_id AS dst, d.amount{policy}
        FROM debts d
        JOIN trust_lines t ON t.from_participant_id = d.creditor_id
                          AND t.to_participant_id = d.debtor_id
                          AND t.equivalent_id = d.equivalent_id
                          AND t.status IN ({statuses})
        WHERE d.equivalent_id = :equivalent_id
          AND d.amount > 0
          {consent}
          {scope}
    """


def cte_sql(*, scoped: bool) -> str:
    """The recursive CTE: simple cycles, canonical start, amount DESC + full identity, LIMIT after."""

    return f"""
WITH RECURSIVE e AS MATERIALIZED ({eligible_edges_sql(scoped=scoped)}),
walk (start_id, start_node, node, path, nodes, amount, len, closed) AS (
    SELECT e.id, e.src, e.dst, ARRAY[e.id], ARRAY[e.src, e.dst], e.amount, 1, false
    FROM e
    UNION ALL
    SELECT w.start_id, w.start_node, n.dst, w.path || n.id, w.nodes || n.dst,
           LEAST(w.amount, n.amount), w.len + 1, n.dst = w.start_node
    FROM walk w
    JOIN e n ON n.src = w.node
    WHERE NOT w.closed
      AND n.id > w.start_id
      AND (
            (n.dst = w.start_node AND w.len + 1 >= 3)
         OR (n.dst <> ALL (w.nodes) AND w.len + 1 < :max_depth)
      )
)
SELECT path, amount,
       ARRAY(SELECT x FROM unnest(path) AS x ORDER BY x) AS identity
FROM walk
WHERE closed
ORDER BY amount DESC, identity
LIMIT :limit
"""


def _params(equivalent_id, max_depth: int, scope_ids, limit: int | None = None) -> dict:
    params: dict = {"equivalent_id": equivalent_id, "max_depth": int(max_depth)}
    if limit is not None:
        params["limit"] = int(limit)
    if scope_ids is not None:
        params["scope"] = sorted(scope_ids)
    return params


def _check_depth(max_depth: int) -> None:
    if not 3 <= int(max_depth) <= 10:
        raise ValueError(f"max_depth must be 3..10, got {max_depth}")


async def load_eligible_edges(session, equivalent_id, *, scope_ids=None):
    """The DFS's one query: the eligibility relation, as rows."""

    if scope_ids is not None and not scope_ids:
        return []
    rows = await session.execute(
        text(eligible_edges_sql(scoped=scope_ids is not None, consent_in_sql=False)),
        _params(equivalent_id, 3, scope_ids),
    )
    # CONSENT IS DECIDED BY THE PRODUCTION PARSER ITSELF (review P2-2, 2026-09-25). A SQL re-statement of
    # `_policy_flag` has to agree with Python on `str.strip()`'s Unicode whitespace set, on `lower()` and on
    # JSON number truthiness; the ASCII trim it had admitted " false ". Parsing the stored JSON
    # here and asking `_policy_flag` makes the parity hold by construction, and it happens before any
    # enumeration or limit, so a refused line never takes a place in the top 100.
    return [
        (r.id, r.src, r.dst, Decimal(r.amount))
        for r in rows
        if _consents(None if r.policy is None else json.loads(r.policy))
    ]


def _consents(policy) -> bool:
    from app.core.clearing.service import ClearingService

    return ClearingService._policy_flag(policy, "auto_clearing", default=True)


async def detect_cte(
    session,
    equivalent_id: uuid.UUID,
    max_depth: int,
    *,
    scope_ids: AbstractSet[uuid.UUID] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Cycle]:
    _check_depth(max_depth)
    if scope_ids is not None and not scope_ids:
        return []
    result = await session.execute(
        text(cte_sql(scoped=scope_ids is not None)), _params(equivalent_id, max_depth, scope_ids, limit)
    )
    rows = result.all()
    if not rows:
        return []
    edge_ids = {i for r in rows for i in r.path}
    edges = {}
    edge_rows = await session.execute(
        text("SELECT id, debtor_id, creditor_id, amount FROM debts WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": sorted(edge_ids)},
    )
    for r in edge_rows:
        edges[r.id] = (r.id, r.debtor_id, r.creditor_id, Decimal(r.amount))
    return [Cycle(Decimal(r.amount), tuple(edges[i] for i in r.path)) for r in rows]


def detect_dfs_in_memory(
    edge_rows: Sequence[tuple],
    max_depth: int,
    *,
    limit: int = DEFAULT_LIMIT,
    bounded: bool = True,
    rank_bound: bool = True,
    deadline: float | None = None,
) -> list[Cycle]:
    """The DFS over an already filtered edge list. Pure Python, no I/O.

    `bounded=True, rank_bound=True` (the default since 2026-09-26) is the single experiment the consultation
    after the supplement authorised: branch-and-bound on the COMPLETE ranking key (`_detect_rank_bound`).
    `rank_bound=False` is the amount-only bound measured in Verification plan 7/7a, kept so that evidence
    stays reproducible; `bounded=False` is the unbounded enumeration (measurement only).
    """

    _check_depth(max_depth)
    if bounded and rank_bound:
        return _detect_rank_bound(edge_rows, max_depth, limit=limit, deadline=deadline)
    by_src: dict = {}
    for row in edge_rows:
        by_src.setdefault(row[1], []).append(row)
    for out in by_src.values():
        out.sort(key=lambda r: (-r[3], r[0]))

    best: list[tuple] = []  # sorted by (-amount, identity); each item (key, path)
    ticks = 0

    def worst_amount():
        return -best[-1][0][0] if len(best) >= limit else None

    starts = sorted(edge_rows, key=lambda r: (-r[3], r[0]))
    for start in starts:
        floor = worst_amount() if bounded else None
        if floor is not None and start[3] < floor:
            break
        start_id, start_node = start[0], start[1]
        path = [start]
        on_path = {start[1], start[2]}

        def extend(node, running: Decimal) -> None:
            nonlocal ticks
            ticks += 1
            if deadline is not None and ticks % 4096 == 0 and time.monotonic() > deadline:
                raise DetectorTimeout()
            for nxt in by_src.get(node, ()):
                if nxt[0] <= start_id:
                    continue
                amount = min(running, nxt[3])
                floor = worst_amount() if bounded else None
                if floor is not None and amount < floor:
                    continue  # adjacency is amount-DESC, but ids filter it, so keep scanning
                if nxt[2] == start_node:
                    if len(path) + 1 >= 3:
                        cycle = path + [nxt]
                        key = (-amount, tuple(sorted(str(e[0]) for e in cycle)))
                        if len(best) < limit or key < best[-1][0]:
                            bisect.insort(best, (key, tuple(cycle)))
                            if len(best) > limit:
                                best.pop()
                    continue
                if nxt[2] in on_path or len(path) + 1 >= max_depth:
                    continue
                path.append(nxt)
                on_path.add(nxt[2])
                extend(nxt[2], amount)
                on_path.discard(nxt[2])
                path.pop()

        extend(start[2], start[3])

    out = []
    for (neg_amount, _identity), cycle in best:
        # canonical rotation: the start edge is the smallest id by construction
        out.append(Cycle(-neg_amount, tuple((e[0], e[1], e[2], e[3]) for e in cycle)))
    return out


def _detect_rank_bound(
    edge_rows: Sequence[tuple], max_depth: int, *, limit: int, deadline: float | None
) -> list[Cycle]:
    """Canonical-root DFS with branch-and-bound on the complete ranking key (consultation 2026-09-26).

    THE KEY. A cycle's key is `(-amount, identity)`, identity = the sorted tuple of all its debt ids as
    canonical strings; smaller is better; the answer is the `limit` smallest keys. Keys of distinct cycles
    differ (distinct edge sets have distinct identities). While fewer than `limit` cycles are retained,
    nothing is pruned. Once `limit` are retained, the worst retained key is `W = (-A, T)`; a new cycle
    matters only if its key is < W. W only ever decreases, so a branch that cannot beat W now can never
    beat a later W either: pruning is never undone.

    THE ROOT. Every cycle is enumerated once, from its smallest debt id `r` (its root); every other edge has
    an id > r. So `identity[0] == str(r)` for every cycle under root r.

    THE BRANCH. A branch is a simple path P (root first, k edges) ending at a vertex v other than the root's
    start; `U` = the smallest amount on P. Every completion C (the edges that close P into a cycle) has
    `amount(P + C) <= U`. Three prune rules, each applied only when `limit` cycles are retained:

    1. `U < A`: every completion has amount < A, so key > W. (The pre-existing amount bound; strict `<`.)
    2. `U == A` and `r > T[0]` (string order): a completion has amount <= A; if < A it loses on amount; if
       == A, its identity starts with r > T[0], so identity > T. Either way key > W.
    3. `U == A` and `r == T[0]`: a completion wins only with amount == A and identity < T. Let S be a
       SUPERSET of the edges that can appear in a completion: id > r, not on P, amount >= A (a lower edge
       would make the cycle amount < A), source not a vertex of P other than v, destination not a vertex of
       P other than the start, and destination able to reach the start within the remaining hops over edges
       with id > r (a reverse-BFS distance computed once per root, lazily; ignoring whether the edge is
       reachable from v keeps S a superset). A completion of m edges has identity `sorted(P + X)` with X a
       subset of S of size m, m from max(1, 3 - k) to max_depth - k. For a fixed m, X = the m smallest ids
       of S gives the lexicographically smallest such tuple (replacing a member by a smaller non-member
       never raises a sorted tuple, element by element). The minimum of those tuples over the permitted m
       is a lower bound LB on the identity of every completion; if S holds fewer ids than the smallest m, no
       completion exists. Prune when `not LB < T`: every completion's identity is then >= T, and == T is the
       already retained cycle, which a path not yet explored cannot produce again.
    (`r < T[0]` with `U == A`: identity[0] < T[0], a completion can win - nothing is pruned.)

    Rule 2 also cuts at the root: roots are taken in `(-root amount, id)` order, and a root's own amount
    bounds its cycles, so once a root has amount < A, or amount == A and id > T[0], every later root is
    below A or has a larger id with amount <= A - the loop stops.

    NOT done, on purpose (the consultation forbids it): no "sort the adjacency and stop after the first
    `limit` finds" (traversal order is not identity order), and the amount bound stays strict `<`
    (equal-amount contenders keep being searched; `test_a_later_equal_amount_candidate_replaces_...`).
    Exactness says nothing about time: whether this meets the frozen thresholds is a measurement.
    """

    rows = list(edge_rows)
    by_src: dict = {}
    for row in rows:
        by_src.setdefault(row[1], []).append(row)
    for out in by_src.values():
        out.sort(key=lambda e: (-e[3], str(e[0])))  # amount DESC, then small ids first: good keys early
    by_id = sorted(rows, key=lambda e: str(e[0]))
    ids_sorted = [str(e[0]) for e in by_id]

    best: list[tuple] = []  # sorted list of (key, cycle)
    ticks = 0

    def cutoff():
        return best[-1][0] if len(best) >= limit else None

    starts = sorted(rows, key=lambda e: (-e[3], str(e[0])))
    for start in starts:
        w = cutoff()
        root = str(start[0])
        if w is not None:
            a0, t0 = -w[0], w[1]
            if start[3] < a0 or (start[3] == a0 and root > t0[0]):
                break  # rules 1 and 2 at the root; every later root is no better (see docstring)
        start_id, start_node = start[0], start[1]
        path = [start]
        path_ids = [root]
        on_path_set = {start[1], start[2]}
        dist_cache: dict = {}

        def dist_to_start(root=root, start_node=start_node, dist_cache=dist_cache):
            if "d" not in dist_cache:
                rev: dict = {}
                for e in rows:
                    if str(e[0]) > root:
                        rev.setdefault(e[2], []).append(e[1])
                dist = {start_node: 0}
                frontier = [start_node]
                hop = 0
                while frontier and hop < max_depth - 1:
                    hop += 1
                    nxt_frontier = []
                    for node in frontier:
                        for prev in rev.get(node, ()):
                            if prev not in dist:
                                dist[prev] = hop
                                nxt_frontier.append(prev)
                    frontier = nxt_frontier
                dist_cache["d"] = dist
            return dist_cache["d"]

        def identity_bound_prunes(v, a, t, k_after, ids_after, root=root, start_node=start_node,
                                  on_path_set=on_path_set, dist_to_start=dist_to_start) -> bool:
            """Rule 3 for the branch whose path has `k_after` edges and ends at `v` (v != start)."""
            m_lo, m_hi = max(1, 3 - k_after), max_depth - k_after
            if m_hi < m_lo:
                return True
            dist = dist_to_start()
            path_id_set = set(ids_after)
            taken: list[str] = []
            pos = bisect.bisect_right(ids_sorted, root)
            while pos < len(by_id) and len(taken) < m_hi:
                e, eid = by_id[pos], ids_sorted[pos]
                pos += 1
                if eid in path_id_set or e[3] < a:
                    continue
                if e[1] in on_path_set and e[1] != v:
                    continue
                if e[2] in on_path_set and e[2] != start_node:
                    continue
                d = dist.get(e[2])
                if d is None or d > m_hi - 1:
                    continue
                taken.append(eid)
            if len(taken) < m_lo:
                return True  # no completion of any permitted length can exist
            lb = min(tuple(sorted(ids_after + taken[:m])) for m in range(m_lo, len(taken) + 1))
            return not lb < t

        def extend(node, running: Decimal, root=root, start_id=start_id, start_node=start_node,
                   path=path, path_ids=path_ids, on_path_set=on_path_set,
                   identity_bound_prunes=identity_bound_prunes) -> None:
            nonlocal ticks
            ticks += 1
            if deadline is not None and ticks % 4096 == 0 and time.monotonic() > deadline:
                raise DetectorTimeout()
            for nxt in by_src.get(node, ()):
                if nxt[0] <= start_id:
                    continue
                amount = min(running, nxt[3])
                w = cutoff()
                a = t = None
                if w is not None:
                    a, t = -w[0], w[1]
                    if amount < a:
                        continue  # rule 1 (strict)
                    if amount == a and root > t[0]:
                        continue  # rule 2
                if nxt[2] == start_node:
                    if len(path) + 1 >= 3:
                        key = (-amount, tuple(sorted(path_ids + [str(nxt[0])])))
                        if len(best) < limit or key < best[-1][0]:
                            bisect.insort(best, (key, tuple(path + [nxt])))
                            if len(best) > limit:
                                best.pop()
                    continue
                if nxt[2] in on_path_set or len(path) + 1 >= max_depth:
                    continue
                if w is not None and amount == a and root == t[0]:
                    on_path_set.add(nxt[2])
                    try:
                        pruned = identity_bound_prunes(nxt[2], a, t, len(path) + 1, path_ids + [str(nxt[0])])
                    finally:
                        on_path_set.discard(nxt[2])
                    if pruned:
                        continue  # rule 3
                path.append(nxt)
                path_ids.append(str(nxt[0]))
                on_path_set.add(nxt[2])
                extend(nxt[2], amount)
                on_path_set.discard(nxt[2])
                path_ids.pop()
                path.pop()

        extend(start[2], start[3])

    return [
        Cycle(-neg_amount, tuple((e[0], e[1], e[2], e[3]) for e in cycle))
        for (neg_amount, _identity), cycle in best
    ]


async def detect_dfs(
    session,
    equivalent_id: uuid.UUID,
    max_depth: int,
    *,
    scope_ids: AbstractSet[uuid.UUID] | None = None,
    limit: int = DEFAULT_LIMIT,
    bounded: bool = True,
    deadline: float | None = None,
) -> list[Cycle]:
    _check_depth(max_depth)
    rows = await load_eligible_edges(session, equivalent_id, scope_ids=scope_ids)
    return detect_dfs_in_memory(rows, max_depth, limit=limit, bounded=bounded, deadline=deadline)


async def render_for_find_cycles(session, cycles: Sequence[Cycle], *, precision: int) -> list[list[dict]]:
    """The `find_cycles` wire form (pids, canonical UUID strings, money strings) for execution."""

    from app.utils.money import to_money_str

    ids = sorted({p for c in cycles for e in c.edges for p in (e[1], e[2])})
    pid_by_id = {}
    if ids:
        rows = await session.execute(
            text("SELECT id, pid FROM participants WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids}
        )
        pid_by_id = {r.id: r.pid for r in rows}
    return [
        [
            {
                "debt_id": str(e[0]),
                "debtor": str(pid_by_id.get(e[1], e[1])),
                "creditor": str(pid_by_id.get(e[2], e[2])),
                "amount": to_money_str(e[3], precision),
            }
            for e in c.edges
        ]
        for c in cycles
    ]


# ===================================================================== the stage-3 wrapper, as it will be called


async def find_cycles_single_dfs(
    session,
    equivalent_code: str,
    max_depth: int = 6,
    *,
    allowed_participant_pids: AbstractSet[str] | None = None,
) -> list[list[dict]]:
    """`ClearingService.find_cycles` rebuilt on the single DFS: the complete public call stage 3 will make.

    What it adds over `detect_dfs` is exactly the work the public method does around the detector, so the
    measurement times the replacement rather than the bare function (review P2-1): the equivalent is read by
    code (a missing one raises `GeoException`, as today); the perimeter keeps its three states (`None` - not
    applied; empty - nobody; pids that resolve to nobody - nobody); the answer is rendered in the wire form
    (pids, canonical UUID strings, money at the equivalent's precision). A depth below 3 has no cycle to
    return (the simulator's ladder may ask for `min(depth, 4)` with a configured depth of 1 or 2).
    No fallback and no partial answer: a failure of any step raises.
    """

    from app.utils.exceptions import GeoException

    row = (
        await session.execute(
            text("SELECT id, precision FROM equivalents WHERE code = :code"), {"code": equivalent_code}
        )
    ).first()
    if row is None:
        raise GeoException(f"Equivalent {equivalent_code} not found")
    equivalent_id, precision = row.id, int(row.precision)
    if int(max_depth) < 3:
        return []
    scope_ids = None
    if allowed_participant_pids is not None:
        if not allowed_participant_pids:
            return []
        scope_ids = {
            r.id
            for r in await session.execute(
                text("SELECT id FROM participants WHERE pid = ANY(CAST(:pids AS text[]))"),
                {"pids": sorted(allowed_participant_pids)},
            )
        }
        if not scope_ids:
            return []
    cycles = await detect_dfs(session, equivalent_id, int(max_depth), scope_ids=scope_ids, limit=DEFAULT_LIMIT)
    return await render_for_find_cycles(session, cycles, precision=precision)


def single_dfs_service_class():
    """A `ClearingService` whose detection and `auto_clear` follow the stage-3 contract.

    `find_cycles` is `find_cycles_single_dfs`; `auto_clear` asks the FULL requested depth on every detection,
    tries candidates in order until the first success (through the production `_auto_clear_try_candidates`,
    so `execute_clearing` and the error envelope are today's), drops the list and detects again; the
    101-success ceiling is kept. Execution is untouched production code. Built lazily so that importing this
    module does not import the application.
    """

    from app.core.clearing.service import ClearingService

    class SingleDfsClearingService(ClearingService):
        async def find_cycles(self, equivalent_code, max_depth=6, *, allowed_participant_pids=None):
            return await find_cycles_single_dfs(
                self.session, equivalent_code, max_depth, allowed_participant_pids=allowed_participant_pids
            )

        async def auto_clear(self, equivalent_code, *, max_depth=6):
            count = 0
            while True:
                cycles = await self._auto_clear_find(equivalent_code, max_depth, count)
                if not cycles:
                    return count
                if not await self._auto_clear_try_candidates(cycles, equivalent_code, count):
                    return count
                count += 1
                if count > 100:
                    return count

    return SingleDfsClearingService
