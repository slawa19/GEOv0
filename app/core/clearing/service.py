import logging
import uuid
from decimal import Decimal
from typing import List, Dict, Optional, Set, Tuple

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.utils.exceptions import GeoException

logger = logging.getLogger(__name__)

class ClearingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_cycles(self, equivalent_code: str, max_depth: int = 6) -> List[List[Dict]]:
        """
        Find closed cycles of debts for a given equivalent.
        Returns list of cycles, where each cycle is a list of Debt objects (or dicts representing edges).
        
        Algorithm:
        1. Load all debts for this equivalent into memory (Graph).
           For MVP (small scale), this is feasible. For production, we need more optimized graph DB or targeted search.
        2. Perform DFS/BFS to find cycles.
        """
        equivalent = (await self.session.execute(select(Equivalent).where(Equivalent.code == equivalent_code))).scalar_one_or_none()
        if not equivalent:
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
        
        adjacency: Dict[uuid.UUID, List[Debt]] = {}
        for d in all_debts:
            if d.debtor_id not in adjacency:
                adjacency[d.debtor_id] = []
            adjacency[d.debtor_id].append(d)
            
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
                        'debt_id': edge.id,
                        'debtor': edge.debtor_id,
                        'creditor': edge.creditor_id,
                        'amount': edge.amount
                    })
                final_cycles.append(cycle_data)

        return final_cycles

    async def execute_clearing(self, cycle: List[Dict]) -> bool:
        """
        Execute clearing for a specific cycle.
        cycle: list of dicts {'debt_id': ..., 'amount': ...} or similar from find_cycles
        """
        if not cycle:
            return False

        # 1. Determine clearing amount (min amount in cycle)
        amounts = [Decimal(str(edge['amount'])) for edge in cycle]
        clear_amount = min(amounts)
        
        if clear_amount <= 0:
            return False
            
        logger.info(f"Executing clearing for cycle length {len(cycle)} with amount {clear_amount}")
        
        # 2. Create Transaction (CLEARING)
        # We need an initiator? System or one of participants.
        # Let's pick the first debtor.
        initiator_id = cycle[0]['debtor']
        
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
            
            for edge in cycle:
                debt_id = edge['debt_id']
                # Re-fetch debt to be sure and lock?
                stmt = select(Debt).where(Debt.id == debt_id).with_for_update()
                debt = (await self.session.execute(stmt)).scalar_one()
                
                if debt.amount < clear_amount:
                     raise GeoException(f"Debt {debt_id} amount changed during clearing")
                     
                debt.amount -= clear_amount
                self.session.add(debt)
                
            # 4. Commit
            new_tx.state = 'COMMITTED'
            self.session.add(new_tx)
            await self.session.commit()
            
            logger.info(f"Clearing {tx_id_str} committed")
            return True
            
        except Exception as e:
            logger.error(f"Clearing failed: {e}")
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
            
            # Execute first cycle
            # Optimization: Try to execute non-overlapping cycles?
            # For MVP, just one by one.
            cycle = cycles[0]
            success = await self.execute_clearing(cycle)
            
            if success:
                count += 1
            else:
                # If failed (e.g. concurrency), break or retry?
                # If we cannot clear what we found, maybe graph changed.
                # Refetch.
                # If we keep failing, break to avoid infinite loop.
                break
                
            if count > 100: # Safety break
                break
                
        return count