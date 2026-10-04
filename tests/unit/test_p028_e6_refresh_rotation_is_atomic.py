"""028 E6, F-028-22: a refresh token is used once, even by two requests presenting it at the same time.

The revocation was checked in `decode_token` and written only after a database read, so two requests with one token
both passed and both got a new pair. REAL SCHEDULE, not injection: each request's database read waits on a barrier
until both are past the check. With Redis (a fake honouring `SET NX`) and without. Controls: a single refresh
rotates; the used token is then refused; the new one works.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.utils.security as security
from app.config import settings
from app.core.auth.service import AuthService
from app.utils.exceptions import UnauthorizedException


class _Redis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True

    async def exists(self, key):
        return int(key in self.keys)


class _Db:
    """The participant read, held until `parties` requests have reached it."""

    def __init__(self, parties: int) -> None:
        self.arrived, self.parties, self.go = 0, parties, asyncio.Event()

    async def execute(self, _stmt):
        self.arrived += 1
        if self.arrived >= self.parties:
            self.go.set()
        await asyncio.wait_for(self.go.wait(), timeout=5)
        participant = SimpleNamespace(pid="alice", display_name="Alice", status="active")
        return SimpleNamespace(scalar_one_or_none=lambda: participant)


@pytest.fixture(params=["memory", "redis"])
def store(request, monkeypatch):
    monkeypatch.setattr(security, "_revoked_jti", {})
    monkeypatch.setattr(settings, "REDIS_ENABLED", request.param == "redis", raising=False)
    monkeypatch.setattr(security, "_redis_client", _Redis() if request.param == "redis" else None)
    return request.param


async def _refresh(db, token):
    try:
        return await AuthService(db).refresh_tokens(token)
    except UnauthorizedException:
        return None


@pytest.mark.asyncio
async def test_two_simultaneous_refreshes_with_one_token_yield_one_session(store) -> None:
    token, db = security.create_refresh_token(subject="alice"), _Db(parties=2)
    results = await asyncio.gather(_refresh(db, token), _refresh(db, token))
    assert sum(r is not None for r in results) == 1, f"{store}: {results}"


@pytest.mark.asyncio
async def test_a_single_refresh_rotates_and_the_used_token_is_refused(store) -> None:
    token = security.create_refresh_token(subject="alice")
    first = await _refresh(_Db(parties=1), token)
    assert first is not None and first["refresh_token"] != token
    assert await _refresh(_Db(parties=1), token) is None
    assert await _refresh(_Db(parties=1), first["refresh_token"]) is not None
