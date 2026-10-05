import base64
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update

from app.db.models.auth_challenge import AuthChallenge
from app.db.models.participant import Participant
from app.core.auth.crypto import verify_signature
from app.utils.security import (
    claim_jti, decode_token, create_access_token, create_refresh_token, refresh_store_marker,
)
from app.utils.exceptions import UnauthorizedException, NotFoundException
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

        # Generate random challenge (32 bytes, base64url without padding)
        challenge_bytes = secrets.token_bytes(32)
        challenge_str = base64.urlsafe_b64encode(challenge_bytes).decode("ascii").rstrip("=")
        
        expires_at = now + timedelta(seconds=settings.AUTH_CHALLENGE_EXPIRE_SECONDS)
        
        auth_challenge = AuthChallenge(
            pid=pid,
            challenge=challenge_str,
            expires_at=expires_at,
            used=False
        )
        self.db.add(auth_challenge)
        # 2026-08-22 / p009_t905 (`F-009-6`): read the server-generated values back INSIDE
        # the transaction, so a readback failure undoes the mutation instead of reporting
        # a mutation that already happened as failed. See `RT-009-5`.
        await self.db.flush()
        await self.db.refresh(auth_challenge)
        await self.db.commit()
        return auth_challenge

    async def login(self, pid: str, challenge: str, signature: str, device_info: dict | None = None) -> dict:
        # Best-effort cleanup of expired challenges.
        now = datetime.now(timezone.utc)
        await self.db.execute(delete(AuthChallenge).where(AuthChallenge.expires_at < now))

        # 1. Find valid challenge
        stmt = select(AuthChallenge).where(
            AuthChallenge.pid == pid,
            AuthChallenge.challenge == challenge,
            AuthChallenge.used.is_(False),
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

        # 4. Use the challenge - only if nobody has since step 1 (029 `F-029-1`): two logins presenting one
        # challenge and signature at once both pass the read above; exactly one of them wins this UPDATE.
        claimed = await self.db.execute(
            update(AuthChallenge)
            .where(AuthChallenge.id == auth_challenge.id, AuthChallenge.used.is_(False))
            .values(used=True)
            .returning(AuthChallenge.id)
        )
        if claimed.scalar_one_or_none() is None:
            raise UnauthorizedException("Invalid or expired challenge")
        await self.db.commit()

        # 5. Issue tokens
        access_token = create_access_token(subject=pid)
        refresh_token = await create_refresh_token(subject=pid)

        expires_in = int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES) * 60

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "participant": {
                "pid": participant.pid,
                "display_name": participant.display_name,
                "status": participant.status,
            },
        }

    async def refresh_tokens(self, refresh_token: str) -> dict:
        payload = await decode_token(refresh_token, expected_type="refresh")
        if not payload:
            raise UnauthorizedException("Invalid refresh token")

        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            raise UnauthorizedException("Invalid refresh token")

        # 029 `F-029-31`: issued by another process, or under the other kind of revocation store - nothing here
        # can tell whether it was already used.
        if payload.get("rsm") != refresh_store_marker():
            raise UnauthorizedException("Invalid refresh token")

        pid = payload.get("sub")
        if not isinstance(pid, str) or not pid:
            raise UnauthorizedException("Invalid refresh token")

        stmt = select(Participant).where(Participant.pid == pid)
        result = await self.db.execute(stmt)
        participant = result.scalar_one_or_none()
        if not participant:
            raise UnauthorizedException("Invalid refresh token")

        # 028 `F-028-22`: the token is used once - a concurrent second use of it loses the claim.
        if not await claim_jti(jti, exp=payload.get("exp")):
            raise UnauthorizedException("Invalid refresh token")

        access_token = create_access_token(subject=pid)
        new_refresh_token = await create_refresh_token(subject=pid)

        expires_in = int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES) * 60

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "participant": {
                "pid": participant.pid,
                "display_name": participant.display_name,
                "status": participant.status,
            },
        }
