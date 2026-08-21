import asyncio
import json

import pytest
from httpx import AsyncClient

from app.core.simulator.runtime import runtime


@pytest.mark.asyncio
async def test_simulator_run_events_sse_has_run_status_and_tx_updated(
    client: AsyncClient, auth_headers
):
    # Start a fixtures-mode run from an existing fixture scenario.
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": "greenfield-village-100-realistic-v2", "mode": "fixtures", "intensity_percent": 90},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # HTTPX buffers ASGI streams until completion. Trigger the normal fixtures
    # event, then stop the run so the HTTP assertion below uses finite replay
    # instead of any pytest-only production branch.
    observer = await runtime.subscribe(run_id, equivalent="UAH")
    try:
        async def _wait_for_tx() -> None:
            while True:
                if (await observer.queue.get()).get("type") == "tx.updated":
                    return

        await asyncio.wait_for(_wait_for_tx(), timeout=5.0)
    finally:
        await runtime.unsubscribe(run_id, observer)

    stop_resp = await client.post(
        f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers
    )
    assert stop_resp.status_code == 200, stop_resp.text

    url = f"/api/v1/simulator/runs/{run_id}/events"

    seen_run_status = False
    seen_tx = False

    async with client.stream(
        "GET",
        url,
        headers={**auth_headers, "Last-Event-ID": f"evt_{run_id}_000000"},
        params={"equivalent": "UAH"},
    ) as r:
        assert r.status_code == 200

        # We expect an immediate run_status snapshot, then at least one tx.updated.
        async def _read_until() -> None:
            nonlocal seen_run_status, seen_tx
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))
                if payload.get("type") == "run_status":
                    seen_run_status = True
                if payload.get("type") == "tx.updated":
                    seen_tx = True
                if seen_run_status and seen_tx:
                    return

        await asyncio.wait_for(_read_until(), timeout=8.0)

    assert seen_run_status
    assert seen_tx
