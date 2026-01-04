import uuid
from decimal import Decimal
from typing import List, Dict, Optional

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.schemas.balance import BalanceSummary, BalanceEquivalent, DebtsDetails, OutgoingDebt, IncomingDebt
from app.utils.exceptions import NotFoundException

class BalanceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_summary(self, participant_id: uuid.UUID) -> BalanceSummary:
        """
        Get aggregated balance by equivalents for a participant.
        Balance = (Total Credits) - (Total Debts)
        Available to Spend = Sum(TrustLine Limits from Me to Others) + Sum(Debts Others owe Me) - Sum(Debts I owe Others)
          Wait, simplified:
          Available to Spend on a TrustLine = Limit - Debt(Me->Other) + Debt(Other->Me).
          But limits are per-trustline. 
          Aggregated available is tricky because it depends on connectivity. 
          But here we likely mean "Direct Liquidity".
          
          Let's follow the schema definition:
          - total_debt: Sum of all debts I owe.
          - total_credit: Sum of all debts others owe me.
          - net_balance: total_credit - total_debt.
          - available_to_spend: Sum of (Limit(Me->Neighbor) - Used(Me->Neighbor) + Debt(Neighbor->Me)).
             Actually: Capacity on each link (Me->Neighbor).
             Capacity = Limit(Me->N) - Debt(Me->N) + Debt(N->Me).
             Wait, if I owe N, I consume my limit.
             Correct formula for capacity Me->N:
               Limit(Me->N) - Debt(Me->N) + Debt(N->Me)
             So "Available to spend" is sum of capacities of all outgoing links.
             
          - available_to_receive: Sum of capacities of all incoming links (N->Me).
             Capacity N->Me = Limit(N->Me) - Debt(N->Me) + Debt(Me->N).
        """
        
        # 1. Fetch all equivalents involved (via Debts or TrustLines)
        # It's better to fetch all TrustLines and Debts for this user.
        
        # Fetch Outgoing Trustlines (Me -> Others)
        out_tl_stmt = select(TrustLine).options(joinedload(TrustLine.equivalent)).where(
            and_(TrustLine.from_participant_id == participant_id, TrustLine.status == 'active')
        )
        out_tls = (await self.session.execute(out_tl_stmt)).scalars().all()
        
        # Fetch Incoming Trustlines (Others -> Me)
        in_tl_stmt = select(TrustLine).options(joinedload(TrustLine.equivalent)).where(
            and_(TrustLine.to_participant_id == participant_id, TrustLine.status == 'active')
        )
        in_tls = (await self.session.execute(in_tl_stmt)).scalars().all()
        
        # Fetch Debts I owe (Me -> Others)
        my_debts_stmt = select(Debt).options(joinedload(Debt.equivalent)).where(Debt.debtor_id == participant_id)
        my_debts = (await self.session.execute(my_debts_stmt)).scalars().all()
        
        # Fetch Debts others owe Me (Others -> Me)
        others_debts_stmt = select(Debt).options(joinedload(Debt.equivalent)).where(Debt.creditor_id == participant_id)
        others_debts = (await self.session.execute(others_debts_stmt)).scalars().all()
        
        # Group by Equivalent
        equivalents_map: Dict[str, Dict] = {}
        
        def get_eq_entry(code: str):
            if code not in equivalents_map:
                equivalents_map[code] = {
                    'total_debt': Decimal('0'),
                    'total_credit': Decimal('0'),
                    'spend_capacity': Decimal('0'),
                    'receive_capacity': Decimal('0')
                }
            return equivalents_map[code]

        # Process Debts I owe (increases total_debt, decreases spend_capacity on that link, increases receive_capacity on that link)
        # Wait, let's use the Link-based logic for capacities.
        
        # We need to map debts to links (Participants + Equivalent).
        # Key: (peer_id, equivalent_code)
        
        # Debts I Owe: key=(creditor_id, code), value=amount
        debts_i_owe = {} 
        for d in my_debts:
            code = d.equivalent.code
            debts_i_owe[(d.creditor_id, code)] = d.amount
            get_eq_entry(code)['total_debt'] += d.amount
            
        # Debts Others Owe Me: key=(debtor_id, code), value=amount
        debts_others_owe = {}
        for d in others_debts:
            code = d.equivalent.code
            debts_others_owe[(d.debtor_id, code)] = d.amount
            get_eq_entry(code)['total_credit'] += d.amount

        # Process Outgoing TrustLines (Me -> N)
        # Contributes to Spend Capacity.
        # Cap(Me->N) = Limit(Me->N) - Debt(Me->N) + Debt(N->Me)
        # Note: Debt(Me->N) might not exist if 0.
        # Note: Debt(N->Me) might exist even if no trustline N->Me, but for capacity calculation on link Me->N it matters?
        # Actually, "Available to Spend" usually means "How much I can send to my neighbors".
        # Yes.
        
        for tl in out_tls:
            code = tl.equivalent.code
            limit = tl.limit
            peer_id = tl.to_participant_id
            
            d_me_n = debts_i_owe.get((peer_id, code), Decimal('0'))
            d_n_me = debts_others_owe.get((peer_id, code), Decimal('0'))
            
            capacity = limit - d_me_n + d_n_me
            # Note: d_n_me increases my ability to send to N (clearing debt).
            
            get_eq_entry(code)['spend_capacity'] += capacity
            
        # Process Incoming TrustLines (N -> Me)
        # Contributes to Receive Capacity.
        # Cap(N->Me) = Limit(N->Me) - Debt(N->Me) + Debt(Me->N)
        for tl in in_tls:
            code = tl.equivalent.code
            limit = tl.limit
            peer_id = tl.from_participant_id
            
            d_n_me = debts_others_owe.get((peer_id, code), Decimal('0'))
            d_me_n = debts_i_owe.get((peer_id, code), Decimal('0'))
            
            capacity = limit - d_n_me + d_me_n
            
            get_eq_entry(code)['receive_capacity'] += capacity

        # What about debts without trustlines? (e.g. TL closed but debt remains)
        # If TL is missing, Limit is 0.
        # Capacity logic still holds with Limit=0.
        # We need to iterate over peers that have debts but NO trustlines to account for "clearing capacity".
        # If N owes Me 50, and I have no TL to N (Limit=0).
        # Spend Cap = 0 - 0 + 50 = 50. I can spend 50 to N (clearing debt).
        # We need to ensure we count this.
        
        all_peers_equivalents = set()
        for (peer, code) in debts_i_owe.keys():
            all_peers_equivalents.add((peer, code))
        for (peer, code) in debts_others_owe.keys():
            all_peers_equivalents.add((peer, code))
            
        # We already processed TLs. Now check if we missed any debt-only links.
        # Optimization: Build a set of processed (peer, code) from TL loops?
        
        processed_out = set((tl.to_participant_id, tl.equivalent.code) for tl in out_tls)
        processed_in = set((tl.from_participant_id, tl.equivalent.code) for tl in in_tls)
        
        # Check "Spend Capacity" for peers where I have no Outgoing TL, but they owe me or I owe them?
        # If I owe them (d_me_n), it consumes capacity (negative contribution if limit=0)? 
        # No, capacity cannot be negative. min is 0?
        # Wait, if Limit=0 and I owe 10, Capacity = -10? No.
        # Engine check: available = limit + r_owes_s - s_owes_r.
        # If < 0, it means we are over limit?
        # But here we just sum up positive capacities.
        
        for (peer, code) in all_peers_equivalents:
            # Check Spend side (Me->Peer)
            if (peer, code) not in processed_out:
                # No TL Me->Peer. Limit=0.
                d_me_n = debts_i_owe.get((peer, code), Decimal('0'))
                d_n_me = debts_others_owe.get((peer, code), Decimal('0'))
                cap = Decimal('0') - d_me_n + d_n_me
                if cap > 0:
                    get_eq_entry(code)['spend_capacity'] += cap
            
            # Check Receive side (Peer->Me)
            if (peer, code) not in processed_in:
                # No TL Peer->Me. Limit=0.
                d_n_me = debts_others_owe.get((peer, code), Decimal('0'))
                d_me_n = debts_i_owe.get((peer, code), Decimal('0'))
                cap = Decimal('0') - d_n_me + d_me_n
                if cap > 0:
                    get_eq_entry(code)['receive_capacity'] += cap

        results = []
        for code, data in equivalents_map.items():
            results.append(BalanceEquivalent(
                code=code,
                total_debt=str(data['total_debt']),
                total_credit=str(data['total_credit']),
                net_balance=str(data['total_credit'] - data['total_debt']),
                available_to_spend=str(max(Decimal('0'), data['spend_capacity'])),
                available_to_receive=str(max(Decimal('0'), data['receive_capacity']))
            ))
            
        return BalanceSummary(equivalents=results)

    async def get_debts(self, participant_id: uuid.UUID, equivalent_code: str, direction: str = 'all') -> DebtsDetails:
        """
        Get detailed debts for a specific equivalent.
        """
        equivalent = (await self.session.execute(select(Equivalent).where(Equivalent.code == equivalent_code))).scalar_one_or_none()
        if not equivalent:
            raise NotFoundException(f"Equivalent {equivalent_code} not found")
            
        outgoing_res = []
        incoming_res = []
        
        if direction in ['outgoing', 'all']:
            # Debts I owe (Me is Debtor)
            stmt = select(Debt).options(joinedload(Debt.creditor)).where(
                and_(
                    Debt.debtor_id == participant_id,
                    Debt.equivalent_id == equivalent.id,
                    Debt.amount > 0
                )
            )
            debts = (await self.session.execute(stmt)).scalars().all()
            for d in debts:
                outgoing_res.append(OutgoingDebt(
                    creditor=d.creditor.pid,
                    creditor_name=d.creditor.display_name or "",
                    equivalent=equivalent_code,
                    amount=str(d.amount)
                ))
                
        if direction in ['incoming', 'all']:
            # Debts others owe Me (Me is Creditor)
            stmt = select(Debt).options(joinedload(Debt.debtor)).where(
                and_(
                    Debt.creditor_id == participant_id,
                    Debt.equivalent_id == equivalent.id,
                    Debt.amount > 0
                )
            )
            debts = (await self.session.execute(stmt)).scalars().all()
            for d in debts:
                incoming_res.append(IncomingDebt(
                    debtor=d.debtor.pid,
                    debtor_name=d.debtor.display_name or "",
                    equivalent=equivalent_code,
                    amount=str(d.amount)
                ))
                
        return DebtsDetails(outgoing=outgoing_res, incoming=incoming_res)