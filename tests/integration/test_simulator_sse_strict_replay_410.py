import pytest
from httpx import AsyncClient

from app.core.simulator.runtime import runtime


@pytest.mark.asyncio
async def test_simulator_sse_strict_replay_returns_410_for_too_old_last_event_id(
    client: AsyncClient,
    auth_headers,
    monkeypatch,
):
    # Enable strict replay and make the in-memory ring buffer tiny to force pruning.
    monkeypatch.setattr(runtime, "_sse_strict_replay", True)
    monkeypatch.setattr(runtime, "_event_buffer_max", 3)

    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": "greenfield-village-100-realistic-v2", "mode": "fixtures", "intensity_percent": 50},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # Generate enough buffered events so that seq=1 becomes older than the oldest retained.
    for _ in range(10):
        runtime.publish_run_status(run_id)

    too_old_event_id = f"evt_{run_id}_000001"

    async with client.stream(
        "GET",
        f"/api/v1/simulator/runs/{run_id}/events",
        headers={**auth_headers, "Last-Event-ID": too_old_event_id},
        params={"equivalent": "UAH"},
    ) as r:
        assert r.status_code == 410
