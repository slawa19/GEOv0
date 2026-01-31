"""Quick local diagnostic for simulator SSE throughput.

Runs against a locally running backend (default http://127.0.0.1:18000).
It:
- registers a temporary participant,
- logs in,
- starts a real-mode simulator run,
- reads SSE events for a short window,
- prints event rate + approximate payload sizes,
- stops the run.

Usage (PowerShell):
  .venv\Scripts\python.exe scripts\diagnose_simulator_sse.py

Optional env vars:
  GEO_BASE_URL=http://127.0.0.1:18000
  GEO_EQ=UAH
  GEO_DURATION_SEC=8
  GEO_INTENSITY=90
  GEO_SCENARIO=greenfield-village-100
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import statistics
import time
from collections import Counter
from typing import Any

import httpx
from nacl.signing import SigningKey

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or str(default))
    except Exception:
        return int(default)


async def _auth_headers(client: httpx.AsyncClient) -> dict[str, str]:
    public_key, private_key = generate_keypair()

    msg = canonical_json(
        {
            "display_name": "Diag User",
            "type": "person",
            "public_key": public_key,
            "profile": {},
        }
    )
    signing_key = SigningKey(base64.b64decode(private_key))
    sig_b64 = base64.b64encode(signing_key.sign(msg).signature).decode("utf-8")

    resp = await client.post(
        "/api/v1/participants",
        json={
            "display_name": "Diag User",
            "type": "person",
            "public_key": public_key,
            "signature": sig_b64,
            "profile": {},
        },
    )
    resp.raise_for_status()
    pid = resp.json()["pid"]

    resp = await client.post("/api/v1/auth/challenge", json={"pid": pid})
    resp.raise_for_status()
    challenge = resp.json()["challenge"]

    login_sig = base64.b64encode(signing_key.sign(challenge.encode("utf-8")).signature).decode("utf-8")
    resp = await client.post(
        "/api/v1/auth/login",
        json={"pid": pid, "challenge": challenge, "signature": login_sig},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _summarize_sizes(name: str, sizes: list[int]) -> str:
    if not sizes:
        return f"{name}: n=0"
    sizes_sorted = sorted(sizes)
    p50 = sizes_sorted[int(0.50 * (len(sizes_sorted) - 1))]
    p90 = sizes_sorted[int(0.90 * (len(sizes_sorted) - 1))]
    p99 = sizes_sorted[int(0.99 * (len(sizes_sorted) - 1))]
    return (
        f"{name}: n={len(sizes)} avg={int(statistics.mean(sizes))}B "
        f"p50={p50}B p90={p90}B p99={p99}B max={max(sizes)}B"
    )


async def main() -> None:
    base_url = os.getenv("GEO_BASE_URL", "http://127.0.0.1:18000")
    eq = os.getenv("GEO_EQ", "UAH")
    duration_sec = float(os.getenv("GEO_DURATION_SEC", "8") or "8")
    intensity = _env_int("GEO_INTENSITY", 90)
    scenario = os.getenv("GEO_SCENARIO", "greenfield-village-100")

    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        headers = await _auth_headers(client)

        resp = await client.post(
            "/api/v1/simulator/runs",
            headers=headers,
            json={"scenario_id": scenario, "mode": "real", "intensity_percent": intensity},
        )
        resp.raise_for_status()
        run_id = resp.json()["run_id"]

        url = f"/api/v1/simulator/runs/{run_id}/events"

        event_counts: Counter[str] = Counter()
        data_sizes: list[int] = []
        tx_sizes: list[int] = []
        tx_patch_sizes: list[int] = []
        tx_edges_counts: list[int] = []
        tx_edge_patch_counts: list[int] = []
        tx_node_patch_counts: list[int] = []

        t0 = time.perf_counter()

        try:
            async with client.stream("GET", url, headers=headers, params={"equivalent": eq}) as r:
                r.raise_for_status()

                async for line in r.aiter_lines():
                    now = time.perf_counter()
                    if now - t0 > duration_sec:
                        break

                    if not line.startswith("data: "):
                        continue

                    raw = line.removeprefix("data: ")
                    data_sizes.append(len(raw.encode("utf-8")))

                    try:
                        payload: dict[str, Any] = json.loads(raw)
                    except Exception:
                        event_counts["<bad_json>"] += 1
                        continue

                    typ = str(payload.get("type") or "<no_type>")
                    event_counts[typ] += 1

                    if typ == "tx.updated":
                        tx_sizes.append(len(raw.encode("utf-8")))
                        edges = payload.get("edges")
                        edge_patch = payload.get("edge_patch")
                        node_patch = payload.get("node_patch")

                        if isinstance(edges, list):
                            tx_edges_counts.append(len(edges))
                        if isinstance(edge_patch, list):
                            tx_edge_patch_counts.append(len(edge_patch))
                        if isinstance(node_patch, list):
                            tx_node_patch_counts.append(len(node_patch))

                        # A rough measure of patch heaviness.
                        if isinstance(edge_patch, list) or isinstance(node_patch, list):
                            tx_patch_sizes.append(
                                len(
                                    json.dumps(
                                        {
                                            "edge_patch": edge_patch if isinstance(edge_patch, list) else None,
                                            "node_patch": node_patch if isinstance(node_patch, list) else None,
                                        },
                                        ensure_ascii=False,
                                    ).encode("utf-8")
                                )
                            )
        finally:
            # Ensure we stop background work.
            try:
                await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=headers)
            except Exception:
                pass

    elapsed = max(0.001, time.perf_counter() - t0)
    total = sum(event_counts.values())

    print("=== Simulator SSE diagnostic ===")
    print(f"base_url={base_url} run_id={run_id} eq={eq} duration={duration_sec}s")
    print(f"events_total={total} rate={total/elapsed:.1f} ev/s")
    print("by_type:")
    for k, v in event_counts.most_common():
        print(f"  {k}: {v} ({v/elapsed:.1f}/s)")

    print(_summarize_sizes("data_payload", data_sizes))
    print(_summarize_sizes("tx.updated", tx_sizes))
    print(_summarize_sizes("tx.patch_only", tx_patch_sizes))

    def _avg(xs: list[int]) -> str:
        if not xs:
            return "n=0"
        return f"n={len(xs)} avg={statistics.mean(xs):.1f} p95={sorted(xs)[int(0.95*(len(xs)-1))]}"

    print(f"edges.count: {_avg(tx_edges_counts)}")
    print(f"edge_patch.count: {_avg(tx_edge_patch_counts)}")
    print(f"node_patch.count: {_avg(tx_node_patch_counts)}")


if __name__ == "__main__":
    asyncio.run(main())
