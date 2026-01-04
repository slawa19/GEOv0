import logging
import time
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
from app.config import settings
from app.utils.observability import log_duration

logger = logging.getLogger(__name__)

_summary_cache: dict[uuid.UUID, tuple[float, BalanceSummary]] = {}

class BalanceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _get_cached_summary(self, participant_id: uuid.UUID) -> BalanceSummary | None:
        ttl = int(getattr(settings, "BALANCE_SUMMARY_CACHE_TTL_SECONDS", 0) or 0)
        if ttl <= 0:
            return None

        cached = _summary_cache.get(participant_id)
        if not cached:
            return None

        stored_at, summary = cached
        if (time.time() - stored_at) > ttl:
            _summary_cache.pop(participant_id, None)
            return None
        return summary

    def _set_cached_summary(self, participant_id: uuid.UUID, summary: BalanceSummary) -> None:
        ttl = int(getattr(settings, "BALANCE_SUMMARY_CACHE_TTL_SECONDS", 0) or 0)
        if ttl <= 0:
            return
        _summary_cache[participant_id] = (time.time(), summary)

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
        
        cached = self._get_cached_summary(participant_id)
        if cached is not None:
            logger.debug("op=balance.get_summary cache=hit participant_id=%s", participant_id)
            return cached

        with log_duration(logger, "balance.get_summary", participant_id=str(participant_id)):
            summary = await self._compute_summary(participant_id)

        self._set_cached_summary(participant_id, summary)
        return summary

    async def _compute_summary(self, participant_id: uuid.UUID) -> BalanceSummary:
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
        equivalents_map: Dict[str, Dict[str, Decimal]] = {}

        def get_eq_entry(code: str) -> Dict[str, Decimal]:
            if code not in equivalents_map:
                equivalents_map[code] = {
                    'total_debt': Decimal('0'),
                    'total_credit': Decimal('0'),
                    'spend_capacity': Decimal('0'),
                    'receive_capacity': Decimal('0'),
                }
            return equivalents_map[code]

        # Debts I Owe: key=(creditor_id, code), value=amount
        debts_i_owe: Dict[tuple[uuid.UUID, str], Decimal] = {}
        for d in my_debts:
            code = d.equivalent.code
            debts_i_owe[(d.creditor_id, code)] = d.amount
            get_eq_entry(code)['total_debt'] += d.amount

        # Debts Others Owe Me: key=(debtor_id, code), value=amount
        debts_others_owe: Dict[tuple[uuid.UUID, str], Decimal] = {}
        for d in others_debts:
            code = d.equivalent.code
            debts_others_owe[(d.debtor_id, code)] = d.amount
            get_eq_entry(code)['total_credit'] += d.amount

        # IMPORTANT: Payment routing semantics in this project treat an edge Me->Peer as enabled by the trustline
        # Peer->Me (Peer trusts Me to owe). Therefore:
        #   spend_capacity(Me->Peer) = Limit(Peer->Me) - Debt(Me->Peer) + Debt(Peer->Me)
        # and
        #   receive_capacity(Peer->Me) = Limit(Me->Peer) - Debt(Peer->Me) + Debt(Me->Peer)

        # Spend capacity comes from Incoming TrustLines (Peer -> Me)
        for tl in in_tls:
            code = tl.equivalent.code
            limit = tl.limit
            peer_id = tl.from_participant_id

            d_me_peer = debts_i_owe.get((peer_id, code), Decimal('0'))
            d_peer_me = debts_others_owe.get((peer_id, code), Decimal('0'))

            capacity = limit - d_me_peer + d_peer_me
            get_eq_entry(code)['spend_capacity'] += capacity

        # Receive capacity comes from Outgoing TrustLines (Me -> Peer)
        for tl in out_tls:
            code = tl.equivalent.code
            limit = tl.limit
            peer_id = tl.to_participant_id

            d_peer_me = debts_others_owe.get((peer_id, code), Decimal('0'))
            d_me_peer = debts_i_owe.get((peer_id, code), Decimal('0'))

            capacity = limit - d_peer_me + d_me_peer
            get_eq_entry(code)['receive_capacity'] += capacity

        # Debts without trustlines still contribute positive capacity with Limit=0.
        all_peers_equivalents = set(debts_i_owe.keys()) | set(debts_others_owe.keys())

        processed_spend = set((tl.from_participant_id, tl.equivalent.code) for tl in in_tls)
        processed_receive = set((tl.to_participant_id, tl.equivalent.code) for tl in out_tls)

        for (peer, code) in all_peers_equivalents:
            if (peer, code) not in processed_spend:
                d_me_peer = debts_i_owe.get((peer, code), Decimal('0'))
                d_peer_me = debts_others_owe.get((peer, code), Decimal('0'))
                cap = Decimal('0') - d_me_peer + d_peer_me
                if cap > 0:
                    get_eq_entry(code)['spend_capacity'] += cap

            if (peer, code) not in processed_receive:
                d_peer_me = debts_others_owe.get((peer, code), Decimal('0'))
                d_me_peer = debts_i_owe.get((peer, code), Decimal('0'))
                cap = Decimal('0') - d_peer_me + d_me_peer
                if cap > 0:
                    get_eq_entry(code)['receive_capacity'] += cap

        results: list[BalanceEquivalent] = []
        for code, data in equivalents_map.items():
            results.append(
                BalanceEquivalent(
                    code=code,
                    total_debt=str(data['total_debt']),
                    total_credit=str(data['total_credit']),
                    net_balance=str(data['total_credit'] - data['total_debt']),
                    available_to_spend=str(max(Decimal('0'), data['spend_capacity'])),
                    available_to_receive=str(max(Decimal('0'), data['receive_capacity'])),
                )
            )

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