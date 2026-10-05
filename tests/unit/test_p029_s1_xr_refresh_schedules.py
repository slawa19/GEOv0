"""029 S1, `T2991`: the stands of the cross-session review (session geov0-05), kept as a guard.

Each asserts the SAFE outcome. The two `xfail(strict=True)` cells are replays the review FOUND and S1 does not close
(decision 2026-10-05, AGENTS.md 19.4: no more product code for Redis durability in S1); their receivers are the rows
of `specs/BACKLOG.md`, section "Класс 2 из перекрёстного ревью S1 программы 029". A fix turns the mark red.
Limits: the Redis here is a fake with a manual clock; it shows schedules, not what a real Redis persists.
"""

import asyncio
import random
import time

import pytest

import app.utils.security as security
from app.config import settings
from app.core.auth.service import AuthService
from app.utils.exceptions import UnauthorizedException
from tests.unit.test_p028_e6_refresh_rotation_is_atomic import _Db


_DOUBLE_CLAIM_RUNS: list[int] = []


class _ClockRedis:
    """SET NX / EX, GET, EXISTS with a manual clock; snapshot/restore models RDB reload or a lagging replica."""

    def __init__(self, ops_hook=None) -> None:
        self.keys: dict[str, tuple[str, float | None]] = {}
        self.clock = 0.0
        self.hook = ops_hook
        self.log: list[str] = []

    def _alive(self, key):
        item = self.keys.get(key)
        if item is None:
            return None
        if item[1] is not None and item[1] <= self.clock:
            del self.keys[key]
            return None
        return item[0]

    async def _op(self, name):
        if self.hook is not None:
            await self.hook(self, name)
        self.log.append(name)

    async def set(self, key, value, ex=None, nx=False):
        await self._op(f"set:{key.split(':')[1]}")
        if nx and self._alive(key) is not None:
            return None
        self.keys[key] = (value, None if ex is None else self.clock + ex)
        if key.startswith("jwt:"):
            self.claims_won = getattr(self, "claims_won", 0) + 1
        return True

    async def get(self, key):
        await self._op("get")
        return self._alive(key)

    async def exists(self, key):
        await self._op("exists")
        return int(self._alive(key) is not None)

    def flushall(self):
        self.keys.clear()

    def snapshot(self):
        return dict(self.keys)

    def restore(self, snap):
        self.keys = dict(snap)


@pytest.fixture
def redis(monkeypatch):
    client = _ClockRedis()
    monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
    monkeypatch.setattr(security, "_redis_client", client)
    monkeypatch.setattr(security, "_revoked_jti", {})
    return client


async def _refresh(token):
    try:
        return await AuthService(_Db(parties=1)).refresh_tokens(token)
    except UnauthorizedException:
        return None


@pytest.mark.xfail(strict=True, reason="BACKLOG, 029 S1 cross-review: a partial Redis rollback revives a used refresh")
async def test_xr_rdb_rollback_or_replica_failover_revives_a_used_refresh(redis) -> None:
    """Redis default `save` points + the image's /data volume: a crash reloads the last RDB snapshot. The store id
    was written long before (in the snapshot); the revocation of a token used after the snapshot is not."""
    used = await security.create_refresh_token(subject="alice")
    snap = redis.snapshot()  # periodic BGSAVE / replica sync point
    assert await _refresh(used) is not None  # used once
    redis.restore(snap)  # crash + reload of dump.rdb, or failover to a replica that lagged
    # mechanism controls: the id survived, the revocation did not
    assert any(k == "geo:refresh_store_id" for k in redis.keys)
    assert not any(k.startswith("jwt:jti:revoked:") for k in redis.keys)
    assert await _refresh(used) is None, "a used refresh token issued a second pair after a partial rollback"


