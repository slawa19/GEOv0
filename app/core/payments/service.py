import uuid
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import select, and_, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.db.models.transaction import Transaction
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.schemas.payment import PaymentCreateRequest, PaymentResult, PaymentDetail
from app.utils.exceptions import NotFoundException, BadRequestException, GeoException

class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.engine = PaymentEngine(session)
        self.router = PaymentRouter(session)

    async def create_payment(self, sender_id: uuid.UUID, request: PaymentCreateRequest) -> PaymentResult:
        """
        Create and execute a payment.
        1. Validate sender/receiver/equivalent.
        2. Find path.
        3. Create Transaction(NEW).
        4. Engine.prepare().
        5. Engine.commit().
        """
        amount = Decimal(str(request.amount))
        if amount <= 0:
            raise BadRequestException("Amount must be positive")

        # 1. Validation
        sender = await self.session.get(Participant, sender_id)
        if not sender:
            raise NotFoundException("Sender not found")

        receiver = (await self.session.execute(select(Participant).where(Participant.pid == request.to))).scalar_one_or_none()
        if not receiver:
            raise NotFoundException(f"Receiver {request.to} not found")

        if sender.id == receiver.id:
            raise BadRequestException("Cannot pay to yourself")

        equivalent = (await self.session.execute(select(Equivalent).where(Equivalent.code == request.equivalent))).scalar_one_or_none()
        if not equivalent:
            raise NotFoundException(f"Equivalent {request.equivalent} not found")

        # 2. Routing
        # Build graph for this equivalent
        await self.router.build_graph(equivalent.code)
        
        # Find paths
        paths = self.router.find_paths(sender.pid, receiver.pid, amount)
        if not paths:
            raise BadRequestException("No route found with sufficient capacity")
        
        # Select best path (shortest)
        # paths[0] is the shortest path list of PIDs
        best_path = paths[0]
        
        # 3. Create Transaction
        tx_uuid = uuid.uuid4()
        # Ensure tx_id is unique string.
        tx_id_str = str(tx_uuid)
        
        new_tx = Transaction(
            id=tx_uuid,
            tx_id=tx_id_str,
            type='PAYMENT',
            initiator_id=sender.id,
            payload={
                'from': sender.pid,
                'to': receiver.pid,
                'amount': str(amount),
                'equivalent': equivalent.code,
                'path': best_path
            },
            state='NEW'
        )
        self.session.add(new_tx)
        await self.session.commit()
        
        # 4. Engine Prepare
        try:
            await self.engine.prepare(tx_id_str, best_path, amount, equivalent.id)
        except Exception as e:
            # If prepare fails, we should update tx state to ABORTED or REJECTED
            # Note: prepare method might raise exception before creating locks.
            await self.session.refresh(new_tx)
            new_tx.state = 'ABORTED'
            new_tx.error = {'message': str(e)}
            await self.session.commit()
            raise BadRequestException(f"Payment preparation failed: {str(e)}")

        # 5. Engine Commit
        # In MVP we commit immediately. In real system, we might wait for receiver ACK.
        try:
            await self.engine.commit(tx_id_str)
        except Exception as e:
            # If commit fails, we try to abort (rollback locks)
            await self.engine.abort(tx_id_str, reason=f"Commit failed: {str(e)}")
            raise GeoException(f"Payment commit failed: {str(e)}")

        return PaymentResult(
            tx_id=tx_id_str,
            status="COMMITTED",
            path=best_path
        )

    async def get_payment(self, tx_id: str) -> PaymentDetail:
        stmt = select(Transaction).where(Transaction.tx_id == tx_id)
        tx = (await self.session.execute(stmt)).scalar_one_or_none()
        
        if not tx:
            raise NotFoundException(f"Payment {tx_id} not found")
            
        return PaymentDetail(
            tx_id=tx.tx_id,
            type=tx.type,
            state=tx.state,
            payload=tx.payload,
            created_at=tx.created_at,
            error=tx.error
        )

    async def list_payments(
        self, 
        participant_id: uuid.UUID, 
        limit: int = 50, 
        offset: int = 0
    ) -> List[PaymentDetail]:
        """
        List payments where participant is initiator or involved in the path.
        For MVP, mostly initiator or simplistic check.
        Ideally we check if participant is in the path in payload.
        """
        # Simple filter: initiated by user.
        # To find "involved" payments requires checking JSON payload or separate index table.
        # For MVP: just initiator.
        
        stmt = select(Transaction).where(
            and_(
                Transaction.type == 'PAYMENT',
                Transaction.initiator_id == participant_id
            )
        ).order_by(Transaction.created_at.desc()).limit(limit).offset(offset)
        
        result = await self.session.execute(stmt)
        txs = result.scalars().all()
        
        return [
            PaymentDetail(
                tx_id=tx.tx_id,
                type=tx.type,
                state=tx.state,
                payload=tx.payload,
                created_at=tx.created_at,
                error=tx.error
            )
            for tx in txs
        ]