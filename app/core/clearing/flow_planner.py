"""Programme 023: the clearing planner - maximum total debt reduction on a snapshot (MTCS). NOT WIRED.

Slice (a) of `specs/023-clearing-as-flow/spec.md`. No production entrypoint calls this module: `/clearing/auto`,
the periodic runner and both simulator callers are switched in slice (d). It reads, it never writes.

THE PROBLEM (decision 1). On the ELIGIBLE subgraph of one equivalent - `amount > 0`; the controlling trust line
creditor -> debtor is active or frozen; consent is exactly `ClearingService._policy_flag(policy, "auto_clearing",
default=True)`; with a perimeter, BOTH endpoints are in it (`None` = no perimeter, an empty perimeter admits
nobody) - maximise `Σ_e T_e` subject to `B·T = 0`, `0 <= T_e <= L_e`, where `L_e` is the debt in integer atoms
(`amount · 10^8`, the `Numeric(20,8)` scale) and `T_e` is the reduction. Solved as the equivalent TRANSSHIPMENT
problem on `M = L - T` (what stays): minimise `Σ_e M_e` subject to `B·M = B·L`, `0 <= M_e <= L_e`, cost +1 per
unit on every eligible debt edge. `B·L` - the supplies `b_v = out_L(v) - in_L(v)` - is computed from the
eligible edges ONLY; excluded debts are not in the problem and not in any balance. (A positive-cost circulation
with zero demand would be minimised by zero and solve nothing.) `V_edge = Σ_e T_e = Σ_i |C_i|·c_i` is the
objective; `V_cyc = Σ_i c_i` is what the simulator accumulates today; both are reported, never conflated.

THE ALGORITHM (decision 2): capacity-scaling successive shortest paths with integer potentials and
binary-heap Dijkstra (Goldberg-Tarjan; the phase structure of NetworkX `capacity_scaling`).

* Residual graph: for debt edge k = (u -> v, L_k) with current `M_k`, a forward arc u -> v with residual
  `L_k - M_k` and cost +1, and a backward arc v -> u with residual `M_k` and cost -1. Reduced cost of an arc
  x -> y with cost c: `r = c + π_x - π_y`. Start: `M = 0`, `π = 0`, excess `e_v = b_v` (a node with `e_v > 0`
  still has to send `e_v` out). The only arcs with residual are the forward ones, whose `r = 1 >= 0`.
* `Δ` starts at the largest power of two `<= U`, `U = max(max_k L_k, max_v |b_v|)` (no auxiliary arcs exist),
  taken from `int.bit_length()`; each phase halves it; the last phase is `Δ = 1`.
* Every phase first RESTORES nonnegative reduced costs on the Δ-residual graph: each arc with residual `>= Δ`
  and `r < 0` is saturated (its whole residual pushed, excesses updated). Arcs with residual `< Δ` are not in
  the Δ-residual graph and may keep `r < 0` until a later, finer phase admits them - and saturates them then.
* Then, while some node has `e >= Δ` (set S) and some has `e <= -Δ` (set T): one Dijkstra over the Δ-residual
  arcs by reduced cost, multi-source from all of S (the nearest S-T pair), stopped when a T node t is
  settled; `Δ` units are pushed along the path; every settled node x gets `π_x += d_x - d_t` (the unsettled
  keep theirs). If no T node is reachable, S is cleared for this phase.
* Common divisor: when every `L_k` is a multiple of `g`, the problem is solved on `L/g` and `M` is multiplied
  back. Exact (every vertex of the scaled polytope scales back to one of the original), and it spares the
  phases below the equivalent's precision; with `g = 1` nothing changes.

WHY IT IS CORRECT - each point is also CHECKED on the output, independently of this reasoning:

1. Invariant: after a phase's restore step, and after every Dijkstra update, every arc of the Δ-residual graph
   has `r >= 0`. Restore makes it so by saturation (a saturated arc leaves the residual graph; its reverse gets
   `-r > 0`). A Dijkstra update keeps it: for settled x, y: `d_y <= d_x + r`; settled x, unsettled y: y's
   tentative distance is `<= d_x + r` and `>= d_t` (t was popped first), so `r + d_x - d_t >= 0`; unsettled x:
   the new cost is `r + (0 or d_t - d_y) >= r >= 0`. Arcs on the pushed path have `r' = 0`, so the reverse arcs
   the push creates have `r' = 0` too. Hence Dijkstra's precondition (no negative arcs) always holds.
2. Termination and feasibility: every push moves `Δ >= 1` atoms from an S node to a T node, `Σ e = 0` always,
   and `M = L` is feasible, so while some node has excess the residual graph (at `Δ = 1`: all of it) holds a
   path from it to a deficit node. At the end of `Δ = 1` every excess is zero: `B·M = B·L`, `0 <= M <= L`.
   `check_feasible` re-verifies bounds and balances from the edges alone.
3. Optimality: at the end of `Δ = 1` the Δ-residual graph IS the residual graph, so by point 1 every residual
   arc has `r >= 0` - for the forward arc: `M_k < L_k => r_k >= 0`; for the backward arc (cost -1, reduced
   `-r_k`): `M_k > 0 => r_k <= 0`, with `r_k = 1 + π_u - π_v`. These are the complementary-slackness conditions
   of the LP, so `M` is optimal. `check_certificate` verifies exactly these two conditions on every edge with
   the returned integer potentials, in `O(n + m)`, sharing no code with the solver.
4. Bound: at most `O(m)` augmentations per phase (standard for capacity scaling: after the restore step the
   total Δ-excess is `O((n + m)Δ)`), `O(log U)` phases, each augmentation one binary-heap Dijkstra
   `O((m + n) log n)`: `O(m (m + n) log n · log U)`. Weakly polynomial; no atom-by-atom augmentation.

DECOMPOSITION (decision 2). `T = L - M` is a circulation (point 2). Walk forward along edges with `T > 0`
(smallest debt UUID first at each vertex) until a vertex repeats; the closed part is a simple cycle C, its
amount `c = min_C T`; subtract; continue from the repeated vertex. Each extraction zeroes at least one edge and
no edge ever becomes positive again, so there are at most `m` cycles; the walk never sticks because every
vertex entered along `T > 0` has an outgoing `T > 0` (balance). A 2-cycle would need an opposing debt pair,
which the book never leaves (it nets opposing debt): such an input is REFUSED, not planned. `check_decomposition`
re-verifies `Σ_i c_i χ_{C_i} == T` exactly, that every cycle is simple, closed, of length >= 3, with `c_i > 0`
and `c_i <= L_e` on each of its edges.

SAFE INCREMENTAL EXECUTION (decision 3), for the later slices: `L - Σ_{executed} c_i χ_{C_i} = M + Σ_{rest}
c_i χ_{C_i}`, so on an unchanged state every remaining cycle is still executable after any subset of the plan.

WHAT IT DOES NOT DO: no lock, no write, no execution-time re-check (slice (b)); no optimality under concurrent
mutation (snapshot only); no cycle-length limit, no search budget, no fallback of any kind. An inconsistent
result is `PlanIntegrityError`, never a partial plan.
"""

