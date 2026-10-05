"""029 S1, F-029-31 (BACKLOG № 240) and F-029-1 (№ 286): a used refresh token stays used across a restart.

§15 review T2991 (fix-delta): every refresh token names the store that will know it was used - the process marker
in memory, an id kept IN Redis with Redis - so a legacy token without the claim and a token of a Redis that lost its
data are refused too. THE RESTART IS A REAL NEW PROCESS (`python -c`, the same `JWT_SECRET`): its memory store is empty by construction.
In-process `_restart` is the same event for the controls - a new marker AND an empty store, never the marker alone.
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
import uuid

import jwt
import pytest
import redis.asyncio as redis_asyncio
from fastapi import FastAPI

import app.main as main_module
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
    # Control: same secret, valid token, unknown to this process's store - a refusal is not a bad signature.
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
    used = await security.create_refresh_token(subject="alice")
    assert await _refresh(used) is not None  # control: it worked once, in this process
    env = {**os.environ, "JWT_SECRET": settings.JWT_SECRET, "REDIS_ENABLED": "false"}
    child = subprocess.run([sys.executable, "-c", _CHILD, used], env=env, capture_output=True, text=True, timeout=120)
    assert child.returncode == 0, child.stderr
    assert child.stdout.split() == ["REFUSED"], child.stdout


async def test_a_restart_refuses_refresh_tokens_but_not_access_tokens(memory_store, monkeypatch) -> None:
    rotated = await _refresh(await security.create_refresh_token(subject="alice"))
    assert rotated is not None and await _refresh(rotated["refresh_token"]) is not None  # no restart: works,
    assert await _refresh(rotated["refresh_token"]) is None  # ... and exactly once
    access, unused = security.create_access_token("alice"), await security.create_refresh_token(subject="alice")
    claims = {"exp": int(time.time()) + 600, "sub": "alice", "type": "refresh", "jti": uuid.uuid4().hex}
    unmarked = jwt.encode(claims, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)  # issued before 029
    _restart(monkeypatch)
    assert await _refresh(unused) is None and await _refresh(unmarked) is None
    assert (await security.decode_token(access))["sub"] == "alice"
    assert await _refresh(await security.create_refresh_token(subject="alice")) is not None  # a new login is served


async def test_with_redis_an_unused_token_survives_a_restart_and_a_used_one_does_not(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
    monkeypatch.setattr(security, "_redis_client", _Redis())  # the Redis keys outlive the restart below
    used, unused = [await security.create_refresh_token(subject="alice") for _ in range(2)]
    assert await _refresh(used) is not None
    _restart(monkeypatch)
    assert await _refresh(unused) is not None and await _refresh(used) is None


async def test_a_token_is_refused_by_the_other_kind_of_store(monkeypatch) -> None:
    """Redis switched on or off around a restart: the store that would know the token was used is not the one asked."""
    monkeypatch.setattr(security, "_revoked_jti", {})
    monkeypatch.setattr(security, "_redis_client", _Redis())
    monkeypatch.setattr(settings, "REDIS_ENABLED", False, raising=False)
    from_memory = await security.create_refresh_token(subject="alice")
    monkeypatch.setattr(settings, "REDIS_ENABLED", True)
    from_redis = await security.create_refresh_token(subject="alice")
    assert await _refresh(from_memory) is None
    monkeypatch.setattr(security, "_redis_client", None)  # enabled, but no client: memory - and the marker is checked
    assert await _refresh(from_redis) is None


async def test_expired_revocations_leave_the_memory_store_and_live_ones_stay(memory_store) -> None:
    now = int(time.time())
    security._revoked_jti.update({"expired": now - 1, "live": now + 600})
    assert await security.claim_jti("new", exp=now + 600) is True
    assert set(security._revoked_jti) == {"live", "new"}
    assert await security.is_jti_revoked("live") is True and await security.claim_jti("live", exp=now + 600) is False


def _legacy_refresh() -> str:
    """What every release before 029 issued: no `rsm` claim."""
    claims = {"exp": int(time.time()) + 600, "sub": "alice", "type": "refresh", "jti": uuid.uuid4().hex}
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@pytest.fixture
def redis_store(monkeypatch) -> None:
    monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
    monkeypatch.setattr(security, "_redis_client", _Redis())
    monkeypatch.setattr(security, "_revoked_jti", {})


async def test_a_legacy_refresh_token_is_refused_after_an_upgrade_that_turns_redis_on(redis_store) -> None:
    """T2991 finding 1: used under the old backend without Redis, then replayed - this Redis never heard of it."""
    assert await _refresh(_legacy_refresh()) is None
    assert await _refresh(await security.create_refresh_token(subject="alice")) is not None  # control: Redis serves


async def test_a_used_refresh_token_is_refused_by_a_redis_that_lost_its_data(redis_store, monkeypatch) -> None:
    """T2991 finding 2: Redis recreated empty under the same JWT secret - the revocation is gone, and so is the id."""
    used, unused = [await security.create_refresh_token(subject="alice") for _ in range(2)]
    assert await _refresh(used) is not None
    monkeypatch.setattr(security, "_redis_client", _Redis())  # the same deployment, an empty store
    assert await _refresh(used) is None and await _refresh(unused) is None
    assert await _refresh(await security.create_refresh_token(subject="alice")) is not None  # a new login is served


async def test_two_workers_on_one_redis_share_the_store_id(redis_store, monkeypatch) -> None:
    issued_by_one = await security.create_refresh_token(subject="alice")
    _restart(monkeypatch)  # the other worker: its own marker and memory, the same Redis
    rotated = await _refresh(issued_by_one)
    assert rotated is not None and await _refresh(issued_by_one) is None
    assert await _refresh(rotated["refresh_token"]) is not None


async def test_a_refresh_parked_across_a_redis_flush_does_not_become_a_second_session(redis_store, monkeypatch) -> None:
    """T2991 fix-delta review, finding 1. REAL SCHEDULE: A and B present one unused token and both pass the `rsm`
    check; B is parked at its participant read; A completes; Redis is flushed; B resumes into an empty store."""
    token, parked, release = await security.create_refresh_token(subject="alice"), asyncio.Event(), asyncio.Event()

    class _ParkedDb(_Db):
        async def execute(self, statement):
            parked.set()
            await asyncio.wait_for(release.wait(), timeout=20)
            return await super().execute(statement)

    async def present_b():
        try:
            return await AuthService(_ParkedDb(parties=1)).refresh_tokens(token)
        except UnauthorizedException:
            return None

    b = asyncio.create_task(present_b())
    await asyncio.wait_for(parked.wait(), timeout=20)  # control: B is past the check, before its claim
    assert await _refresh(token) is not None  # A used the token
    monkeypatch.setattr(security, "_redis_client", _Redis())  # FLUSHALL
    release.set()
    assert await asyncio.wait_for(b, timeout=20) is None


class _PolicyRedis:
    def __init__(self, policy) -> None:
        self.policy, self.closed = policy, False

    async def ping(self) -> None:
        return None

    async def config_get(self, name):
        if isinstance(self.policy, Exception):
            raise self.policy
        return {name: self.policy}

    async def aclose(self) -> None:
        self.closed = True


async def test_startup_refuses_a_redis_that_may_evict_revocations(monkeypatch) -> None:
    """Finding 2: under `volatile-*` a used token's revocation key (it has a TTL) is evicted and the store id stays."""
    client = _PolicyRedis("volatile-lru")
    monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *args, **kwargs: client)
    monkeypatch.setattr("app.core.simulator.storage.reconcile_stale_runs", lambda: asyncio.sleep(0, 0))
    with pytest.raises(RuntimeError, match="noeviction"):
        async with main_module.lifespan(FastAPI()):
            pytest.fail("the application started")
    assert client.closed and security._redis_client is None


async def test_startup_accepts_noeviction_and_names_the_requirement_when_config_is_forbidden(caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="app.main"):
        await main_module._require_redis_noeviction(_PolicyRedis("noeviction"))
        assert caplog.records == []  # anti-vacuum: the line below is not logged on every start
        await main_module._require_redis_noeviction(_PolicyRedis(redis_asyncio.ResponseError("unknown command")))
    assert [r.levelname for r in caplog.records] == ["ERROR"] and "noeviction" in caplog.text
    with pytest.raises(ConnectionError):  # only a refusal of CONFIG is tolerated, not a lost connection
        await main_module._require_redis_noeviction(_PolicyRedis(ConnectionError("gone")))
