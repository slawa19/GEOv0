from unittest.mock import MagicMock

import pytest

from app.api import deps
from app.config import settings
from app.utils.exceptions import TooManyRequestsException


def _request(host: str) -> MagicMock:
    request = MagicMock()
    request.url.path = "/api/v1/test-rate-limit"
    request.client.host = host
    request.app.state.redis = None
    return request


@pytest.fixture(autouse=True)
def _isolated_rate_limit_state(monkeypatch):
    deps._rate_limit_counters.clear()
    monkeypatch.setattr(deps, "_rate_limit_last_cleanup_bucket", None)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_WINDOW", 100)
    monkeypatch.setattr(deps.time, "monotonic", lambda: 600.0)
    yield
    deps._rate_limit_counters.clear()


@pytest.mark.asyncio
async def test_unique_hosts_cannot_grow_in_memory_rate_limit_state_without_bound(
    monkeypatch,
) -> None:
    monkeypatch.setattr(deps, "_RATE_LIMIT_MAX_ENTRIES", 4)

    for index in range(20):
        await deps.rate_limit(_request(f"host-{index}"))

    assert len(deps._rate_limit_counters) == 4
    assert {host for _, host in deps._rate_limit_counters} == {
        "host-16",
        "host-17",
        "host-18",
        "host-19",
    }


@pytest.mark.asyncio
async def test_recently_active_host_survives_unique_host_eviction(monkeypatch) -> None:
    monkeypatch.setattr(deps, "_RATE_LIMIT_MAX_ENTRIES", 3)
    hot_request = _request("hot-host")

    await deps.rate_limit(hot_request)
    for index in range(10):
        await deps.rate_limit(_request(f"unique-{index}"))
        await deps.rate_limit(hot_request)

    assert deps._rate_limit_counters[(10, "hot-host")] == 11
    assert len(deps._rate_limit_counters) == 3


@pytest.mark.asyncio
async def test_bounded_state_still_enforces_limit_for_real_host(monkeypatch) -> None:
    monkeypatch.setattr(deps, "_RATE_LIMIT_MAX_ENTRIES", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_WINDOW", 2)
    request = _request("limited-host")

    await deps.rate_limit(request)
    await deps.rate_limit(request)
    with pytest.raises(TooManyRequestsException):
        await deps.rate_limit(request)

    assert deps._rate_limit_counters[(10, "limited-host")] == 3