from __future__ import annotations

import heapq
import json
import math
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import AbstractSet, Iterable, Sequence

from sqlalchemy import select, text

#: `debts.amount` is `Numeric(20, 8)`: one atom is 10^-8 of the unit.
ATOM_SCALE = 10**8


class PlanIntegrityError(Exception):
    """The input or a computed plan violates an invariant; no plan is returned."""


@dataclass(frozen=True)
class PlanEdge:
    """One eligible debt `debtor -> creditor` of the snapshot, its amount `L` in atoms."""

    debt_id: uuid.UUID
    debtor_id: uuid.UUID
    creditor_id: uuid.UUID
    atoms: int


@dataclass(frozen=True)
class PlannedCycle:
    """A simple directed cycle of the plan (canonical rotation: smallest debt UUID first) and its `c` in atoms."""

    edges: tuple[PlanEdge, ...]
    atoms: int


@dataclass(frozen=True)
class ClearingPlan:
    """An immutable plan on one snapshot, with its proof.

    `remaining` is `M` (atoms left per debt id), `potentials` the integer certificate `π`; `cycles` decompose
    `T = L - M`. `v_edge = Σ T = Σ |C_i| c_i`, `v_cyc = Σ c_i`.
    """

    edges: tuple[PlanEdge, ...]
    remaining: dict
    potentials: dict
    cycles: tuple[PlannedCycle, ...]
    v_edge: int
    v_cyc: int

    @property
    def longest_cycle(self) -> int:
        return max((len(c.edges) for c in self.cycles), default=0)


