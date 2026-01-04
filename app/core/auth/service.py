import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.db.models.auth_challenge import AuthChallenge
from app.db.models.participant import Participant
from app.core.auth.crypto import verify_signature
from app.utils.security import decode_token, create_access_token, create_refresh_token, revoke_jti
from app.utils.exceptions import BadRequestException, UnauthorizedException, NotFoundException
from app.config import settings

class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_challenge(self, pid: str) -> AuthChallenge:
        # Best-effort cleanup of expired challenges to avoid unbounded growth.
        now = datetime.now(timezone.utc)
        await self.db.execute(delete(AuthChallenge).where(AuthChallenge.expires_at < now))

        # Check if participant exists
        stmt = select(Participant).where(Participant.pid == pid)
        result = await self.db.execute(stmt)
        participant = result.scalar_one_or_none()
        
        if not participant:
            raise NotFoundException(f"Participant {pid} not found")

        # Generate random challenge
        challenge_str = str(uuid.uuid4())
        
        expires_at = now + timedelta(seconds=settings.AUTH_CHALLENGE_EXPIRE_SECONDS)
        
        auth_challenge = AuthChallenge(
            pid=pid,
            challenge=challenge_str,
            expires_at=expires_at,
            used=False
        )
        self.db.add(auth_challenge)
        await self.db.commit()
        await self.db.refresh(auth_challenge)
        return auth_challenge

    async def login(self, pid: str, challenge: str, signature: str) -> dict:
        # Best-effort cleanup of expired challenges.
        now = datetime.now(timezone.utc)
        await self.db.execute(delete(AuthChallenge).where(AuthChallenge.expires_at < now))

        # 1. Find valid challenge
        stmt = select(AuthChallenge).where(
            AuthChallenge.pid == pid,
            AuthChallenge.challenge == challenge,
            AuthChallenge.used == False,
            AuthChallenge.expires_at > now
        )
        result = await self.db.execute(stmt)
        auth_challenge = result.scalar_one_or_none()
        
        if not auth_challenge:
            raise UnauthorizedException("Invalid or expired challenge")

        # 2. Get participant public key
        stmt_p = select(Participant).where(Participant.pid == pid)
        result_p = await self.db.execute(stmt_p)
        participant = result_p.scalar_one_or_none()
        
        if not participant:
            raise UnauthorizedException("Participant not found")

        # 3. Verify signature
        try:
            # Message to verify is the challenge string
            message = challenge.encode('utf-8')
            verify_signature(participant.public_key, message, signature)
        except Exception:
            raise UnauthorizedException("Invalid signature")

        # 4. Mark challenge as used
        auth_challenge.used = True
        await self.db.commit()

        # 5. Issue tokens
        access_token = create_access_token(subject=pid)
        refresh_token = create_refresh_token(subject=pid)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer"
        }

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        payload = await decode_token(refresh_token, expected_type="refresh")
        if not payload:
            raise BadRequestException("Invalid refresh token")

        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            raise BadRequestException("Refresh token missing jti")

        await revoke_jti(jti, exp=payload.get("exp"))