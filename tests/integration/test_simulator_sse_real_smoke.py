import asyncio
import json

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_simulator_run_events_sse_real_mode_has_run_status_and_tx_updated(
    client: AsyncClient, auth_headers
):
    # Start a real-mode run from an existing fixture scenario.
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={
            "scenario_id": "greenfield-village-100-realistic-v2",
            "mode": "real",
            "intensity_percent": 90,
        },
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    url = f"/api/v1/simulator/runs/{run_id}/events"

    seen_run_status = False
    seen_tx = False
    seen_tx_with_edges = False
    seen_tx_with_patches = False

    try:
        async with client.stream(
            "GET",
            url,
            headers=auth_headers,
            params={"equivalent": "UAH"},
        ) as r:
            assert r.status_code == 200

            # We expect an immediate run_status snapshot, then at least one tx.updated.
            async def _read_until() -> None:
                nonlocal seen_run_status, seen_tx, seen_tx_with_edges, seen_tx_with_patches
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line.removeprefix("data: "))
                    if payload.get("type") == "run_status":
                        seen_run_status = True
                    if payload.get("type") == "tx.updated":
                        seen_tx = True
                        edges = payload.get("edges")
                        if isinstance(edges, list) and edges:
                            e0 = edges[0]
                            if isinstance(e0, dict) and isinstance(e0.get("from"), str) and isinstance(
                                e0.get("to"), str
                            ):
                                seen_tx_with_edges = True

                        edge_patch = payload.get("edge_patch")
                        node_patch = payload.get("node_patch")
                        if isinstance(edge_patch, list) and edge_patch and isinstance(node_patch, list) and node_patch:
                            ep0 = edge_patch[0]
                            np0 = node_patch[0]
                            if (
                                isinstance(ep0, dict)
                                and isinstance(ep0.get("source"), str)
                                and isinstance(ep0.get("target"), str)
                                and isinstance(ep0.get("viz_alpha_key"), str)
                                and isinstance(ep0.get("viz_width_key"), str)
                                and isinstance(np0, dict)
                                and isinstance(np0.get("id"), str)
                                and isinstance(np0.get("net_balance_atoms"), str)
                                and isinstance(np0.get("net_sign"), int)
                                and isinstance(np0.get("viz_color_key"), str)
                                and isinstance(np0.get("viz_size"), dict)
                            ):
                                # Contract: net_balance_atoms is magnitude; sign is carried by net_sign.
                                assert not str(np0.get("net_balance_atoms")).startswith("-")
                                seen_tx_with_patches = True
                    if seen_run_status and seen_tx_with_edges and seen_tx_with_patches:
                        return

            await asyncio.wait_for(_read_until(), timeout=10.0)
    finally:
        # Stop the background run to avoid holding DB locks across tests.
        await client.post(
            f"/api/v1/simulator/runs/{run_id}/stop",
            headers=auth_headers,
        )

    assert seen_run_status
    assert seen_tx
    assert seen_tx_with_edges
    assert seen_tx_with_patches
