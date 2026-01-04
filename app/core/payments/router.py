import logging
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trustline import TrustLine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.schemas.payment import CapacityResponse, MaxFlowResponse, MaxFlowPath, Bottleneck

logger = logging.getLogger(__name__)

class PaymentRouter:
    def __init__(self, session: AsyncSession):
        self.session = session
        # Graph structure: { from_pid: { to_pid: capacity } }
        self.graph: Dict[str, Dict[str, Decimal]] = {}
        self.pids: Dict[UUID, str] = {} # Map UUID to PID string for easier graph keys
        self.uuids: Dict[str, UUID] = {} # Map PID string to UUID

    async def build_graph(self, equivalent_code: str):
        """
        Loads all trustlines and debts for the given equivalent and builds the capacity graph.
        """
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
        stmt = select(Participant.id, Participant.pid).where(Participant.id.in_(all_participant_ids))
        result = await self.session.execute(stmt)
        rows = result.all()
        
        self.pids = {row.id: row.pid for row in rows}
        self.uuids = {row.pid: row.id for row in rows}

        # Initialize graph
        self.graph = {pid: {} for pid in self.pids.values()}

        # 4. Process Debts into a lookup: (debtor_id, creditor_id) -> amount
        debt_map = {} # (debtor_uuid, creditor_uuid) -> amount
        for d in debts:
            debt_map[(d.debtor_id, d.creditor_id)] = d.amount

        # 5. Build edges
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

            # Debt(debtor -> creditor)
            debt_debtor_owes_creditor = debt_map.get((debtor_id, creditor_id), Decimal('0'))
            debt_creditor_owes_debtor = debt_map.get((creditor_id, debtor_id), Decimal('0'))

            # Capacity for debtor -> creditor:
            # - can create more debt up to (limit - current_debt)
            # - can also offset reverse debt (creditor owes debtor)
            cap = (limit - debt_debtor_owes_creditor) + debt_creditor_owes_debtor
            if cap > 0:
                self._add_capacity(debtor_pid, creditor_pid, cap)

    def _add_capacity(self, u: str, v: str, amount: Decimal):
        if u not in self.graph:
            self.graph[u] = {}
        current = self.graph[u].get(v, Decimal('0'))
        self.graph[u][v] = current + amount

    def find_paths(self, from_pid: str, to_pid: str, amount: Decimal, max_hops: int = 6) -> List[List[str]]:
        """
        Find paths from source to target with sufficient capacity.
        Uses BFS to find shortest path by hops.
        Returns list of paths (list of nodes).
        For MVP, finding just one shortest path is often enough, but let's try to support multi-path conceptually.
        Actually, standard payments usually use one path. 
        Max-flow splits across paths.
        
        Here we implement simple BFS for the shortest path with capacity >= amount.
        """
        if from_pid not in self.graph or to_pid not in self.graph:
            return []

        queue = [(from_pid, [from_pid])] # (current_node, path)
        visited = {from_pid} # Visited set to avoid cycles. 
        # Note: BFS finds shortest path in unweighted graph (hops).
        
        # Limitation: This BFS stops at first match.
        # If we want k-shortest paths, it's more complex.
        # For MVP "check_capacity", one valid path is enough.
        
        while queue:
            current, path = queue.pop(0)
            
            if current == to_pid:
                return [path]
            
            if len(path) > max_hops:
                continue
                
            neighbors = self.graph.get(current, {})
            for neighbor, capacity in neighbors.items():
                if neighbor not in visited and capacity >= amount:
                    visited.add(neighbor) # Mark visited when enqueuing for BFS
                    queue.append((neighbor, path + [neighbor]))
                    
        return []

    def check_capacity(self, from_pid: str, to_pid: str, amount: Decimal) -> CapacityResponse:
        paths = self.find_paths(from_pid, to_pid, amount)
        can_pay = len(paths) > 0
        
        return CapacityResponse(
            can_pay=can_pay,
            max_amount=str(amount) if can_pay else "0", # This logic is slightly circular, max_amount logic needs real max flow
            routes_count=len(paths),
            estimated_hops=len(paths[0]) - 1 if paths else None
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
                
                if len(path) > 7: # Limit hops for performance
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
        
        return MaxFlowResponse(
            max_amount=str(max_flow),
            paths=paths,
            bottlenecks=[],
            algorithm="Edmonds-Karp (BFS)",
            computed_at="now"
        )