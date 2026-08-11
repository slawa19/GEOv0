import asyncio
import json

import pytest
from httpx import AsyncClient

from app.core.simulator.runtime import runtime


@pytest.mark.asyncio
async def test_simulator_artifacts_include_events_ndjson(
    client: AsyncClient, auth_headers
):
    # Start a fixtures-mode run.
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={
            "scenario_id": "greenfield-village-100-realistic-v2",
            "mode": "fixtures",
            "intensity_percent": 90,
        },
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # Trigger a real fixtures event, then close the run through the normal
    # terminal status so HTTPX's buffering ASGI transport receives a finite
    # production SSE response.
    observer = await runtime.subscribe(run_id, equivalent="UAH")
    try:
        async def _wait_for_domain_event() -> None:
            while True:
                event = await observer.queue.get()
                if event.get("type") != "run_status":
                    return

        await asyncio.wait_for(_wait_for_domain_event(), timeout=5.0)
    finally:
        await runtime.unsubscribe(run_id, observer)

    stop_resp = await client.post(
        f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers
    )
    assert stop_resp.status_code == 200, stop_resp.text

    # Exercise the actual HTTP replay path after the domain event is journaled.
    url = f"/api/v1/simulator/runs/{run_id}/events"
    async with client.stream(
        "GET",
        url,
        headers={**auth_headers, "Last-Event-ID": f"evt_{run_id}_000000"},
        params={"equivalent": "UAH"},
    ) as r:
        assert r.status_code == 200

        async def _read_one_event() -> None:
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    return

        await asyncio.wait_for(_read_one_event(), timeout=5.0)

    # Artifacts index must include events.ndjson.
    idx = await client.get(
        f"/api/v1/simulator/runs/{run_id}/artifacts", headers=auth_headers
    )
    assert idx.status_code == 200, idx.text

    items = idx.json().get("items") or []
    names = {it.get("name") for it in items}
    assert "events.ndjson" in names
    assert "summary.json" in names
    assert "bundle.zip" in names

    assert (
        idx.json().get("bundle_url")
        == f"/api/v1/simulator/runs/{run_id}/artifacts/bundle.zip"
    )

    events_item = next(it for it in items if it.get("name") == "events.ndjson")
    assert events_item.get("content_type") == "application/x-ndjson"

    # Download and validate it contains JSON lines.
    dl_url = events_item["url"]
    dl = await client.get(dl_url, headers=auth_headers)
    assert dl.status_code == 200, dl.text

    lines = [ln for ln in dl.text.splitlines() if ln.strip()]
    assert len(lines) >= 1

    first = json.loads(lines[0])
    assert str(first.get("type") or "").strip()
    assert first.get("type") != "run_status"
    assert str(first.get("event_id") or "").startswith("evt_")

    # run_status is intentionally not exported into events.ndjson (too noisy).
    assert all(json.loads(ln).get("type") != "run_status" for ln in lines)

    # Bundle.zip must be downloadable.
    bundle = await client.get(
        f"/api/v1/simulator/runs/{run_id}/artifacts/bundle.zip", headers=auth_headers
    )
    assert bundle.status_code == 200
    assert bundle.content.startswith(b"PK")
