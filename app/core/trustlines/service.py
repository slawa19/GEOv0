from uuid import UUID
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.db.models.trustline import TrustLine
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.db.models.debt import Debt
from app.schemas.trustline import TrustLineCreateRequest, TrustLineUpdateRequest
from sqlalchemy import inspect as sa_inspect

class TrustLineService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, from_participant_id: UUID, data: TrustLineCreateRequest) -> TrustLine:
        # Check existence of 'to' participant (by PID)
        stmt = select(Participant).where(Participant.pid == data.to)
        result = await self.session.execute(stmt)
        to_participant = result.scalar_one_or_none()
        if not to_participant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recipient participant not found"
            )

        # Check if self-trust
        if from_participant_id == to_participant.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot create trustline to self",
            )

        # Check equivalent
        stmt = select(Equivalent).where(Equivalent.code == data.equivalent)
        result = await self.session.execute(stmt)
        equivalent = result.scalar_one_or_none()
        if not equivalent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Equivalent '{data.equivalent}' not found"
            )

        # Check for duplicate active trustline
        stmt = select(TrustLine).where(
            and_(
                TrustLine.from_participant_id == from_participant_id,
                TrustLine.to_participant_id == to_participant.id,
                TrustLine.equivalent_id == equivalent.id,
                TrustLine.status != 'closed'
            )
        )
        result = await self.session.execute(stmt)
        existing_trustline = result.scalar_one_or_none()
        if existing_trustline:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Active trustline already exists"
            )

        # Create TrustLine
        trustline = TrustLine(
            from_participant_id=from_participant_id,
            to_participant_id=to_participant.id,
            equivalent_id=equivalent.id,
            limit=data.limit,
            policy=data.policy or {},
            status='active'
        )
        self.session.add(trustline)

        # Ensure Debt record exists (debtor = to, creditor = from)
        # TrustLine A -> B means B can borrow from A. So B is debtor, A is creditor.
        stmt = select(Debt).where(
            and_(
                Debt.debtor_id == to_participant.id,
                Debt.creditor_id == from_participant_id,
                Debt.equivalent_id == equivalent.id
            )
        )
        result = await self.session.execute(stmt)
        debt = result.scalar_one_or_none()

        if not debt:
            debt = Debt(
                debtor_id=to_participant.id,
                creditor_id=from_participant_id,
                equivalent_id=equivalent.id,
                amount=Decimal('0')
            )
            self.session.add(debt)
        
        await self.session.commit()
        await self.session.refresh(trustline)
        
        # Hydrate extra fields for response
        return await self._hydrate_trustline(trustline)

    async def update(self, trustline_id: UUID, user_id: UUID, data: TrustLineUpdateRequest) -> TrustLine:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise HTTPException(status_code=404, detail="Trustline not found")

        if trustline.from_participant_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to update this trustline")

        if data.limit is not None:
            trustline.limit = data.limit
        
        if data.policy is not None:
            # Merge or replace policy? Usually merge or replace. Assuming replace for now or merge top level.
            # Schema says optional dict. Let's update existing dict.
            current_policy = dict(trustline.policy) if trustline.policy else {}
            current_policy.update(data.policy)
            trustline.policy = current_policy

        await self.session.commit()
        await self.session.refresh(trustline)
        return await self._hydrate_trustline(trustline)

    async def close(self, trustline_id: UUID, user_id: UUID) -> None:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise HTTPException(status_code=404, detail="Trustline not found")

        if trustline.from_participant_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to close this trustline")

        # Check debt
        used = await self._get_used_amount(trustline)
        if used > 0:
            raise HTTPException(status_code=400, detail="Cannot close trustline with non-zero debt usage")

        trustline.status = 'closed'
        await self.session.commit()

    async def get_by_participant(self, participant_id: UUID, direction: str = "outgoing") -> List[TrustLine]:
        # direction: 'outgoing' (I trust someone) or 'incoming' (someone trusts me)
        query = select(TrustLine).where(TrustLine.status == 'active')
        
        if direction == "outgoing":
            query = query.where(TrustLine.from_participant_id == participant_id)
        elif direction == "incoming":
            query = query.where(TrustLine.to_participant_id == participant_id)
        
        result = await self.session.execute(query)
        trustlines = result.scalars().all()
        
        hydrated = []
        for tl in trustlines:
            hydrated.append(await self._hydrate_trustline(tl))
        return hydrated

    async def get_one(self, trustline_id: UUID) -> TrustLine:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()
        if not trustline:
            raise HTTPException(status_code=404, detail="Trustline not found")
        return await self._hydrate_trustline(trustline)

    async def _hydrate_trustline(self, trustline: TrustLine) -> TrustLine:
        state = sa_inspect(trustline)

        # Fetch equivalent code (avoid triggering async lazy-load)
        if "equivalent" in state.unloaded or getattr(trustline, "equivalent", None) is None:
            stmt = select(Equivalent).where(Equivalent.id == trustline.equivalent_id)
            result = await self.session.execute(stmt)
            trustline.equivalent = result.scalar_one()

        # Fetch participant PIDs (avoid triggering async lazy-load)
        if "from_participant" in state.unloaded or getattr(trustline, "from_participant", None) is None:
            stmt = select(Participant).where(Participant.id == trustline.from_participant_id)
            result = await self.session.execute(stmt)
            trustline.from_participant = result.scalar_one()

        if "to_participant" in state.unloaded or getattr(trustline, "to_participant", None) is None:
            stmt = select(Participant).where(Participant.id == trustline.to_participant_id)
            result = await self.session.execute(stmt)
            trustline.to_participant = result.scalar_one()

        used = await self._get_used_amount(trustline)
        
        # Attach dynamic properties for Pydantic schema
        # Pydantic model expects: equivalent_code, used, available
        # We can attach them to the object, or return a dict, or let Pydantic extract from methods if we used getter.
        # But since we return the ORM object, we can monkey-patch or use a wrapper.
        # The schema uses aliases.
        # schema.TrustLine: equivalent_code, used, available.
        
        trustline.equivalent_code = trustline.equivalent.code
        trustline.from_pid = trustline.from_participant.pid
        trustline.to_pid = trustline.to_participant.pid
        trustline.used = used
        trustline.available = trustline.limit - used
        
        return trustline

    async def _get_used_amount(self, trustline: TrustLine) -> Decimal:
        # used = debt where debtor is 'to' and creditor is 'from'
        stmt = select(Debt.amount).where(
            and_(
                Debt.debtor_id == trustline.to_participant_id,
                Debt.creditor_id == trustline.from_participant_id,
                Debt.equivalent_id == trustline.equivalent_id
            )
        )
        result = await self.session.execute(stmt)
        amount = result.scalar_one_or_none()
        return amount if amount is not None else Decimal('0')