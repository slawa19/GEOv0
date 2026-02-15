import logging
import heapq
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple, Iterable
from uuid import UUID

from app.utils.observability import log_duration

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trustline import TrustLine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.prepare_lock import PrepareLock
from app.schemas.payment import CapacityResponse, MaxFlowResponse, MaxFlowPath, Bottleneck
from app.config import settings
from app.utils.metrics import ROUTING_FAILURES_TOTAL
from app.utils.validation import validate_equivalent_code
from app.utils.exceptions import TimeoutException

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

    async def build_graph(self, equivalent_code: str):
        """Loads all trustlines and debts for the given equivalent and builds the capacity graph."""
        validate_equivalent_code(equivalent_code)
        with log_duration(logger, "router.build_graph", equivalent=equivalent_code):
            ttl = int(getattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 0) or 0)
            if ttl > 0:
                cached = self._graph_cache.get(equivalent_code)
                if cached is not None:
                    # Backward-compatible cache unpacking.
                    if len(cached) == 5:
                        cached_at, graph, edge_policy, pids, uuids = cached  # type: ignore[misc]
                        edge_blocked = {}
                    else:
                        cached_at, graph, edge_policy, edge_blocked, pids, uuids = cached
                    if (time.time() - cached_at) <= ttl:
                        # Shallow copies are enough because nested values are Decimals/bools.
                        self.graph = {u: dict(v) for u, v in graph.items()}
                        self.edge_can_be_intermediate = {u: dict(v) for u, v in edge_policy.items()}
                        self.edge_blocked_participants = {
                            u: {v: set(s) for v, s in m.items()} for u, m in (edge_blocked or {}).items()
                        }
                        self.pids = dict(pids)
                        self.uuids = dict(uuids)
                        return

            await self._build_graph_impl(equivalent_code)

    async def _build_graph_impl(self, equivalent_code: str) -> None:
        # 1. Get Equivalent ID
        stmt = select(Equivalent).where(Equivalent.code == equivalent_code)
        result = await self.session.execute(stmt)
        equivalent = result.scalar_one_or_none()
        if not equivalent:
            logger.warning(f"Equivalent {equivalent_code} not found")
            self.graph = {}
            return

        # 2. Load all TrustLines for this equivalent
        # We need to join with Participant to get PIDs
        stmt = select(TrustLine).where(
            and_(
                TrustLine.equivalent_id == equivalent.id,
                TrustLine.status == 'active'
            )
        )
        result = await self.session.execute(stmt)
        trustlines = result.scalars().all()

        # 3. Load all Debts for this equivalent
        stmt = select(Debt).where(Debt.equivalent_id == equivalent.id)
        result = await self.session.execute(stmt)
        debts = result.scalars().all()

        # 3b. Load all active PrepareLocks (to account for reserved capacity)
        lock_stmt = select(PrepareLock).where(PrepareLock.expires_at > func.now())
        lock_result = await self.session.execute(lock_stmt)
        active_locks = lock_result.scalars().all()
        
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

        # Locks might include participants not present in current trustlines/debts lists.
        for lock in active_locks:
            all_participant_ids.add(lock.participant_id)
            for flow in (lock.effects or {}).get('flows', []):
                try:
                    all_participant_ids.add(UUID(flow['from']))
                    all_participant_ids.add(UUID(flow['to']))
                except Exception:
                    continue

        if not all_participant_ids:
            self.graph = {}
            return

        from app.db.models.participant import Participant
        stmt = select(Participant.id, Participant.pid).where(Participant.id.in_(all_participant_ids))
        result = await self.session.execute(stmt)
        rows = result.all()
        
        self.pids = {row.id: row.pid for row in rows}
        self.uuids = {row.pid: row.id for row in rows}

        # Initialize graph
        self.graph = {pid: {} for pid in self.pids.values()}
        self.edge_can_be_intermediate = {pid: {} for pid in self.pids.values()}
        self.edge_blocked_participants = {pid: {} for pid in self.pids.values()}

        # 4. Process Debts into a lookup: (debtor_id, creditor_id) -> amount
        debt_map = {} # (debtor_uuid, creditor_uuid) -> amount
        for d in debts:
            debt_map[(d.debtor_id, d.creditor_id)] = d.amount

        # 5. Build reserved capacity map from active locks: (from_pid, to_pid) -> reserved_amount
        reserved_map: Dict[Tuple[str, str], Decimal] = {}
        for lock in active_locks:
            for flow in (lock.effects or {}).get('flows', []):
                try:
                    eq_id = UUID(flow['equivalent'])
                    if eq_id != equivalent.id:
                        continue
                    from_id = UUID(flow['from'])
                    to_id = UUID(flow['to'])
                    amt = Decimal(str(flow['amount']))
                except Exception:
                    continue

                from_pid = self.pids.get(from_id)
                to_pid = self.pids.get(to_id)
                if not from_pid or not to_pid:
                    continue

                key = (from_pid, to_pid)
                reserved_map[key] = reserved_map.get(key, Decimal('0')) + amt

        # 6. Build edges
        # Payment flow direction is Sender -> Receiver.
        # A payment S -> R increases S's debt to R.
        # Therefore, the credit limit that enables S -> R is the TrustLine R -> S
        # (R trusts S up to a limit).
        for tl in trustlines:
            creditor_id = tl.from_participant_id  # trusts
            debtor_id = tl.to_participant_id      # can owe

            creditor_pid = self.pids.get(creditor_id)
            debtor_pid = self.pids.get(debtor_id)

            if not creditor_pid or not debtor_pid:
                continue

            limit = tl.limit
            can_be_intermediate = True
            blocked_participants: Set[str] = set()
            if tl.policy is not None:
                can_be_intermediate = bool(tl.policy.get('can_be_intermediate', True))
                # Best-effort: if max_hop_usage is explicitly 0, treat as forbid intermediate usage.
                try:
                    if int(tl.policy.get('max_hop_usage', 1)) == 0:
                        can_be_intermediate = False
                except Exception:
                    pass

                try:
                    bp = tl.policy.get("blocked_participants", None)
                    if isinstance(bp, list):
                        blocked_participants = {str(x) for x in bp if isinstance(x, str) and x}
                except Exception:
                    blocked_participants = set()

            # Debt(debtor -> creditor)
            debt_debtor_owes_creditor = debt_map.get((debtor_id, creditor_id), Decimal('0'))
            debt_creditor_owes_debtor = debt_map.get((creditor_id, debtor_id), Decimal('0'))

            # Capacity for debtor -> creditor:
            # - can create more debt up to (limit - current_debt)
            # - can also offset reverse debt (creditor owes debtor)
            cap = (limit - debt_debtor_owes_creditor) + debt_creditor_owes_debtor

            # Subtract reserved capacity due to other prepared transactions.
            cap -= reserved_map.get((debtor_pid, creditor_pid), Decimal('0'))
            if cap > 0:
                self._add_capacity(debtor_pid, creditor_pid, cap)
                self._set_edge_policy(debtor_pid, creditor_pid, can_be_intermediate)
                self._set_edge_blocked_participants(debtor_pid, creditor_pid, blocked_participants)

        # Debt-only capacity is already included via the trustline-based formula:
        # cap = (limit - debt_debtor_owes_creditor) + debt_creditor_owes_debtor.
        # If limit=0 and creditor owes debtor, debt_creditor_owes_debtor still provides positive capacity.

        ttl = int(getattr(settings, "ROUTING_GRAPH_CACHE_TTL_SECONDS", 0) or 0)
        if ttl > 0:
            self._graph_cache[equivalent_code] = (
                time.time(),
                {u: dict(v) for u, v in self.graph.items()},
                {u: dict(v) for u, v in self.edge_can_be_intermediate.items()},
                {u: {v: set(s) for v, s in m.items()} for u, m in self.edge_blocked_participants.items()},
                dict(self.pids),
                dict(self.uuids),
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

        # Track cumulative blocked_participants from policies along the current path.
        queue: List[Tuple[str, List[str], Set[str]]] = [(from_pid, [from_pid], set())]

        while queue:
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutException("Routing timed out")

            current, path, blocked_so_far = queue.pop(0)
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

                # Enforce blocked_participants from earlier edges: forbid using such nodes as intermediates.
                if neighbor not in {from_pid, to_pid} and neighbor in blocked_so_far:
                    continue

                edge_blocked = self._edge_blocked(current, neighbor)
                if edge_blocked:
                    # Block using forbidden PIDs as intermediate nodes (endpoints allowed).
                    if neighbor not in {from_pid, to_pid} and neighbor in edge_blocked:
                        continue

                    # Also forbid adding this edge if it blocks any already-used intermediate node.
                    if len(path) > 2:
                        intermediates = set(path[1:-1])
                        if intermediates & edge_blocked:
                            continue

                # Enforce can_be_intermediate on the edge when neighbor is used as intermediate.
                if neighbor not in {from_pid, to_pid}:
                    if not self._edge_allows_intermediate(current, neighbor):
                        continue

                next_blocked = blocked_so_far
                if edge_blocked:
                    next_blocked = set(blocked_so_far)
                    next_blocked.update(edge_blocked)

                queue.append((neighbor, path + [neighbor], next_blocked))

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
            else (getattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 50) or 50)
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

    def find_paths(
        self,
        from_pid: str,
        to_pid: str,
        amount: Decimal,
        max_hops: int = 6,
        k: int = 3,
    ) -> List[List[str]]:
        """Find up to k shortest (by hops) simple paths with capacity >= amount.

        Implements Yen's algorithm for k-shortest simple paths, using BFS as the
        shortest-path oracle (unweighted edges => shortest by hop count).

        Tie-break: among equal hop-count candidates, prefer higher bottleneck.
        """

        if k <= 0:
            return []

        first = self._bfs_single_path(from_pid, to_pid, amount, max_hops=max_hops)
        if not first:
            return []

        def _path_bottleneck_for_sort(path: List[str]) -> Decimal:
            return self._path_bottleneck(path, graph=self.graph)

        shortest_paths: List[List[str]] = [first]
        # Min-heap of candidates: (hop_len, -bottleneck, path_tuple)
        candidate_heap: List[Tuple[int, Decimal, Tuple[str, ...]]] = []
        candidate_set: Set[Tuple[str, ...]] = set()

        for _ in range(1, k):
            prev = shortest_paths[-1]

            for j in range(len(prev) - 1):
                root_path = prev[: j + 1]
                spur_node = prev[j]

                # Forbid edges that would recreate any previously accepted path
                # that shares the same root.
                forbidden_edges: Set[Tuple[str, str]] = set()
                for p in shortest_paths:
                    if len(p) > j and p[: j + 1] == root_path:
                        forbidden_edges.add((p[j], p[j + 1]))

                # Forbid nodes in root_path except spur_node to enforce simple paths.
                forbidden_nodes: Set[str] = set(root_path[:-1])

                spur_path = self._bfs_single_path(
                    spur_node,
                    to_pid,
                    amount,
                    max_hops=max_hops - j,
                    forbidden_edges=forbidden_edges,
                    forbidden_nodes=forbidden_nodes,
                )
                if not spur_path:
                    continue

                candidate = root_path[:-1] + spur_path
                candidate_t = tuple(candidate)
                if candidate_t in candidate_set:
                    continue
                if candidate in shortest_paths:
                    continue

                hop_len = len(candidate)
                bn = _path_bottleneck_for_sort(candidate)
                heapq.heappush(candidate_heap, (hop_len, -bn, candidate_t))
                candidate_set.add(candidate_t)

            if not candidate_heap:
                break

            hop_len, neg_bn, best = heapq.heappop(candidate_heap)
            candidate_set.discard(best)
            shortest_paths.append(list(best))

        return shortest_paths

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
                    if v not in residual_graph: residual_graph[v] = {}
                    residual_graph[v][u] = 0
                residual_graph[v][u] += flow

        # Identify bottlenecks (min-cut edges roughly, or just full edges on the paths)
        # For MVP, just listing edges on paths that have 0 remaining capacity in original direction?
        # Let's return empty bottlenecks for now or simple heuristic.
        
        include_metadata = bool(getattr(settings, "FEATURE_FLAGS_FULL_MULTIPATH_ENABLED", False))

        return MaxFlowResponse(
            max_amount=str(max_flow),
            paths=paths if include_metadata else [],
            bottlenecks=[],
            algorithm="Edmonds-Karp (BFS)",
            computed_at=datetime.now(timezone.utc).isoformat(),
        )
