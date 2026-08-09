"""Behavioral contract for unconditional SSE replay recovery responses."""

import pytest
from httpx import AsyncClient

from app.core.simulator.runtime import runtime


@pytest.mark.asyncio
async def test_simulator_sse_returns_410_for_every_unrecoverable_last_event_id_on_both_endpoints(
    client: AsyncClient,
    auth_headers,
    monkeypatch,
):
    # Replay correctness is unconditional; make the ring tiny to force pruning.
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

    cursors = [
        f"evt_{run_id}_000001",  # pruned
        "not-an-event-id",  # malformed
        f"evt_{run_id}_999999",  # future
        f"evt_{run_id}_{'9' * 5000}",  # oversized numeric tail
    ]
    paths = [
        f"/api/v1/simulator/runs/{run_id}/events",
        "/api/v1/simulator/events",
    ]
    for path in paths:
        for cursor in cursors:
            async with client.stream(
                "GET",
                path,
                headers={**auth_headers, "Last-Event-ID": cursor},
                params={"equivalent": "UAH"},
            ) as response:
                assert response.status_code == 410, (path, cursor, response.text)
