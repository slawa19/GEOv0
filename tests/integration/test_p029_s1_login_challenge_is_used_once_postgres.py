"""029 S1, F-029-1 (BACKLOG № 237): a login challenge is used once, also by two logins presenting it together.

REAL SCHEDULE on PostgreSQL, nothing injected into the driver and no stand-in session: two `AuthService.login`
calls on two real sessions of a mode-B clone, each held right after its participant read - past the `used = false`
read, before the write - until both are there. Controls: both logins were held; a single login succeeds; the used
challenge is then refused.
"""

from __future__ import annotations

import asyncio
import base64
import uuid

from nacl.signing import SigningKey
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.crypto import generate_keypair
from app.core.auth.service import AuthService
from app.db.models.participant import Participant
from app.utils.exceptions import UnauthorizedException


async def _signed_challenge(sessionmaker) -> tuple[str, str, str]:
    public_key, private_key = generate_keypair()
    pid = "p029" + uuid.uuid4().hex[:12]
    async with sessionmaker() as session:
        session.add(Participant(pid=pid, display_name="P", public_key=public_key, type="person", status="active"))
        await session.commit()
        challenge = (await AuthService(session).create_challenge(pid)).challenge
    signature = SigningKey(base64.b64decode(private_key)).sign(challenge.encode("utf-8")).signature
    return pid, challenge, base64.b64encode(signature).decode("utf-8")


async def _login(sessionmaker, pid: str, challenge: str, signature: str) -> dict | None:
    async with sessionmaker() as session:
        try:
            return await AuthService(session).login(pid, challenge, signature)
        except UnauthorizedException:
            return None


async def test_two_simultaneous_logins_with_one_challenge_yield_one_session(committed_database, monkeypatch) -> None:
    sessionmaker = committed_database.sessionmaker
    pid, challenge, signature = await _signed_challenge(sessionmaker)
    held, both_past_the_read, execute = [], asyncio.Event(), AsyncSession.execute

    async def held_after_the_participant_read(self, statement, *args, **kwargs):
        result = await execute(self, statement, *args, **kwargs)
        entities = [d.get("entity") for d in getattr(statement, "column_descriptions", [])]
        if getattr(statement, "is_select", False) and entities == [Participant]:
            held.append(self)
            if len(held) >= 2:
                both_past_the_read.set()
            await asyncio.wait_for(both_past_the_read.wait(), timeout=20)
        return result

    monkeypatch.setattr(AsyncSession, "execute", held_after_the_participant_read)
    results = await asyncio.wait_for(
        asyncio.gather(*(_login(sessionmaker, pid, challenge, signature) for _ in range(2))), timeout=60
    )
    assert len(held) == 2 and held[0] is not held[1], held  # control: both were past the read before either wrote
    assert sum(r is not None for r in results) == 1, results


async def test_a_single_login_succeeds_and_the_used_challenge_is_refused(committed_database) -> None:
    sessionmaker = committed_database.sessionmaker
    pid, challenge, signature = await _signed_challenge(sessionmaker)
    first = await _login(sessionmaker, pid, challenge, signature)
    assert first is not None and first["access_token"] and first["refresh_token"]
    assert await _login(sessionmaker, pid, challenge, signature) is None
