from __future__ import annotations

from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.models.participant import Participant
from app.schemas.participant import ParticipantCreateRequest
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
            payload["profile"] = participant_in.profile

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
            profile=participant_in.profile,
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
        return participant

    async def list_participants(
        self, 
        query: Optional[str] = None, 
        type_filter: Optional[str] = None, 
        limit: int = 20, 
        offset: int = 0
    ) -> List[Participant]:
        stmt = select(Participant)
        
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