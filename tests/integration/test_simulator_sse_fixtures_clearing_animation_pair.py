import asyncio
import json

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_simulator_fixtures_mode_emits_clearing_plan_and_done_with_same_plan_id(
    client: AsyncClient,
    auth_headers,
    monkeypatch,
):
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": "greenfield-village-100-realistic-v2", "mode": "fixtures", "intensity_percent": 50},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # clearing.plan is emitted very early (often before we attach SSE).
    # Give the heartbeat loop time to emit clearing.plan + clearing.done, then use SSE replay.
    await asyncio.sleep(3.5)

    url = f"/api/v1/simulator/runs/{run_id}/events"

    plan_id: str | None = None
    seen_plan = False
    seen_done = False

    # This endpoint intentionally short-circuits under pytest to avoid hanging streams.
    # For this test we need both clearing.plan and clearing.done.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    async with client.stream(
        "GET",
        url,
        headers={**auth_headers, "Last-Event-ID": f"evt_{run_id}_000000"},
        params={"equivalent": "UAH"},
    ) as r:
        assert r.status_code == 200

        async def _read_until() -> None:
            nonlocal plan_id, seen_plan, seen_done
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line.removeprefix("data: "))

                if payload.get("type") == "clearing.plan":
                    seen_plan = True
                    plan_id = payload.get("plan_id")
                    assert isinstance(plan_id, str) and plan_id

                if payload.get("type") == "clearing.done":
                    seen_done = True
                    assert payload.get("plan_id") == plan_id

                if seen_plan and seen_done:
                    return

        await asyncio.wait_for(_read_until(), timeout=8.0)

    assert seen_plan
    assert seen_done

    # Cleanup: stop run to avoid background heartbeats affecting other tests.
    await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)
