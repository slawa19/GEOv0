import asyncio
import itertools
import logging
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple, Iterable
from uuid import UUID

from app.utils.observability import log_duration

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.payments.capacity import pair_capacity, pair_rules, pending_pair_capacity, route_breaks_policy
from app.db.models.trustline import TrustLine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.schemas.payment import CapacityResponse, MaxFlowResponse, MaxFlowPath
from app.config import settings
from app.utils.metrics import ROUTING_FAILURES_TOTAL
from app.utils.validation import floor_to_step, money_step, validate_equivalent_code
from app.utils.exceptions import BadRequestException, TimeoutException

logger = logging.getLogger(__name__)

class PaymentRouter:
    _graph_cache: Dict[
        str,
        Tuple[
            float,
            Dict[str, Dict[str, Decimal]],
            Dict[str, Dict[str, bool]],
            Dict[str, Dict[str, Set[str]]],
            Dict[UUID, str],
            Dict[str, UUID],
        ],
    ] = {}

    # Lightweight, trustline-only topology cache used to distinguish NO_ROUTE vs INSUFFICIENT_CAPACITY.
    # Keyed by equivalent_code; invalidated via invalidate_cache() on trustline CRUD.
    _topology_cache: Dict[str, Dict[str, Set[str]]] = {}

    # 027 stage 1: the one graph build in flight per equivalent in this process (single-flight); its waiters read
    # the cache it stores. A future of another event loop is ignored.
    _inflight: Dict[str, "asyncio.Future[None]"] = {}
    # 027 `T2704` (a): when the read of each equivalent's cached graph BEGAN, on one process-wide tick
    # (`itertools.count`, not a clock: two events in one coarse clock tick would be unordered).
    _read_started: Dict[str, int] = {}
    _ticks = itertools.count(1)

    @classmethod
    def invalidate_cache(cls, equivalent_code: str | None = None) -> None:
        if equivalent_code:
            cls._graph_cache.pop(str(equivalent_code), None)
            cls._topology_cache.pop(str(equivalent_code), None)
        else:
            cls._graph_cache.clear()
            cls._topology_cache.clear()

    def __init__(self, session: AsyncSession):
        self.session = session
        # Graph structure: { from_pid: { to_pid: capacity } }
        self.graph: Dict[str, Dict[str, Decimal]] = {}
        # Edge flags: { from_pid: { to_pid: can_be_intermediate } }
        self.edge_can_be_intermediate: Dict[str, Dict[str, bool]] = {}
        # Edge policy: { from_pid: { to_pid: set(blocked_pid) } }
        self.edge_blocked_participants: Dict[str, Dict[str, Set[str]]] = {}
        # Owners (pids) whose active line on the pair forbids them to mediate over the hop u -> v.
        self.edge_no_transit: Dict[str, Dict[str, frozenset]] = {}
        # 026 В2: pairs (participant UUIDs) holding a requested close - the core's lock-mode hint, not a rule.
        self.pending_pairs: Set[frozenset] = set()
        # 028 `F-028-23`: the equivalent's precision when the graph was built; every capacity is floored to its step.
        self.precision: Optional[int] = None
        self.from_cache = False  # the last build_graph() was served by the shared cache (027: may be stale)
        self.pids: Dict[UUID, str] = {} # Map UUID to PID string for easier graph keys
        self.uuids: Dict[str, UUID] = {} # Map PID string to UUID

        # Trustline-only adjacency list: { debtor_pid: set(creditor_pid) }
        # Payment flow is debtor -> creditor.
        self.topology_adj: Dict[str, Set[str]] = {}

    async def build_topology(self, equivalent_code: str) -> None:
        """Build (or load from cache) trustline-only adjacency.

        This intentionally ignores remaining capacity/debts/locks so fully-saturated edges are still
        considered "connected" for NO_ROUTE vs INSUFFICIENT_CAPACITY distinction.
        """
        validate_equivalent_code(equivalent_code)

        cached = self._topology_cache.get(equivalent_code)
        if cached is not None:
            # Copy to isolate instance mutation.
            self.topology_adj = {u: set(vs) for u, vs in cached.items()}
            return

        # Join TrustLine -> Equivalent(code) + Participant (creditor/debtor) to avoid N+1 and avoid
        # a second lookup for participant UUID->PID mapping.
        from sqlalchemy.orm import aliased
        from app.db.models.participant import Participant

        Creditor = aliased(Participant)
        Debtor = aliased(Participant)

        stmt = (
            select(Debtor.pid, Creditor.pid)
            .select_from(TrustLine)
            .join(Equivalent, Equivalent.id == TrustLine.equivalent_id)
            .join(Creditor, Creditor.id == TrustLine.from_participant_id)
            .join(Debtor, Debtor.id == TrustLine.to_participant_id)
            .where(
                and_(
                    Equivalent.code == equivalent_code,
                    TrustLine.status == "active",
                )
            )
        )

        rows = (await self.session.execute(stmt)).all()
        adj: Dict[str, Set[str]] = {}
        for debtor_pid, creditor_pid in rows:
            d = str(debtor_pid or "").strip()
            c = str(creditor_pid or "").strip()
            if not d or not c:
                continue
            adj.setdefault(d, set()).add(c)

        self.topology_adj = {u: set(vs) for u, vs in adj.items()}
        # Store a copy in cache.
        self._topology_cache[equivalent_code] = {u: set(vs) for u, vs in adj.items()}

    def has_topology_path(self, from_pid: str, to_pid: str, *, max_hops: int = 6) -> bool:
        """Return True if a trustline-topology path exists (ignoring capacity).

        max_hops is aligned with routing constraint semantics: a topology-only path longer than
        max_hops should still be treated as NO_ROUTE for payment routing.
        """
        src = str(from_pid or "").strip()
        dst = str(to_pid or "").strip()
        if not src or not dst:
            return False
        if src == dst:
            return True

        max_hops = int(max_hops or 0)
        if max_hops <= 0:
            return False

        # BFS with hop limit.
        q: deque[tuple[str, int]] = deque([(src, 0)])
        seen: Set[str] = {src}

        while q:
            cur, hops = q.popleft()
            if hops >= max_hops:
                continue
            for nxt in self.topology_adj.get(cur, set()):
                if nxt == dst:
                    return True
                if nxt in seen:
                    continue
                seen.add(nxt)
                q.append((nxt, hops + 1))

        return False

    async def build_graph(self, equivalent_code: str, *, use_shared_cache: bool = False, refresh: bool = False,
                          reader=None):
        """Load the active lines and debts of the equivalent and build the capacity graph - a HINT, not a proof.

        027 stage 1. `use_shared_cache`: a cached graph younger than `ROUTING_GRAPH_CACHE_TTL_SECONDS`, else ONE
        build per equivalent in this process that concurrent callers wait for (single-flight), stored. `refresh`:
        build afresh and store (the payment's one re-route); it accepts only a graph whose read began
        AFTER the refresh was asked - never the read in flight before it, nor the old cache when that build failed. Neither: build afresh for this instance only. Topology
        edits drop the cache; money commits do not - a cached capacity may be stale up to the rest of a build in
        flight when the edit committed plus the TTL (the stamp is the publish time), and the core's final check
        re-reads the pair (`PaymentService._segment`). The default reads afresh in this router's
        session: `/payments/capacity`, `/payments/max-flow` and the simulator's targets answer from the state as
        it is, never from a cache a payment did not drop. `reader`: a factory of the async context the
        build reads in (the payment's own read transaction), entered only when a build actually runs.
        """
        validate_equivalent_code(equivalent_code)
        self.from_cache = False
        with log_duration(logger, "router.build_graph", equivalent=equivalent_code):
            ttl = settings.ROUTING_GRAPH_CACHE_TTL_SECONDS
            shared = (use_shared_cache or refresh) and ttl > 0
            # Follow the build in flight; when it stored nothing (failed, cancelled), follow the next leader or become
            # it. A re-route (`refresh`) waits too, but accepts only a read that began after it asked (027 `T2704` (a)).
            asked = next(self._ticks) if refresh else 0
            while shared:
                if self._load_cached(equivalent_code, ttl, read_after=asked):
                    return
                pending = self._inflight.get(equivalent_code)
                if pending is None or pending.get_loop() is not asyncio.get_running_loop():
                    break
                await asyncio.shield(pending)
            if not shared:
                await self._build_in(reader, equivalent_code, write_shared_cache=False)
                return
            done = asyncio.get_running_loop().create_future()
            self._inflight[equivalent_code] = done
            try:
                await self._build_in(reader, equivalent_code, write_shared_cache=True)
            finally:
                done.set_result(None)
                if self._inflight.get(equivalent_code) is done:
                    del self._inflight[equivalent_code]

    async def _build_in(self, reader, equivalent_code: str, *, write_shared_cache: bool) -> None:
        if reader is None:
            return await self._build_graph_impl(equivalent_code, write_shared_cache=write_shared_cache)
        own = self.session
        async with reader() as self.session:
            try:
                await self._build_graph_impl(equivalent_code, write_shared_cache=write_shared_cache)
            finally:
                self.session = own

    def _load_cached(self, equivalent_code: str, ttl: int, *, read_after: int = 0) -> bool:
        cached = self._graph_cache.get(equivalent_code)
        if cached is None or self._read_started.get(equivalent_code, 0) < read_after:
            return False
        # Backward-compatible cache unpacking.
        if len(cached) == 5:
            cached_at, graph, edge_policy, pids, uuids = cached  # type: ignore[misc]
            edge_blocked, no_transit = {}, []
        else:
            cached_at, graph, edge_policy, edge_blocked, pids, uuids, *no_transit = cached
        if (time.time() - cached_at) > ttl:
            return False
        # Shallow copies are enough because nested values are Decimals/bools.
        self.graph = {u: dict(v) for u, v in graph.items()}
        self.edge_can_be_intermediate = {u: dict(v) for u, v in edge_policy.items()}
        self.edge_blocked_participants = {
            u: {v: set(s) for v, s in m.items()} for u, m in (edge_blocked or {}).items()
        }
        self.pids = dict(pids)
        self.uuids = dict(uuids)
        self.edge_no_transit = {u: dict(v) for u, v in (no_transit or [{}])[0].items()}
        self.pending_pairs = set(no_transit[1]) if len(no_transit) > 1 else set()
        self.precision = no_transit[2] if len(no_transit) > 2 else None
        self.from_cache = True
        return True

    async def _build_graph_impl(
        self,
        equivalent_code: str,
        *,
        write_shared_cache: bool = True,
    ) -> None:
        read_started = next(self._ticks)
        # 1. Get Equivalent ID. 027 stage 1: columns, not ORM objects, throughout (F-027-4).
        stmt = select(Equivalent.id, Equivalent.precision).where(Equivalent.code == equivalent_code)
        row = (await self.session.execute(stmt)).one_or_none()
        equivalent_id, self.precision = (row[0], int(row[1])) if row is not None else (None, None)
        if equivalent_id is None:
            logger.warning(f"Equivalent {equivalent_code} not found")
            self.graph = {}
            return

        # 2. Load all TrustLines for this equivalent
        # We need to join with Participant to get PIDs
        # 026 `T2603.1`: a line with a requested close marks its pair pending (В2); since 028 `F-028-29` it is an
        # `active` line with limit 0 (the `frozen` status is gone).
        stmt = select(TrustLine.from_participant_id, TrustLine.to_participant_id, TrustLine.limit, TrustLine.policy,
                      TrustLine.status, TrustLine.close_requested_at).where(
            and_(TrustLine.equivalent_id == equivalent_id, TrustLine.status == 'active'))
        trustlines = (await self.session.execute(stmt)).all()
        requested = {frozenset((tl.from_participant_id, tl.to_participant_id))
                     for tl in trustlines if tl.close_requested_at is not None}

        # 3. Load all Debts for this equivalent
        stmt = select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == equivalent_id)
        debts = (await self.session.execute(stmt)).all()

        # Helper to map UUID -> PID
        # We can't easily join efficiently without loading participants.
        # Let's collect all needed participant IDs and fetch them in batch or rely on lazy loading (slow).
        # Better: Join in the initial queries.
        
        # Optimization: Fetch IDs and PIDs separately or use join.
        # Let's use a separate query to fetch Participant map.
        all_participant_ids = set()
        for tl in trustlines:
            all_participant_ids.add(tl.from_participant_id)
            all_participant_ids.add(tl.to_participant_id)
        
        # Debts should match trustlines, but let's be safe
        for d in debts:
            all_participant_ids.add(d.debtor_id)
            all_participant_ids.add(d.creditor_id)

        if not all_participant_ids:
            self.graph = {}
            return

        from app.db.models.participant import Participant
        stmt = select(Participant.id, Participant.pid, Participant.status).where(Participant.id.in_(all_participant_ids))
        result = await self.session.execute(stmt)
        rows = result.all()
        # 028 `F-028-28` (owner В-1): no hop to or from a participant that is not active - fewer refusals, not the
        # boundary (the core reads the status under its row locks, `MoneyBoundary.lock_participants`).
        out_of_circulation = {row.id for row in rows if row.status != "active"}
        
        self.pids = {row.id: row.pid for row in rows}
        self.uuids = {row.pid: row.id for row in rows}

        # Initialize graph
        self.graph = {pid: {} for pid in self.pids.values()}
        self.edge_can_be_intermediate = {pid: {} for pid in self.pids.values()}
        self.edge_blocked_participants = {pid: {} for pid in self.pids.values()}
        self.edge_no_transit = {}
        self.pending_pairs = set()

        # 4. Process Debts into a lookup: (debtor_id, creditor_id) -> amount
        debt_map = {} # (debtor_uuid, creditor_uuid) -> amount
        for d in debts:
            debt_map[(d.debtor_id, d.creditor_id)] = d.amount

        # 5. Build edges (protocol §6.3.1, `capacity.py`). No reservations are subtracted: REST payments
        # hold none (019 `T1909`). A pair with an active line gets a hop in each direction whose capacity
        # is positive; a pair without one gets none (024 `T2415.2`, owner decision 2026-09-29, GEO).
        lines = {(tl.from_participant_id, tl.to_participant_id): tl for tl in trustlines}
        step = money_step(self.precision)
        for x, y in {frozenset(k) for k in lines if k[0] != k[1] and not out_of_circulation & set(k)}:
            pair = [lines[k] for k in ((x, y), (y, x)) if k in lines]
            forbid, blocked = pair_rules((self.pids.get(tl.from_participant_id), tl.policy) for tl in pair)
            pending = frozenset((x, y)) in requested  # 026 В2, `capacity.py` addendum
            if pending:
                self.pending_pairs.add(frozenset((x, y)))
            for payer, payee in ((x, y), (y, x)):
                payer_pid, payee_pid = self.pids.get(payer), self.pids.get(payee)
                payee_owes = debt_map.get((payee, payer), Decimal('0'))
                cap = pair_capacity(
                    line_limit=getattr(lines.get((payee, payer)), "limit", None),
                    payer_owes=debt_map.get((payer, payee), Decimal('0')),
                    payee_owes=payee_owes,
                    pair_has_active_line=True,
                )
                # 028 `F-028-23`: in the equivalent's step, so every split `min(remaining, bottleneck)` and every
                # max-flow sum is a multiple of it (a stored non-multiple is never offered).
                cap = floor_to_step(cap, step)
                if pending:
                    cap = pending_pair_capacity(cap, payee_owes=payee_owes, step=step)
                if payer_pid and payee_pid and cap > 0:
                    self._add_capacity(payer_pid, payee_pid, cap)
                    self._set_edge_policy(payer_pid, payee_pid, payee_pid not in forbid)
                    self._set_edge_blocked_participants(payer_pid, payee_pid, set(blocked))
                    self.edge_no_transit.setdefault(payer_pid, {})[payee_pid] = forbid

        ttl = settings.ROUTING_GRAPH_CACHE_TTL_SECONDS
        if write_shared_cache and ttl > 0:
            self._read_started[equivalent_code] = read_started
            self._graph_cache[equivalent_code] = (
                time.time(),
                {u: dict(v) for u, v in self.graph.items()},
                {u: dict(v) for u, v in self.edge_can_be_intermediate.items()},
                {u: {v: set(s) for v, s in m.items()} for u, m in self.edge_blocked_participants.items()},
                dict(self.pids),
                dict(self.uuids),
                {u: dict(v) for u, v in self.edge_no_transit.items()},
                set(self.pending_pairs),
                self.precision,
            )

    def _add_capacity(self, u: str, v: str, amount: Decimal):
        if u not in self.graph:
            self.graph[u] = {}
        current = self.graph[u].get(v, Decimal('0'))
        self.graph[u][v] = current + amount

    def _set_edge_policy(self, u: str, v: str, can_be_intermediate: bool) -> None:
        if u not in self.edge_can_be_intermediate:
            self.edge_can_be_intermediate[u] = {}
        self.edge_can_be_intermediate[u][v] = can_be_intermediate

    def _set_edge_blocked_participants(self, u: str, v: str, blocked: Set[str]) -> None:
        if u not in self.edge_blocked_participants:
            self.edge_blocked_participants[u] = {}
        self.edge_blocked_participants[u][v] = set(blocked or set())

    def _edge_allows_intermediate(self, u: str, v: str) -> bool:
        return self.edge_can_be_intermediate.get(u, {}).get(v, True)

    def _edge_blocked(self, u: str, v: str) -> Set[str]:
        return self.edge_blocked_participants.get(u, {}).get(v, set())

    def _hop_rules(self, u: str, v: str) -> Tuple[frozenset, frozenset]:
        forbid = self.edge_no_transit.get(u, {}).get(v, frozenset())
        if not self._edge_allows_intermediate(u, v):
            forbid = forbid | {v}
        return forbid, frozenset(self._edge_blocked(u, v))

    def _bfs_single_path(
        self,
        from_pid: str,
        to_pid: str,
        amount: Decimal,
        *,
        max_hops: int,
        forbidden_edges: Set[Tuple[str, str]] | None = None,
        forbidden_nodes: Set[str] | None = None,
        graph_override: Dict[str, Dict[str, Decimal]] | None = None,
        deadline: float | None = None,
    ) -> Optional[List[str]]:
        graph = graph_override or self.graph

        if from_pid not in graph or to_pid not in graph:
            return None

        forbidden_edges = forbidden_edges or set()
        static_forbidden_nodes = forbidden_nodes or set()

        if from_pid in static_forbidden_nodes or to_pid in static_forbidden_nodes:
            return None

        queue: List[Tuple[str, List[str]]] = [(from_pid, [from_pid])]

        while queue:
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutException("Routing timed out")

            current, path = queue.pop(0)
            if current == to_pid:
                return path

            if (len(path) - 1) >= max_hops:
                continue

            for neighbor, capacity in graph.get(current, {}).items():
                if (current, neighbor) in forbidden_edges:
                    continue
                if neighbor in static_forbidden_nodes:
                    continue
                if capacity <= 0:
                    continue
                if capacity < amount:
                    continue
                if neighbor in path:
                    continue

                # One policy rule for the router and the core (`capacity.route_breaks_policy`).
                if route_breaks_policy(path + [neighbor], self._hop_rules, payee=to_pid):
                    continue

                queue.append((neighbor, path + [neighbor]))

        return None

    def _path_bottleneck(self, path: List[str], *, graph: Dict[str, Dict[str, Decimal]]) -> Decimal:
        b = Decimal('Infinity')
        for u, v in zip(path[:-1], path[1:]):
            b = min(b, graph.get(u, {}).get(v, Decimal('0')))
        return b

    def find_flow_routes(
        self,
        from_pid: str,
        to_pid: str,
        amount: Decimal,
        *,
        max_hops: int = 6,
        max_paths: int = 3,
        timeout_ms: int | None = None,
        avoid_participants: Iterable[str] | None = None,
    ) -> List[Tuple[List[str], Decimal]]:
        """Find up to max_paths routes that sum to amount.

        MVP multipath: iterative augmentation on a residual copy of the capacity graph.
        - Respects edge can_be_intermediate constraints.
        - Enforces max_hops.
        """
        if amount <= 0 or max_paths <= 0:
            return []

        effective_timeout_ms = int(
            timeout_ms
            if timeout_ms is not None
            else (settings.ROUTING_PATH_FINDING_TIMEOUT_MS or 50)
        )
        effective_timeout_ms = max(1, effective_timeout_ms)
        deadline = time.perf_counter() + (effective_timeout_ms / 1000.0)

        forbidden_nodes: Set[str] = set()
        if avoid_participants:
            forbidden_nodes = {
                str(x)
                for x in avoid_participants
                if isinstance(x, str) and x.strip()
            }

        # Working copy; subtract allocations to avoid over-committing shared edges.
        residual: Dict[str, Dict[str, Decimal]] = {u: d.copy() for u, d in self.graph.items()}

        remaining = amount
        routes: List[Tuple[List[str], Decimal]] = []

        while remaining > 0 and len(routes) < max_paths:
            if time.perf_counter() >= deadline:
                try:
                    ROUTING_FAILURES_TOTAL.labels(reason="timeout").inc()
                except Exception:
                    pass
                logger.info(
                    "event=routing.timeout from_pid=%s to_pid=%s timeout_ms=%s",
                    from_pid,
                    to_pid,
                    effective_timeout_ms,
                )
                raise TimeoutException("Routing timed out")

            path = self._bfs_single_path(
                from_pid,
                to_pid,
                Decimal('0'),
                max_hops=max_hops,
                graph_override=residual,
                forbidden_nodes=forbidden_nodes,
                deadline=deadline,
            )
            if not path:
                break

            bottleneck = self._path_bottleneck(path, graph=residual)
            if bottleneck <= 0:
                break

            alloc = min(remaining, bottleneck)
            if alloc <= 0:
                break

            # Update residual capacities along the path.
            for u, v in zip(path[:-1], path[1:]):
                new_cap = residual.get(u, {}).get(v, Decimal('0')) - alloc
                if new_cap <= 0:
                    residual.get(u, {}).pop(v, None)
                else:
                    residual[u][v] = new_cap

            routes.append((path, alloc))
            remaining -= alloc

        if remaining > 0:
            return []
        return routes

    def check_capacity(self, from_pid: str, to_pid: str, amount: Decimal) -> CapacityResponse:
        routes = self.find_flow_routes(
            from_pid,
            to_pid,
            amount,
            max_hops=settings.ROUTING_MAX_HOPS,
            max_paths=settings.ROUTING_MAX_PATHS,
        )
        can_pay = len(routes) > 0
        
        return CapacityResponse(
            can_pay=can_pay,
            max_amount=str(amount) if can_pay else "0", # Circular in MVP; use /max-flow for estimate
            routes_count=len(routes),
            estimated_hops=(len(routes[0][0]) - 1) if routes else 0,
        )
    
    def calculate_max_flow(self, from_pid: str, to_pid: str) -> MaxFlowResponse:
        """
        Edmonds-Karp or similar to find max flow.
        """
        # T1545, 2026-09-13: with from == to the BFS finds a zero-length path at once, the residual
        # update does nothing, and `while True` finds it again forever - synchronously, inside an
        # async route. Guarded here, not in the route, because the simulator also calls this.
        # Same refusal as a payment to yourself (`PaymentService`, "Cannot pay to yourself").
        if from_pid == to_pid:
            raise BadRequestException("Cannot pay to yourself")

        # Create a working copy of the graph since we modify residuals
        residual_graph = {u: d.copy() for u, d in self.graph.items()}
        
        max_flow = Decimal('0')
        paths = []
        
        while True:
            # BFS for augmenting path
            queue = [(from_pid, [from_pid], Decimal('Infinity'))]
            visited = {from_pid}
            path_found = None
            
            while queue:
                u, path, flow = queue.pop(0)
                if u == to_pid:
                    path_found = (path, flow)
                    break
                
                if len(path) > settings.MAX_FLOW_MAX_HOPS: # Limit hops for performance
                    continue

                for v, cap in residual_graph.get(u, {}).items():
                    # Policy per augmenting path only: residual re-routing may still overstate (BACKLOG).
                    if route_breaks_policy(path + [v], self._hop_rules, payee=to_pid):
                        continue
                    if v not in visited and cap > 0:
                        visited.add(v)
                        new_flow = min(flow, cap)
                        queue.append((v, path + [v], new_flow))
            
            if not path_found:
                break
                
            path, flow = path_found
            max_flow += flow
            paths.append(MaxFlowPath(path=path, capacity=str(flow)))
            
            # Update residuals
            for i in range(len(path) - 1):
                u, v = path[i], path[i+1]
                residual_graph[u][v] -= flow
                if residual_graph[u][v] == 0:
                    del residual_graph[u][v]
                
                # Add reverse flow
                if u not in residual_graph.get(v, {}):
                    if v not in residual_graph:
                        residual_graph[v] = {}
                    residual_graph[v][u] = 0
                residual_graph[v][u] += flow

        # Identify bottlenecks (min-cut edges roughly, or just full edges on the paths)
        # For MVP, just listing edges on paths that have 0 remaining capacity in original direction?
        # Let's return empty bottlenecks for now or simple heuristic.
        
        include_metadata = settings.FEATURE_FLAGS_FULL_MULTIPATH_ENABLED

        return MaxFlowResponse(
            max_amount=str(max_flow),
            paths=paths if include_metadata else [],
            bottlenecks=[],
            algorithm="Edmonds-Karp (BFS)",
            computed_at=datetime.now(timezone.utc).isoformat(),
        )