@pytest.mark.xfail(strict=True, reason="BACKLOG, 029 S1 cross-review: revocation TTL by the issuing worker's clock")
async def test_xr_ttl_arithmetic_with_skew(monkeypatch) -> None:
    """Pure arithmetic of claim_jti's TTL: with exp 1000 s ahead on W2's clock and W1 300 s ahead, the key lives
    700 s on Redis's clock; W2 accepts the token for 1000 s. Window = skew."""
    client = _ClockRedis()
    monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
    monkeypatch.setattr(security, "_redis_client", client)
    real = time.time
    exp = int(real()) + 1000
    monkeypatch.setattr(security.time, "time", lambda: real() + 300)
    assert await security.claim_jti("j", exp=exp) is True
    monkeypatch.setattr(security.time, "time", real)
    ttl = client.keys["jwt:jti:revoked:j"][1]
    client.clock = ttl + 0.5  # key expired on Redis
    assert await security.is_jti_revoked("j") is False  # mechanism: revocation gone
    # W2 (correct clock) still within exp by ~300 s -> claim succeeds again
    assert await security.claim_jti("j", exp=exp) is False, "revocation expired ~skew seconds before the token"


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("flushes", [1, 2])
async def test_xr_two_presenters_with_flushes_at_every_redis_op_never_get_two_pairs(monkeypatch, seed, flushes):
    rng = random.Random(seed)
    results_by_k = []
    probe = _ClockRedis()
    monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
    monkeypatch.setattr(security, "_redis_client", probe)
    token = await security.create_refresh_token(subject="alice")
    base_keys = probe.snapshot()
    dangerous_phase = 0
    for k in range(0, 16):
        counter = {"n": 0, "flushed": 0}
        ks = {k, k + rng.randint(1, 5)} if flushes == 2 else {k}

        async def hook(client, name):
            for _ in range(rng.randint(0, 3)):
                await asyncio.sleep(0)
            if counter["n"] in ks:
                client.flushall()
                counter["flushed"] += 1
            counter["n"] += 1

        client = _ClockRedis(hook)
        client.restore(base_keys)
        monkeypatch.setattr(security, "_redis_client", client)

        async def present():
            for _ in range(rng.randint(0, 3)):
                await asyncio.sleep(0)
            try:
                return await AuthService(_Db(parties=1)).refresh_tokens(token)
            except (UnauthorizedException, RuntimeError):  # RuntimeError: id lost between SET NX and GET -> 500
                return None

        a, b = await asyncio.gather(present(), present())
        claims = [n for n in client.log if n == "set:jti"]
        if getattr(client, "claims_won", 0) == 2:  # B's claim WON after A's was flushed away
            dangerous_phase += 1  # both presenters reached claim with a flush in the run
        results_by_k.append((k, a is not None, b is not None))
        assert not (a is not None and b is not None), (seed, k, client.log)
    print(f"XR seed={seed} flushes={flushes} double_claim_runs={dangerous_phase}")
    _DOUBLE_CLAIM_RUNS.append(dangerous_phase)


def test_xr_zz_double_claim_phase_was_reached() -> None:
    """Anti-vacuum for the interleaving stand: B's claim WON after A's was flushed in most seeds (re-check refused)."""
    assert len(_DOUBLE_CLAIM_RUNS) == 80 and sum(1 for n in _DOUBLE_CLAIM_RUNS if n) >= 70, _DOUBLE_CLAIM_RUNS


async def test_xr_redis_errors_fail_closed(redis, monkeypatch) -> None:
    token = await security.create_refresh_token(subject="alice")

    async def boom(*a, **kw):
        raise ConnectionError("redis gone")

    for attr in ("set", "get", "exists"):
        monkeypatch.setattr(redis, attr, boom)
        with pytest.raises(ConnectionError):
            await AuthService(_Db(parties=1)).refresh_tokens(token)
        monkeypatch.undo()
        monkeypatch.setattr(settings, "REDIS_ENABLED", True, raising=False)
        monkeypatch.setattr(security, "_redis_client", redis)


async def test_xr_client_removed_between_check_and_claim_is_refused(redis, monkeypatch) -> None:
    """Shutdown sets the client to None while a refresh is in flight: claim lands in memory, re-check catches it."""
    token = await security.create_refresh_token(subject="alice")
    parked, release = asyncio.Event(), asyncio.Event()

    class _ParkedDb(_Db):
        async def execute(self, statement):
            parked.set()
            await asyncio.wait_for(release.wait(), timeout=20)
            return await super().execute(statement)

    async def present():
        try:
            return await AuthService(_ParkedDb(parties=1)).refresh_tokens(token)
        except UnauthorizedException:
            return None

    task = asyncio.create_task(present())
    await asyncio.wait_for(parked.wait(), timeout=20)
    assert "get" in redis.log  # mechanism: check 1 went to Redis
    monkeypatch.setattr(security, "_redis_client", None)
    release.set()
    assert await asyncio.wait_for(task, timeout=20) is None
    assert security._revoked_jti  # mechanism: the claim went to memory