# ------------------------------------------------------------------------------------------------ atoms


def atoms_of(amount: Decimal) -> int:
    """`amount · 10^8` as an exact integer; anything finer than an atom, or not positive and finite, is refused."""

    value = Decimal(amount)
    if not value.is_finite():
        raise PlanIntegrityError(f"amount {amount!r} is not finite")
    scaled = value.scaleb(8)
    atoms = int(scaled)
    if Decimal(atoms) != scaled:
        raise PlanIntegrityError(f"amount {amount!r} is finer than one atom (10^-8)")
    return atoms


def money_of(atoms: int) -> Decimal:
    return Decimal(atoms).scaleb(-8)


# --------------------------------------------------------------------------------------------- the input


def _index(edges: Sequence[PlanEdge]) -> tuple[list[uuid.UUID], list[int], list[int], list[int]]:
    """Validate the input and index it: vertices sorted by canonical UUID; tails, heads, capacities."""

    seen_ids: set = set()
    pairs: set = set()
    for e in edges:
        if not isinstance(e.atoms, int) or isinstance(e.atoms, bool) or e.atoms <= 0:
            raise PlanIntegrityError(f"debt {e.debt_id}: amount must be a positive integer of atoms, got {e.atoms!r}")
        if e.debtor_id == e.creditor_id:
            raise PlanIntegrityError(f"debt {e.debt_id}: debtor and creditor coincide")
        if e.debt_id in seen_ids:
            raise PlanIntegrityError(f"debt {e.debt_id} appears twice in the snapshot")
        seen_ids.add(e.debt_id)
        pair = (e.debtor_id, e.creditor_id)
        if pair in pairs:
            raise PlanIntegrityError(f"two debts {e.debtor_id} -> {e.creditor_id} in one equivalent")
        pairs.add(pair)
    for debtor, creditor in pairs:
        if (creditor, debtor) in pairs:
            # The book nets opposing debt; an opposing pair would make a 2-cycle "clearable" by the flow.
            raise PlanIntegrityError(f"opposing debts between {debtor} and {creditor}: the book never leaves them")
    vertices = sorted({v for e in edges for v in (e.debtor_id, e.creditor_id)}, key=str)
    pos = {v: i for i, v in enumerate(vertices)}
    tails = [pos[e.debtor_id] for e in edges]
    heads = [pos[e.creditor_id] for e in edges]
    caps = [e.atoms for e in edges]
    return vertices, tails, heads, caps


def _supplies(n: int, tails: Sequence[int], heads: Sequence[int], amounts: Sequence[int]) -> list[int]:
    b = [0] * n
    for k, amount in enumerate(amounts):
        b[tails[k]] += amount
        b[heads[k]] -= amount
    return b


# ------------------------------------------------------------------------------------------------ solver


