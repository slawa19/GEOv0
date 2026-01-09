from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.core.balance.service import BalanceService
from app.schemas.participant import ParticipantCreateRequest
from app.schemas.participant import ParticipantStats, ParticipantUpdateRequest
from app.core.auth.crypto import get_pid_from_public_key, verify_signature
from app.core.auth.canonical import canonical_json
from app.utils.exceptions import ConflictException, NotFoundException, BadRequestException, InvalidSignatureException

class ParticipantService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_participant(self, participant_in: ParticipantCreateRequest) -> Participant:
        # 1. Check if public key is valid and derive PID
        try:
            pid = get_pid_from_public_key(participant_in.public_key)
        except Exception:
            raise BadRequestException("Invalid public key")

        # 2. Check if participant already exists (by derived PID or by public_key)
        result = await self.db.execute(
            select(Participant).where(
                or_(
                    Participant.pid == pid,
                    Participant.public_key == participant_in.public_key,
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise ConflictException("Participant already exists")

        # 3. Verify signature (proof-of-possession + binding of key to identity fields)
        payload: dict = {
            "display_name": participant_in.display_name,
            "type": participant_in.type,
            "public_key": participant_in.public_key,
        }
        if participant_in.profile is not None:
            payload["profile"] = participant_in.profile.model_dump(exclude_unset=True)

        message = canonical_json(payload)
        try:
            verify_signature(participant_in.public_key, message, participant_in.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        # 4. Create participant
        participant = Participant(
            pid=pid,
            display_name=participant_in.display_name,
            public_key=participant_in.public_key,
            type=participant_in.type,
            profile=(participant_in.profile.model_dump(exclude_unset=True) if participant_in.profile is not None else None),
            status='active',
            verification_level=0
        )
        self.db.add(participant)
        try:
            await self.db.commit()
        except IntegrityError:
            # Covers race conditions against unique constraints (pid/public_key).
            await self.db.rollback()
            raise ConflictException("Participant already exists")
        await self.db.refresh(participant)
        return participant

    async def get_participant(self, pid: str) -> Participant:
        result = await self.db.execute(select(Participant).where(Participant.pid == pid))
        participant = result.scalar_one_or_none()
        if not participant:
            raise NotFoundException(f"Participant {pid} not found")

        incoming_sum = (
            await self.db.execute(
                select(func.coalesce(func.sum(TrustLine.limit), 0)).where(
                    TrustLine.to_participant_id == participant.id,
                    TrustLine.status == "active",
                )
            )
        ).scalar_one()
        participant.public_stats = {
            "total_incoming_trust": self._fmt_amount(Decimal(str(incoming_sum or 0))),
            "member_since": participant.created_at,
        }
        return participant

    def _fmt_amount(self, value: Decimal) -> str:
        try:
            return str(value.quantize(Decimal("0.00")))
        except Exception:
            return str(value)

    async def get_participant_stats(self, participant_id) -> ParticipantStats:
        outgoing_sum = (
            await self.db.execute(
                select(func.coalesce(func.sum(TrustLine.limit), 0)).where(
                    TrustLine.from_participant_id == participant_id,
                    TrustLine.status == "active",
                )
            )
        ).scalar_one()

        incoming_sum = (
            await self.db.execute(
                select(func.coalesce(func.sum(TrustLine.limit), 0)).where(
                    TrustLine.to_participant_id == participant_id,
                    TrustLine.status == "active",
                )
            )
        ).scalar_one()

        total_outgoing_trust = Decimal(str(outgoing_sum or 0))
        total_incoming_trust = Decimal(str(incoming_sum or 0))

        balance = await BalanceService(self.db).get_summary(participant_id)
        total_debt = Decimal("0")
        total_credit = Decimal("0")
        for eq in balance.equivalents:
            total_debt += Decimal(str(eq.total_debt))
            total_credit += Decimal(str(eq.total_credit))

        net_balance = total_credit - total_debt

        return ParticipantStats(
            total_incoming_trust=self._fmt_amount(total_incoming_trust),
            total_outgoing_trust=self._fmt_amount(total_outgoing_trust),
            total_debt=self._fmt_amount(total_debt),
            total_credit=self._fmt_amount(total_credit),
            net_balance=self._fmt_amount(net_balance),
        )

    async def update_participant(self, participant_id, data: ParticipantUpdateRequest) -> Participant:
        participant = await self.db.get(Participant, participant_id)
        if not participant:
            raise NotFoundException("Participant not found")

        signed_payload: dict = {}
        if data.display_name is not None:
            signed_payload["display_name"] = data.display_name
        if data.profile is not None:
            signed_payload["profile"] = data.profile.model_dump(exclude_unset=True)

        if not signed_payload:
            raise BadRequestException("No changes provided")

        try:
            verify_signature(participant.public_key, canonical_json(signed_payload), data.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        if data.display_name is not None:
            participant.display_name = data.display_name

        if data.profile is not None:
            current_profile = dict(participant.profile) if participant.profile else {}
            current_profile.update(data.profile.model_dump(exclude_unset=True))
            participant.profile = current_profile

        await self.db.commit()
        await self.db.refresh(participant)
        return participant

    async def list_participants(
        self, 
        query: Optional[str] = None, 
        type_filter: Optional[str] = None, 
        limit: int = 20, 
        offset: int = 0
    ) -> List[Participant]:
        stmt = select(Participant).order_by(Participant.id.asc())
        
        if query:
            stmt = stmt.where(or_(
                Participant.display_name.ilike(f"%{query}%"),
                Participant.pid.ilike(f"%{query}%")
            ))
        
        if type_filter:
            stmt = stmt.where(Participant.type == type_filter)
            
        stmt = stmt.limit(limit).offset(offset)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())