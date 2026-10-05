"""029 S1, F-029-31 (BACKLOG № 240) and F-029-1 (№ 286): a used refresh token stays used across a restart.

Without Redis the revocation store is process memory. THE RESTART IS A REAL NEW PROCESS (`python -c`, the same
`JWT_SECRET`): its store is empty by construction, nothing is left over from this one. In-process `_restart` is the
same event for the controls - a new start marker AND an empty memory store together, never the marker alone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import jwt
import pytest

import app.utils.security as security
from app.config import settings
from app.core.auth.service import AuthService
from app.utils.exceptions import UnauthorizedException
from tests.unit.test_p028_e6_refresh_rotation_is_atomic import _Db, _Redis

_CHILD = """
import asyncio, sys
from types import SimpleNamespace
from app.core.auth.service import AuthService
from app.utils.exceptions import UnauthorizedException
from app.utils.security import decode_token

class Db:
    async def execute(self, _stmt):
        alice = SimpleNamespace(pid="alice", display_name="A", status="active")
        return SimpleNamespace(scalar_one_or_none=lambda: alice)

async def main():
    # Control: same secret, valid token, and this process's store does not know it - a refusal is not a bad signature.
    assert await decode_token(sys.argv[1], expected_type="refresh") is not None
    try:
        await AuthService(Db()).refresh_tokens(sys.argv[1])
        print("ACCEPTED")
    except UnauthorizedException:
        print("REFUSED")

asyncio.run(main())
"""


async def _refresh(token: str) -> dict | None:
    try:
        return await AuthService(_Db(parties=1)).refresh_tokens(token)
    except UnauthorizedException:
        return None


def _restart(monkeypatch) -> None:
    monkeypatch.setattr(security, "_revoked_jti", {})
    monkeypatch.setattr(security, "_process_start_marker", uuid.uuid4().hex, raising=False)


@pytest.fixture
def memory_store(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REDIS_ENABLED", False, raising=False)
    monkeypatch.setattr(security, "_redis_client", None)
    monkeypatch.setattr(security, "_revoked_jti", {})


async def test_a_used_refresh_token_is_refused_by_the_next_process(memory_store) -> None:
    used = security.create_refresh_token(subject="alice")
    assert await _refresh(used) is not None  # control: it worked once, in this process
    env = {**os.environ, "JWT_SECRET": settings.JWT_SECRET, "REDIS_ENABLED": "false"}
    child = subprocess.run([sys.executable, "-c", _CHILD, used], env=env, capture_output=True, text=True, timeout=120)
    assert child.returncode == 0, child.stderr
    assert child.stdout.split() == ["REFUSED"], child.stdout


async def test_without_a_restart_the_new_token_works_exactly_once(memory_store) -> None:
    first = await _refresh(security.create_refresh_token(subject="alice"))
    assert first is not None
    assert await _refresh(first["refresh_token"]) is not None
    assert await _refresh(first["refresh_token"]) is None


async def test_a_restart_refuses_refresh_tokens_but_not_access_tokens(memory_store, monkeypatch) -> None:
    access, unused = security.create_access_token("alice"), security.create_refresh_token(subject="alice")
    unmarked = jwt.encode(
        {"exp": int(time.time()) + 600, "sub": "alice", "type": "refresh", "jti": uuid.uuid4().hex},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )  # what a release before 029 issued
    _restart(monkeypatch)
    assert await _refresh(unused) is None and await _refresh(unmarked) is None
    assert (await security.decode_token(access))["sub"] == "alice"
    assert await _refresh(security.create_refresh_token(subject="alice")) is not None  # a new login is served


async def test_with_redis_an_unused_token_survives_a_restart_and_a_used_one_does_not(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
    monkeypatch.setattr(security, "_redis_client", _Redis())  # the Redis keys outlive the restart below
    used, unused = (security.create_refresh_token(subject="alice") for _ in range(2))
    assert await _refresh(used) is not None
    _restart(monkeypatch)
    assert await _refresh(unused) is not None
    assert await _refresh(used) is None


async def test_a_token_is_refused_by_the_other_kind_of_store(monkeypatch) -> None:
    """Redis switched on or off around a restart: the store that would know the token was used is not the one asked."""
    monkeypatch.setattr(security, "_revoked_jti", {})
    monkeypatch.setattr(security, "_redis_client", _Redis())
    monkeypatch.setattr(settings, "REDIS_ENABLED", False, raising=False)
    from_memory = security.create_refresh_token(subject="alice")
    monkeypatch.setattr(settings, "REDIS_ENABLED", True)
    from_redis = security.create_refresh_token(subject="alice")
    assert await _refresh(from_memory) is None
    monkeypatch.setattr(security, "_redis_client", None)  # enabled, but no client: memory - and the marker is checked
    assert await _refresh(from_redis) is None


async def test_expired_revocations_leave_the_memory_store_and_live_ones_stay(memory_store) -> None:
    now = int(time.time())
    security._revoked_jti.update({"expired": now - 1, "live": now + 600})
    assert await security.claim_jti("new", exp=now + 600) is True
    assert set(security._revoked_jti) == {"live", "new"}
    assert await security.is_jti_revoked("live") is True and await security.claim_jti("live", exp=now + 600) is False