def solve_transshipment(
    n: int, tails: Sequence[int], heads: Sequence[int], caps: Sequence[int]
) -> tuple[list[int], list[int]]:
    """Capacity-scaling SSP for `min Σ M, B·M = B·L, 0 <= M <= L`. Returns `(M, π)`, integers only.

    Arc `2k` is edge k forward (tail -> head, cost +1, residual `L_k - M_k`); arc `2k + 1` is its reverse
    (head -> tail, cost -1, residual `M_k`).
    """

    m = len(caps)
    if m == 0:
        return [], [0] * n
    g = 0
    for cap in caps:
        g = math.gcd(g, cap)
    scaled = [cap // g for cap in caps]

    flow = [0] * m
    excess = _supplies(n, tails, heads, scaled)
    pi = [0] * n
    out_arcs: list[list[int]] = [[] for _ in range(n)]
    for k in range(m):
        out_arcs[tails[k]].append(2 * k)
        out_arcs[heads[k]].append(2 * k + 1)

    upper = max(max(scaled), max(abs(x) for x in excess))
    delta = 1 << (upper.bit_length() - 1)
    dist = [0] * n
    settled_stamp = [0] * n
    best_stamp = [0] * n
    pred = [-1] * n
    stamp = 0
    while delta >= 1:
        # RESTORE nonnegative reduced costs on the Δ-residual graph: saturate every arc with residual >= Δ
        # and reduced cost < 0. Forward arc of k: r = 1 + π_tail - π_head; its reverse has -r.
        for k in range(m):
            u, v = tails[k], heads[k]
            r = 1 + pi[u] - pi[v]
            if r < 0:
                amount = scaled[k] - flow[k]
                if amount >= delta:
                    flow[k] += amount
                    excess[u] -= amount
                    excess[v] += amount
            elif r > 0:
                amount = flow[k]
                if amount >= delta:
                    flow[k] -= amount
                    excess[v] -= amount
                    excess[u] += amount
        sources = {v for v in range(n) if excess[v] >= delta}
        sinks = {v for v in range(n) if excess[v] <= -delta}
        while sources and sinks:
            # One binary-heap Dijkstra by reduced cost over the Δ-residual arcs, from every source at once.
            stamp += 1
            heap: list[tuple[int, int]] = []
            for s in sources:
                dist[s] = 0
                best_stamp[s] = stamp
                pred[s] = -1
                heap.append((0, s))
            heapq.heapify(heap)
            settled: list[int] = []
            target = -1
            while heap:
                d_x, x = heapq.heappop(heap)
                if settled_stamp[x] == stamp or d_x != dist[x]:
                    continue
                settled_stamp[x] = stamp
                settled.append(x)
                if x in sinks:
                    target = x
                    break
                base = d_x + pi[x]
                for a in out_arcs[x]:
                    k = a >> 1
                    if a & 1:
                        res, y, cost = flow[k], tails[k], -1
                    else:
                        res, y, cost = scaled[k] - flow[k], heads[k], 1
                    if res < delta:  # THE SCALING THRESHOLD: only arcs of the Δ-residual graph
                        continue
                    if settled_stamp[y] == stamp:
                        continue
                    d_y = base + cost - pi[y]
                    if best_stamp[y] != stamp or d_y < dist[y]:
                        best_stamp[y] = stamp
                        dist[y] = d_y
                        pred[y] = a
                        heapq.heappush(heap, (d_y, y))
            if target < 0:
                break  # no deficit is reachable from any Δ-excess: this phase is done
            # Push Δ along the path, source <- ... <- target.
            y = target
            while pred[y] >= 0:
                a = pred[y]
                k = a >> 1
                if a & 1:
                    flow[k] -= delta
                    y = heads[k]
                else:
                    flow[k] += delta
                    y = tails[k]
            excess[y] -= delta
            excess[target] += delta
            if excess[y] < delta:
                sources.discard(y)
            if excess[target] > -delta:
                sinks.discard(target)
            d_t = dist[target]
            for x in settled:
                pi[x] += dist[x] - d_t
        delta >>= 1
    return [f * g for f in flow], pi


# ------------------------------------------------------------------------------------------------ checks


def check_feasible(edges: Sequence[PlanEdge], remaining: dict) -> None:
    """`0 <= M_e <= L_e` and `B·M = B·L`, from the edges alone. Raises `PlanIntegrityError`."""

    if set(remaining) != {e.debt_id for e in edges}:
        raise PlanIntegrityError("the flow does not cover exactly the snapshot's edges")
    net: dict = {}
    for e in edges:
        mk = remaining[e.debt_id]
        if not isinstance(mk, int) or not 0 <= mk <= e.atoms:
            raise PlanIntegrityError(f"debt {e.debt_id}: remaining {mk!r} outside [0, {e.atoms}]")
        t = e.atoms - mk
        net[e.debtor_id] = net.get(e.debtor_id, 0) + t
        net[e.creditor_id] = net.get(e.creditor_id, 0) - t
    unbalanced = [v for v, x in net.items() if x != 0]
    if unbalanced:
        raise PlanIntegrityError(f"the reduction is not a circulation at {len(unbalanced)} vertex(es)")


def check_certificate(edges: Sequence[PlanEdge], remaining: dict, potentials: dict) -> None:
    """Optimality of `M` by integer potentials: `r = 1 + π_debtor - π_creditor`; `M < L => r >= 0`; `M > 0 => r <= 0`.

    Assumes `check_feasible` passed. `O(n + m)`. Raises `PlanIntegrityError` naming the first violated edge.
    """

    for e in edges:
        pu, pv = potentials.get(e.debtor_id), potentials.get(e.creditor_id)
        if not isinstance(pu, int) or not isinstance(pv, int):
            raise PlanIntegrityError(f"debt {e.debt_id}: an endpoint has no integer potential")
        r = 1 + pu - pv
        mk = remaining[e.debt_id]
        if mk < e.atoms and r < 0:
            raise PlanIntegrityError(f"debt {e.debt_id}: residual forward arc with reduced cost {r} < 0")
        if mk > 0 and r > 0:
            raise PlanIntegrityError(f"debt {e.debt_id}: residual backward arc with reduced cost {-r} < 0")


def check_decomposition(edges: Sequence[PlanEdge], remaining: dict, cycles: Iterable[PlannedCycle]) -> None:
    """`Σ c_i χ_{C_i} == L - M` exactly; each cycle simple, closed, length >= 3, `0 < c_i <= L_e`."""

    by_id = {e.debt_id: e for e in edges}
    total: dict = {}
    for i, cycle in enumerate(cycles):
        if cycle.atoms <= 0 or len(cycle.edges) < 3:
            raise PlanIntegrityError(f"cycle {i}: amount {cycle.atoms}, length {len(cycle.edges)}")
        seen_vertices: set = set()
        for j, e in enumerate(cycle.edges):
            if by_id.get(e.debt_id) != e:
                raise PlanIntegrityError(f"cycle {i}: debt {e.debt_id} is not a snapshot edge")
            if e.creditor_id != cycle.edges[(j + 1) % len(cycle.edges)].debtor_id:
                raise PlanIntegrityError(f"cycle {i}: not a closed walk at position {j}")
            if e.debtor_id in seen_vertices:
                raise PlanIntegrityError(f"cycle {i}: vertex {e.debtor_id} repeats - not simple")
            seen_vertices.add(e.debtor_id)
            if cycle.atoms > e.atoms:
                raise PlanIntegrityError(f"cycle {i}: amount {cycle.atoms} exceeds debt {e.debt_id}")
            total[e.debt_id] = total.get(e.debt_id, 0) + cycle.atoms
    for e in edges:
        if total.get(e.debt_id, 0) != e.atoms - remaining[e.debt_id]:
            raise PlanIntegrityError(f"debt {e.debt_id}: cycles reduce {total.get(e.debt_id, 0)}, flow {e.atoms - remaining[e.debt_id]}")


# --------------------------------------------------------------------------------------------- decompose


def decompose(edges: Sequence[PlanEdge], remaining: dict) -> list[PlannedCycle]:
    """Simple cycles of `T = L - M`, at most `m` of them, deterministic by canonical debt UUID."""

    order = sorted(range(len(edges)), key=lambda k: str(edges[k].debt_id))
    t = {k: edges[k].atoms - remaining[edges[k].debt_id] for k in range(len(edges))}
    out: dict = {}
    for k in order:  # outgoing edges of each vertex, smallest debt id first
        out.setdefault(edges[k].debtor_id, []).append(k)
    cursor = {v: 0 for v in out}

    def next_edge(v) -> int:
        ks = out.get(v, ())
        i = cursor.get(v, 0)
        while i < len(ks) and t[ks[i]] == 0:
            i += 1
        if v in cursor:
            cursor[v] = i
        return ks[i] if i < len(ks) else -1

    cycles: list[PlannedCycle] = []
    for start in order:
        if t[start] == 0:
            continue
        path: list[int] = []  # edge indices of the current walk
        position = {edges[start].debtor_id: 0}  # vertex -> index in path where it starts
        v = edges[start].debtor_id
        while True:
            k = next_edge(v)
            if k < 0:
                if path:
                    raise PlanIntegrityError(f"the reduction is not a circulation: the walk sticks at {v}")
                break
            path.append(k)
            w = edges[k].creditor_id
            if w in position:
                at = position[w]
                cyc = path[at:]
                c = min(t[j] for j in cyc)
                for j in cyc:
                    t[j] -= c
                first = min(range(len(cyc)), key=lambda i: str(edges[cyc[i]].debt_id))
                cycles.append(PlannedCycle(tuple(edges[j] for j in cyc[first:] + cyc[:first]), c))
                for j in cyc:
                    del position[edges[j].creditor_id]
                del path[at:]
                position[w] = at
                v = w
                if not path and next_edge(w) < 0:
                    break
            else:
                position[w] = len(path)
                v = w
            if len(cycles) > len(edges):
                raise PlanIntegrityError("more cycles than edges: the decomposition does not terminate")
    return cycles


# -------------------------------------------------------------------------------------------------- plan


def plan_clearing(edges: Sequence[PlanEdge]) -> ClearingPlan:
    """Solve, verify feasibility, decompose, verify the decomposition, verify the optimality certificate."""

    edges = tuple(sorted(edges, key=lambda e: str(e.debt_id)))
    vertices, tails, heads, caps = _index(edges)
    flow, pi = solve_transshipment(len(vertices), tails, heads, caps)
    remaining = {e.debt_id: flow[k] for k, e in enumerate(edges)}
    potentials = {v: pi[i] for i, v in enumerate(vertices)}
    check_feasible(edges, remaining)
    cycles = decompose(edges, remaining)
    check_decomposition(edges, remaining, cycles)
    check_certificate(edges, remaining, potentials)
    v_edge = sum(e.atoms - remaining[e.debt_id] for e in edges)
    v_cyc = sum(c.atoms for c in cycles)
    if v_edge != sum(len(c.edges) * c.atoms for c in cycles):
        raise PlanIntegrityError("V_edge differs from Σ|C_i|·c_i")
    return ClearingPlan(edges, remaining, potentials, tuple(cycles), v_edge, v_cyc)


# ------------------------------------------------------------------------------------------------ snapshot


_ELIGIBLE_EDGES_SQL = """
    SELECT d.id, d.debtor_id, d.creditor_id, d.amount, t.policy::text AS policy
    FROM debts d
    JOIN trust_lines t ON t.from_participant_id = d.creditor_id
                      AND t.to_participant_id = d.debtor_id
                      AND t.equivalent_id = d.equivalent_id
                      AND t.status = ANY(CAST(:statuses AS text[]))
    WHERE d.equivalent_id = :equivalent_id
      AND d.amount > 0
"""
_SCOPE_SQL = """
      AND d.debtor_id = ANY(CAST(:scope AS uuid[]))
      AND d.creditor_id = ANY(CAST(:scope AS uuid[]))
"""


async def load_snapshot(
    session, equivalent_code: str, *, allowed_participant_pids: AbstractSet[str] | None = None
) -> list[PlanEdge]:
    """The eligible subgraph of one equivalent, read in one statement (plus the equivalent and the perimeter).

    Consent is decided by the production parser itself, `ClearingService._policy_flag`, on the stored JSON, so
    admission cannot differ from execution's by construction (020 review P2-2). Statuses are the production
    constant. A missing equivalent raises `GeoException`, as `find_cycles` does.
    """

    from app.core.clearing.service import _CLEARABLE_TRUSTLINE_STATUSES, ClearingService
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.utils.exceptions import GeoException

    equivalent_id = (
        await session.execute(select(Equivalent.id).where(Equivalent.code == equivalent_code))
    ).scalar_one_or_none()
    if equivalent_id is None:
        raise GeoException(f"Equivalent {equivalent_code} not found")
    params: dict = {"equivalent_id": equivalent_id, "statuses": list(_CLEARABLE_TRUSTLINE_STATUSES)}
    sql = _ELIGIBLE_EDGES_SQL
    if allowed_participant_pids is not None:
        if not allowed_participant_pids:
            return []
        scope = (
            await session.execute(
                select(Participant.id).where(Participant.pid.in_(sorted(allowed_participant_pids)))
            )
        ).scalars().all()
        if not scope:
            return []
        params["scope"] = sorted(scope, key=str)
        sql += _SCOPE_SQL
    rows = await session.execute(text(sql), params)
    edges = []
    for r in rows:
        policy = None if r.policy is None else json.loads(r.policy)
        if ClearingService._policy_flag(policy, "auto_clearing", default=True):
            edges.append(PlanEdge(r.id, r.debtor_id, r.creditor_id, atoms_of(Decimal(r.amount))))
    return edges


async def plan_for_equivalent(
    session, equivalent_code: str, *, allowed_participant_pids: AbstractSet[str] | None = None
) -> ClearingPlan:
    """Snapshot read + `plan_clearing`. The caller owns the transaction; nothing is written."""

    edges = await load_snapshot(session, equivalent_code, allowed_participant_pids=allowed_participant_pids)
    return plan_clearing(edges)
