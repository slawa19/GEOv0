from __future__ import annotations

from collections import defaultdict

import pytest
from httpx import AsyncClient

from app.api import deps
from app.config import settings
from app.main import app


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: defaultdict[str, int] = defaultdict(int)
        self.expirations: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] += 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations.append((key, seconds))


@pytest.fixture(autouse=True)
def _isolated_http_rate_limit(monkeypatch: pytest.MonkeyPatch):
    clock = {"monotonic": 600.0, "wall": 600.0}
    deps._rate_limit_counters.clear()
    monkeypatch.setattr(deps, "_rate_limit_last_cleanup_bucket", None)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 10)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_WINDOW", 2)
    monkeypatch.setattr(deps.time, "monotonic", lambda: clock["monotonic"])
    monkeypatch.setattr(deps.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(app.state, "redis", None, raising=False)
    yield clock
    deps._rate_limit_counters.clear()


@pytest.mark.asyncio
async def test_http_rate_limit_falls_back_to_memory_and_resets_next_window(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_http_rate_limit: dict[str, float],
) -> None:
    monkeypatch.setattr(settings, "REDIS_ENABLED", True)

    first = await client.get("/api/v1/healthz")
    second = await client.get("/api/v1/healthz")
    limited = await client.get("/api/v1/healthz")

    assert [first.status_code, second.status_code, limited.status_code] == [200, 200, 429]
    assert limited.json() == {
        "error": {
            "code": "E009",
            "message": "Too many requests",
            "details": {"window_seconds": 10, "limit": 2},
        }
    }
    assert {bucket for bucket, _host in deps._rate_limit_counters} == {60}

    _isolated_http_rate_limit["monotonic"] = 610.0
    next_window = await client.get("/api/v1/healthz")

    assert next_window.status_code == 200
    assert {bucket for bucket, _host in deps._rate_limit_counters} == {60, 61}


@pytest.mark.asyncio
async def test_http_rate_limit_uses_redis_counter_and_expiry(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_WINDOW", 1)
    monkeypatch.setattr(app.state, "redis", redis)

    allowed = await client.get("/api/v1/healthz")
    limited = await client.get("/api/v1/healthz")

    assert allowed.status_code == 200
    assert limited.status_code == 429
    assert len(redis.counts) == 1
    [(key, count)] = redis.counts.items()
    assert key.startswith("rl:")
    assert key.endswith(":60")
    assert count == 2
    assert redis.expirations == [(key, 11)]
    assert deps._rate_limit_counters == {}
