import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Dict, Tuple
from uuid import UUID

from sqlalchemy import select, and_, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models.prepare_lock import PrepareLock
from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.utils.exceptions import GeoException, ConflictException

logger = logging.getLogger(__name__)

class PaymentEngine:
    def __init__(self, session: AsyncSession):
        self.session = session
        from app.config import settings

        self.lock_ttl_seconds = settings.PREPARE_LOCK_TTL_SECONDS

    async def prepare(self, tx_id: str, path: List[str], amount: Decimal, equivalent_id: UUID):
        """
        Phase 1: Prepare
        Create locks on all segments of the path.
        Checks capacity and ensures no double spending (via locks).
        
        path: List of PIDs, e.g., ['A', 'B', 'C']
        """
        logger.info(f"Preparing payment {tx_id} along path {path} for {amount}")
        
        # 1. Resolve PIDs to UUIDs
        pids = set(path)
        stmt = select(Participant).where(Participant.pid.in_(pids))
        result = await self.session.execute(stmt)
        participants = {p.pid: p for p in result.scalars().all()}
        
        if len(participants) != len(pids):
            missing = pids - set(participants.keys())
            raise GeoException(f"Participants not found: {missing}")

        participant_map = {pid: p.id for pid, p in participants.items()}

        # 2. Iterate through path segments
        locks_to_create = []
        
        # We need to lock resources. In MVP, we use PrepareLock table.
        # Check if locks already exist for this tx_id (idempotency or error)
        stmt = select(PrepareLock).where(PrepareLock.tx_id == tx_id)
        result = await self.session.execute(stmt)
        existing_locks = result.scalars().all()
        if existing_locks:
            raise ConflictException(f"Transaction {tx_id} already prepared/processing")

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.lock_ttl_seconds)

        for i in range(len(path) - 1):
            sender_pid = path[i]
            receiver_pid = path[i+1]
            
            sender_id = participant_map[sender_pid]
            receiver_id = participant_map[receiver_pid]

            # 3. Check capacity and existing locks
            # We need to know current debt and limit
            # Direction: sender -> receiver
            # If sender owes receiver: pay back (reduce debt) -> capacity is debt amount
            # If receiver owes sender: lend more (increase debt) -> capacity is limit - debt
            # Actually, we simplify:
            # We check "Available Capacity" from Sender to Receiver.
            #   Capacity = (TrustLine Limit from Sender to Receiver) - (Balance Sender owes Receiver)
            #   Note: Balance can be negative if Receiver owes Sender.
            #   Wait, let's stick to the Debt model:
            #   Debt(debtor, creditor, amount)
            
            # For payment flow Sender -> Receiver, capacity is controlled by TrustLine (Receiver -> Sender)
            tl_stmt = select(TrustLine).where(
                and_(
                    TrustLine.from_participant_id == receiver_id,
                    TrustLine.to_participant_id == sender_id,
                    TrustLine.equivalent_id == equivalent_id,
                    TrustLine.status == 'active'
                )
            )
            tl_res = await self.session.execute(tl_stmt)
            trustline = tl_res.scalar_one_or_none()
            
            limit = trustline.limit if trustline else Decimal('0')
            if not trustline:
                # Check if reverse trustline exists? No, for "ripple" like payments, trustline is usually needed.
                # However, if Receiver Owes Sender, we might not need Sender -> Receiver trustline to pay back.
                # But typically trustlines are bidirectional or we check both.
                # For MVP, assume capacity check was done by Router, but we must verify strictly here.
                # If no trustline sender->receiver, capacity is limited to what Receiver owes Sender.
                pass

            # Fetch Debts
            # Case A: Receiver owes Sender (Debt: debtor=receiver, creditor=sender)
            # We can "pay" by reducing this debt.
            debt_r_s_stmt = select(Debt).where(
                and_(
                    Debt.debtor_id == receiver_id,
                    Debt.creditor_id == sender_id,
                    Debt.equivalent_id == equivalent_id
                )
            )
            debt_r_s = (await self.session.execute(debt_r_s_stmt)).scalar_one_or_none()
            amount_r_owes_s = debt_r_s.amount if debt_r_s else Decimal('0')

            # Case B: Sender owes Receiver (Debt: debtor=sender, creditor=receiver)
            # We can "pay" by increasing this debt, up to Limit.
            debt_s_r_stmt = select(Debt).where(
                and_(
                    Debt.debtor_id == sender_id,
                    Debt.creditor_id == receiver_id,
                    Debt.equivalent_id == equivalent_id
                )
            )
            debt_s_r = (await self.session.execute(debt_s_r_stmt)).scalar_one_or_none()
            amount_s_owes_r = debt_s_r.amount if debt_s_r else Decimal('0')

            # Calculate Net Balance from Sender perspective
            # Balance = (Receiver owes Sender) - (Sender owes Receiver)
            # If Balance > 0, Sender has credit.
            # If Balance < 0, Sender has debt.
            # Available to Send = (Receiver owes Sender) + (Limit S->R - Sender owes Receiver)
            # Wait, standard ripple logic:
            #   If R owes S 100, S can send 100 to R (clearing debt).
            #   If S has limit 50 to R, S can send 50 to R (creating debt).
            #   Total capacity = 100 + 50 = 150.
            
            # Check Pending Locks impacting this link
            # We need to sum up locks for (Sender, Receiver) link.
            # Locks store "effects".
            # Effect: { "change": "decrease_debt", "debtor": ..., "creditor": ..., "amount": ... }
            # Or simplified: We just lock the participants and re-check.
            # But concurrency requires accounting for other pending locks.
            
            # For MVP simplicity: We can query all active locks for these participants
            # and subtract/add to available capacity.
            # This is "Pessimistic Locking" or "Accounting for Pending".
            
            # Let's verify capacity INCLUDING pending locks.
            
            # Pending outgoing from Sender to Receiver?
            # Pending incoming from Receiver to Sender?
            
            # Let's simplify for MVP:
            # We assume single-threaded per user or use DB locks?
            # No, we use PrepareLock table to "reserve" capacity.
            
            # We need to calculate "Reserved" amounts on this link.
            # SELECT sum(amount) from PrepareLock where ... is hard because structure is JSON.
            # Let's just create the Lock and trust the router? NO. Engine must enforce.
            
            # For MVP, let's implement strict check WITHOUT complex lock aggregation query if possible,
            # or fetch all locks involving these users.
            
            # Optimization: If we trust the Router's recent check, we risk race conditions.
            # Proper way: Select for Update on Debt rows?
            # But we are creating locks, not updating debt yet.
            
            # Let's fetch ALL locks for Sender and Receiver to calculate "Effective Balance".
            # This might be heavy if many pending txs.
            
            # Alternative: Add `pending_debt` column to Debt/Trustline? No.
            
            # Let's try simple "Select For Update" on Debt/Trustline to ensure serialization?
            # But Prepare is long-lived? No, Prepare is quick, but the lock stays until Commit.
            # So we cannot hold DB transaction open.
            
            # Solution for MVP:
            # 1. Fetch current debts.
            # 2. Fetch all valid PrepareLocks involving (Sender, Receiver).
            # 3. Calculate "Effective Available Capacity".
            # 4. If enough, Insert new Lock.
            
            locks_query = select(PrepareLock).where(
                and_(
                    PrepareLock.participant_id.in_([sender_id, receiver_id]),
                    PrepareLock.expires_at > func.now()
                )
            )
            # Wait, fetching by participant is broad.
            # The Lock should explicitly state the link?
            # The PrepareLock structure in `app/db/models/prepare_lock.py` has `effects` JSON.
            # effects = [{"from": s, "to": r, "amount": ...}]
            
            # This is getting complex for MVP JSON parsing in SQL.
            # Let's try to trust the router BUT handle failure at Commit?
            # No, Prepare MUST reserve.
            
            # Simplified approach for MVP:
            # We store `reserved_amount` in the Lock record in a structured way if possible, or we iterate python-side.
            # Since scale is small, Python iteration over active locks for these 2 users is fine.
            
            relevant_locks = (await self.session.execute(locks_query)).scalars().all()
            
            reserved_usage = Decimal('0')
            for lock in relevant_locks:
                # Parse lock effects
                # Effect format: {'diffs': [{'debtor':..., 'creditor':..., 'amount':...}]}
                # We need to know if this lock consumes the SAME capacity direction.
                
                # If lock is sending S -> R: it consumes S->R capacity.
                # If lock is sending R -> S: it releases S->R capacity (technically), but we shouldn't rely on pending incoming funds.
                
                effects = lock.effects.get('diffs', [])
                for eff in effects:
                    # If this effect increases S->R debt or decreases R->S debt
                    d_id = UUID(eff['debtor'])
                    c_id = UUID(eff['creditor'])
                    amt = Decimal(eff['amount']) # absolute change
                    
                    # If effect is "Increase Debt of S to R"
                    if d_id == sender_id and c_id == receiver_id:
                         reserved_usage += amt
                    
                    # If effect is "Decrease Debt of R to S" (which consumes the 'credit' S has with R)
                    # Wait, decreasing debt is also consuming capacity if we are using that debt to pay.
                    if d_id == receiver_id and c_id == sender_id:
                        # Wait, `amount` in effect is typically positive delta?
                        # Let's define effect clearly.
                        # `amount` is the flow amount.
                        # Flow S -> R:
                        #   1. Reduce R's debt to S (if any).
                        #   2. Increase S's debt to R (if needed).
                        pass

            # Let's define the lock effect for THIS transaction segment S->R
            # The lock should say: "S sends `amount` to R"
            # We will compute the exact debt updates at Commit, but for reservation we just need "Amount flowing S->R".
            
            # Re-calculate reserved usage based on "Flow S->R"
            # Any lock sending S->R consumes capacity.
            for lock in relevant_locks:
                # We need to know flow direction from lock.
                # Let's assume lock.effects contains 'flows': [{'from': s, 'to': r, 'amount': ...}]
                flows = lock.effects.get('flows', [])
                for flow in flows:
                    f_s = UUID(flow['from'])
                    f_r = UUID(flow['to'])
                    f_a = Decimal(flow['amount'])
                    
                    if f_s == sender_id and f_r == receiver_id:
                        reserved_usage += f_a
            
            # Now Check Capacity
            # Available capacity for Sender -> Receiver:
            # Limit comes from TrustLine(Receiver -> Sender)
            # Available = Limit - Debt(Sender owes Receiver) + Debt(Receiver owes Sender)
            available_capacity = limit - amount_s_owes_r + amount_r_owes_s
            
            if available_capacity < (amount + reserved_usage):
                raise GeoException(
                    f"Insufficient capacity between {sender_pid} and {receiver_pid}. "
                    f"Available: {available_capacity}, Needed: {amount}, Reserved: {reserved_usage}"
                )

            # Create Lock for this participant (Sender)? 
            # Or Lock for the Link?
            # The `PrepareLock` table has `participant_id`. 
            # We should probably lock the sender of the flow on this link?
            # Or we create a lock entry that describes the flow.
            
            # We'll create a lock for the Sender of this segment.
            lock = PrepareLock(
                tx_id=tx_id,
                participant_id=sender_id,
                effects={
                    'flows': [{
                        'from': str(sender_id),
                        'to': str(receiver_id),
                        'amount': str(amount),
                        'equivalent': str(equivalent_id)
                    }]
                },
                expires_at=expires_at
            )
            locks_to_create.append(lock)

        # 4. Save all locks
        self.session.add_all(locks_to_create)
        # Update transaction state to PREPARED (or PREPARE_IN_PROGRESS then PREPARED)
        # We assume caller manages Transaction object state, but Engine helps.
        
        # Let's verify Transaction exists?
        # stmt = select(Transaction).where(Transaction.tx_id == tx_id)
        # ...
        
        # For MVP, we assume caller created Transaction('NEW') and calls prepare.
        # We update it to PREPARED.
        stmt = update(Transaction).where(Transaction.tx_id == tx_id).values(state='PREPARED')
        await self.session.execute(stmt)
        
        await self.session.commit()
        logger.info(f"Payment {tx_id} prepared successfully")
        return True

    async def commit(self, tx_id: str):
        """
        Phase 2: Commit
        Apply changes to Debt/TrustLine based on locks.
        Remove locks.
        Update Transaction to COMMITTED.
        """
        logger.info(f"Committing payment {tx_id}")
        
        # 1. Load Locks
        stmt = select(PrepareLock).where(PrepareLock.tx_id == tx_id)
        result = await self.session.execute(stmt)
        locks = result.scalars().all()
        
        if not locks:
            # Maybe already committed?
            # Check transaction state
            tx = (await self.session.execute(select(Transaction).where(Transaction.tx_id == tx_id))).scalar_one_or_none()
            if tx and tx.state == 'COMMITTED':
                return True
            raise GeoException(f"No locks found for transaction {tx_id}")

        # 2. Process each lock (segment)
        for lock in locks:
            flows = lock.effects.get('flows', [])
            for flow in flows:
                from_id = UUID(flow['from'])
                to_id = UUID(flow['to'])
                amount = Decimal(flow['amount'])
                equivalent_id = UUID(flow['equivalent'])
                
                await self._apply_flow(from_id, to_id, amount, equivalent_id)

        # 3. Delete Locks
        delete_stmt = delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
        await self.session.execute(delete_stmt)
        
        # 4. Update Transaction
        update_stmt = update(Transaction).where(Transaction.tx_id == tx_id).values(state='COMMITTED')
        await self.session.execute(update_stmt)
        
        await self.session.commit()
        logger.info(f"Payment {tx_id} committed")
        return True

    async def _apply_flow(self, from_id: UUID, to_id: UUID, amount: Decimal, equivalent_id: UUID):
        """
        Apply flow of `amount` from `from_id` to `to_id`.
        Logic:
          1. If receiver owes sender: reduce that debt.
          2. If remaining amount > 0: increase sender's debt to receiver.
        """
        remaining_amount = amount

        # 1. Check if Receiver owes Sender (Debt: debtor=to, creditor=from)
        debt_r_s = await self._get_debt(to_id, from_id, equivalent_id)
        
        if debt_r_s and debt_r_s.amount > 0:
            reduction = min(remaining_amount, debt_r_s.amount)
            debt_r_s.amount -= reduction
            remaining_amount -= reduction
            self.session.add(debt_r_s)
            
            if debt_r_s.amount == 0:
                 # Optional: Delete debt row if 0?
                 # Keep it for history or delete? 
                 # Usually keep, or delete to save space. 
                 # Let's keep for MVP.
                 pass

        if remaining_amount > 0:
            # 2. Increase Sender's debt to Receiver (Debt: debtor=from, creditor=to)
            debt_s_r = await self._get_debt(from_id, to_id, equivalent_id)
            if not debt_s_r:
                # Create new debt record
                debt_s_r = Debt(
                    debtor_id=from_id,
                    creditor_id=to_id,
                    equivalent_id=equivalent_id,
                    amount=Decimal('0')
                )
            
            debt_s_r.amount += remaining_amount
            self.session.add(debt_s_r)

    async def _get_debt(self, debtor_id: UUID, creditor_id: UUID, equivalent_id: UUID) -> Debt | None:
        stmt = select(Debt).where(
            and_(
                Debt.debtor_id == debtor_id,
                Debt.creditor_id == creditor_id,
                Debt.equivalent_id == equivalent_id
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def abort(self, tx_id: str, reason: str = "Aborted"):
        """
        Abort transaction: Delete locks, set state to ABORTED.
        """
        logger.info(f"Aborting payment {tx_id}: {reason}")
        
        # Delete locks
        delete_stmt = delete(PrepareLock).where(PrepareLock.tx_id == tx_id)
        await self.session.execute(delete_stmt)
        
        # Update Transaction
        update_stmt = update(Transaction).where(Transaction.tx_id == tx_id).values(
            state='ABORTED',
            error={'message': reason}
        )
        await self.session.execute(update_stmt)
        
        await self.session.commit()
        return True
    