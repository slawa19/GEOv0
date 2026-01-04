import logging
import uuid
from decimal import Decimal
from typing import List, Dict, Optional, Set, Tuple

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.utils.exceptions import GeoException
from app.utils.metrics import CLEARING_EVENTS_TOTAL

logger = logging.getLogger(__name__)

class ClearingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _locked_pairs_for_equivalent(self, equivalent_id: uuid.UUID) -> Set[frozenset[uuid.UUID]]:
        """Return participant pairs that must not be touched by clearing.

        For MVP safety we treat any active prepared payment flow `from->to` as a lock on the unordered
        participant pair {from, to}. Clearing must not modify debts between these participants.
        """
        stmt = select(PrepareLock).where(PrepareLock.expires_at > func.now())
        locks = (await self.session.execute(stmt)).scalars().all()

        locked: Set[frozenset[uuid.UUID]] = set()
        for lock in locks:
            for flow in (lock.effects or {}).get('flows', []):
                try:
                    eq_id = uuid.UUID(str(flow.get('equivalent')))
                    if eq_id != equivalent_id:
                        continue
                    from_id = uuid.UUID(str(flow.get('from')))
                    to_id = uuid.UUID(str(flow.get('to')))
                except Exception:
                    continue

                locked.add(frozenset({from_id, to_id}))

        return locked

    async def find_cycles(self, equivalent_code: str, max_depth: int = 6) -> List[List[Dict]]:
        """
        Find closed cycles of debts for a given equivalent.
        Returns list of cycles, where each cycle is a list of Debt objects (or dicts representing edges).
        
        Algorithm:
        1. Load all debts for this equivalent into memory (Graph).
           For MVP (small scale), this is feasible. For production, we need more optimized graph DB or targeted search.
        2. Perform DFS/BFS to find cycles.
        """
        logger.info("event=clearing.find_cycles equivalent=%s max_depth=%s", equivalent_code, max_depth)
        try:
            CLEARING_EVENTS_TOTAL.labels(event="find_cycles", result="start").inc()
        except Exception:
            pass

        equivalent = (await self.session.execute(select(Equivalent).where(Equivalent.code == equivalent_code))).scalar_one_or_none()
        if not equivalent:
            try:
                CLEARING_EVENTS_TOTAL.labels(event="find_cycles", result="not_found").inc()
            except Exception:
                pass
            raise GeoException(f"Equivalent {equivalent_code} not found")

        # 1. Load Graph
        # Node: Participant ID
        # Edge: Debt (debtor -> creditor, amount)
        stmt = select(Debt).where(
            and_(
                Debt.equivalent_id == equivalent.id,
                Debt.amount > 0
            )
        )
        all_debts = (await self.session.execute(stmt)).scalars().all()

        # Exclude edges that are involved in active prepared payments.
        locked_pairs = await self._locked_pairs_for_equivalent(equivalent.id)
        if locked_pairs:
            all_debts = [
                d for d in all_debts
                if frozenset({d.debtor_id, d.creditor_id}) not in locked_pairs
            ]
        
        adjacency: Dict[uuid.UUID, List[Debt]] = {}
        for d in all_debts:
            if d.debtor_id not in adjacency:
                adjacency[d.debtor_id] = []
            adjacency[d.debtor_id].append(d)

        # Build UUID -> PID mapping for participants in this graph.
        participant_ids: Set[uuid.UUID] = set()
        for d in all_debts:
            participant_ids.add(d.debtor_id)
            participant_ids.add(d.creditor_id)

        pid_by_id: Dict[uuid.UUID, str] = {}
        if participant_ids:
            participants = (
                await self.session.execute(
                    select(Participant).where(Participant.id.in_(list(participant_ids)))
                )
            ).scalars().all()
            pid_by_id = {p.id: p.pid for p in participants}
            
        # 2. Find Cycles
        # We look for simple cycles.
        cycles = []
        visited = set()
        
        # To avoid duplicates (e.g. A->B->C->A vs B->C->A->B), we can enforce ordering or use set of sets.
        # Simple DFS with path tracking.
        
        def dfs(start_node: uuid.UUID, current_node: uuid.UUID, path: List[Debt], visited_in_path: Set[uuid.UUID]):
            if len(path) > max_depth:
                return

            if current_node not in adjacency:
                return

            for edge in adjacency[current_node]:
                neighbor = edge.creditor_id
                
                if neighbor == start_node:
                    # Cycle found!
                    cycles.append(path + [edge])
                    return
                
                if neighbor not in visited_in_path:
                    dfs(start_node, neighbor, path + [edge], visited_in_path | {neighbor})

        # Run DFS from each node. 
        # Optimization: Remove nodes that cannot be part of a cycle (in-degree=0 or out-degree=0).
        # Optimization: Once a cycle is found, we might want to "consume" it? 
        # But here we just LIST them.
        
        # We need to avoid finding same cycle multiple times starting from different nodes.
        # Canonization: Cycle is represented by min(node_id) as start?
        
        nodes = list(adjacency.keys())
        # Sort for determinism
        # nodes.sort() 
        
        # We need a robust cycle finder.
        # NetworkX is good but adding dependency? Let's keep it simple custom DFS.
        # Since we want to find *any* cycle to clear, we don't need *all* cycles.
        
        unique_cycles_hashes = set()
        unique_cycles = []
        
        for node in nodes:
            # Simple DFS from this node
            stack = [(node, [], {node})] # (current, path_edges, path_nodes)
            
            # DFS Iterative to avoid recursion limit
            # But recursive is easier to write for finding ALL simple cycles (within depth).
            # Let's use the recursive inner function but controlled.
            pass

        # Let's retry simple approach:
        # Iterate all nodes. If node not visited globally (optional optimization?), start DFS.
        # Actually finding ALL cycles in a graph is NP-hard (or exponential).
        # We usually want "Shortest Cycle" or "Any Cycle".
        
        # Let's implement finding ONE cycle per run? Or a few.
        # Clearing usually iterates: Find Cycle -> Clear -> Repeat.
        
        # Heuristic: Start from nodes with Debts.
        for start_node in nodes:
             # Limit search
             if len(cycles) > 10: break
             
             dfs(start_node, start_node, [], {start_node})

        # Filter duplicates
        final_cycles = []
        for cycle in cycles:
            # cycle is list of Debt objects
            # Signature: sorted list of debt IDs?
            ids = sorted([d.id for d in cycle])
            h = tuple(ids)
            if h not in unique_cycles_hashes:
                unique_cycles_hashes.add(h)
                
                # Format for output
                cycle_data = []
                for edge in cycle:
                    cycle_data.append({
                        'debt_id': str(edge.id),
                        'debtor': str(pid_by_id.get(edge.debtor_id, edge.debtor_id)),
                        'creditor': str(pid_by_id.get(edge.creditor_id, edge.creditor_id)),
                        'amount': str(edge.amount)
                    })
                final_cycles.append(cycle_data)

        logger.info("event=clearing.find_cycles_done equivalent=%s cycles=%s", equivalent_code, len(final_cycles))
        try:
            CLEARING_EVENTS_TOTAL.labels(event="find_cycles", result="success").inc()
        except Exception:
            pass
        return final_cycles

    async def execute_clearing(self, cycle: List[Dict]) -> bool:
        """
        Execute clearing for a specific cycle.
        cycle: list of dicts {'debt_id': ..., 'amount': ...} or similar from find_cycles
        """
        if not cycle:
            return False

        logger.info("event=clearing.execute cycle_len=%s", len(cycle))
        try:
            CLEARING_EVENTS_TOTAL.labels(event="execute", result="start").inc()
        except Exception:
            pass

        # Load debts for this cycle to avoid relying on debtor/creditor fields in the API output.
        try:
            debt_ids = [uuid.UUID(str(edge['debt_id'])) for edge in cycle]
        except Exception:
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="bad_request").inc()
            except Exception:
                pass
            return False

        debts = (
            await self.session.execute(
                select(Debt).where(Debt.id.in_(debt_ids)).with_for_update()
            )
        ).scalars().all()

        if len(debts) != len(debt_ids):
            return False

        # 1. Determine clearing amount (min amount in cycle)
        clear_amount = min([d.amount for d in debts])
        
        if clear_amount <= 0:
            return False
            
        logger.info(
            "event=clearing.execute_ready cycle_len=%s amount=%s",
            len(cycle),
            clear_amount,
        )

        # Reject cycles that touch any edge reserved by active PrepareLocks.
        locked_pairs = await self._locked_pairs_for_equivalent(debts[0].equivalent_id)
        if locked_pairs:
            for d in debts:
                pair = frozenset({d.debtor_id, d.creditor_id})
                if pair in locked_pairs:
                    logger.info("event=clearing.skip_locked cycle_len=%s", len(cycle))
                    try:
                        CLEARING_EVENTS_TOTAL.labels(event="execute", result="skip_locked").inc()
                    except Exception:
                        pass
                    return False
        
        # 2. Create Transaction (CLEARING)
        # We need an initiator? System or one of participants.
        # Let's pick the first debtor.
        initiator_id = debts[0].debtor_id
        
        tx_uuid = uuid.uuid4()
        tx_id_str = str(tx_uuid)
        
        new_tx = Transaction(
            id=tx_uuid,
            tx_id=tx_id_str,
            type='CLEARING',
            initiator_id=initiator_id,
            payload={
                'cycle': [str(e['debt_id']) for e in cycle],
                'amount': str(clear_amount)
            },
            state='NEW'
        )
        self.session.add(new_tx)
        
        try:
            # 3. Apply changes (Decrease debts)
            # We must lock rows? Or just update.
            # Since we are in a transaction, we should select for update ideally.
            # For MVP, we just update.
            
            for debt in debts:
                if debt.amount < clear_amount:
                    raise GeoException(f"Debt {debt.id} amount changed during clearing")

                debt.amount -= clear_amount
                self.session.add(debt)
                
            # 4. Commit
            new_tx.state = 'COMMITTED'
            self.session.add(new_tx)
            await self.session.commit()

            logger.info("event=clearing.committed tx_id=%s", tx_id_str)
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="success").inc()
            except Exception:
                pass
            return True
            
        except Exception as e:
            logger.error("event=clearing.failed error=%s", str(e))
            try:
                CLEARING_EVENTS_TOTAL.labels(event="execute", result="error").inc()
            except Exception:
                pass
            await self.session.rollback()
            # Log failed tx?
            return False

    async def auto_clear(self, equivalent_code: str) -> int:
        """
        Run clearing loop.
        Returns number of cleared cycles.
        """
        count = 0
        while True:
            cycles = await self.find_cycles(equivalent_code)
            if not cycles:
                break

            # Try cycles until one succeeds. If all candidates fail (e.g. due to locks/concurrency), stop.
            executed = False
            for cycle in cycles:
                success = await self.execute_clearing(cycle)
                if success:
                    count += 1
                    executed = True
                    break

            if not executed:
                break
                
            if count > 100: # Safety break
                break
                
        return count