import asyncio
import json

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_simulator_sse_trust_drift_decay_emits_edge_patch_not_empty_topology_changed(
    client: AsyncClient,
    auth_headers,
):
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={
            "scenario_id": "trust-drift-decay-minimal",
            # Keep intensity low so we don't spam the single edge.
            # Planner guarantees >=1 action/tick when intensity>0.
            "mode": "real",
            "intensity_percent": 5,
        },
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    url = f"/api/v1/simulator/runs/{run_id}/events"

    seen_decay_topology_changed = False
    seen_tx_updated = False
    seen_types: dict[str, int] = {}

    try:
        async with client.stream(
            "GET",
            url,
            headers=auth_headers,
            params={
                "equivalent": "UAH",
                # Under pytest, the SSE endpoint intentionally terminates after a
                # bounded "first frame". Ask it to keep streaming until we see
                # topology.changed (or hit its short deadline).
                "stop_after_types": "topology.changed",
            },
        ) as r:
            assert r.status_code == 200

            async def _read_until() -> None:
                nonlocal seen_decay_topology_changed, seen_tx_updated
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue

                    evt = json.loads(line.removeprefix("data: "))
                    t = str(evt.get("type") or "")
                    if t:
                        seen_types[t] = int(seen_types.get(t, 0)) + 1

                    if t == "tx.updated":
                        seen_tx_updated = True

                    if t != "topology.changed":
                        continue

                    # reason is an extra field (schema extra="allow")
                    if evt.get("reason") != "trust_drift_decay":
                        continue

                    payload = evt.get("payload")
                    assert isinstance(payload, dict)

                    edge_patch = payload.get("edge_patch")
                    # Regression guard: decay must not emit empty payload (UI would refresh snapshot every tick).
                    assert isinstance(edge_patch, list) and edge_patch, payload

                    ep0 = edge_patch[0]
                    assert isinstance(ep0, dict)
                    assert isinstance(ep0.get("source"), str)
                    assert isinstance(ep0.get("target"), str)
                    assert isinstance(ep0.get("trust_limit"), str)
                    assert isinstance(ep0.get("viz_alpha_key"), str)

                    seen_decay_topology_changed = True
                    return

            await asyncio.wait_for(_read_until(), timeout=15.0)
    finally:
        # Stop the background run to avoid holding DB locks across tests.
        await client.post(
            f"/api/v1/simulator/runs/{run_id}/stop",
            headers=auth_headers,
        )

    assert seen_tx_updated, f"No tx.updated seen; types={seen_types}"
    assert seen_decay_topology_changed, f"No trust_drift_decay topology.changed; types={seen_types}"
