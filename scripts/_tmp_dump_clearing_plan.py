"""Temporary local script: dumps first clearing.plan SSE event.

Not part of product code. Safe to delete.

Usage (PowerShell):
  .venv\\Scripts\\python.exe scripts\\_tmp_dump_clearing_plan.py

Env vars:
  GEO_BASE_URL=http://127.0.0.1:18000
  GEO_EQ=UAH
  GEO_INTENSITY=90
  GEO_SCENARIO=greenfield-village-100-realistic-v2
  GEO_TIMEOUT_SEC=25
"""

from __future__ import annotations

import pathlib
import sys
import asyncio
import base64
import json
import os
import time

import httpx
from nacl.signing import SigningKey

# Allow running this script without installing the backend as a package.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair


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

    r = await client.post(
        "/api/v1/participants",
        json={
            "display_name": "Diag User",
            "type": "person",
            "public_key": public_key,
            "signature": sig_b64,
            "profile": {},
        },
    )
    r.raise_for_status()
    pid = r.json()["pid"]

    r = await client.post("/api/v1/auth/challenge", json={"pid": pid})
    r.raise_for_status()
    challenge = r.json()["challenge"]

    login_sig = base64.b64encode(signing_key.sign(challenge.encode("utf-8")).signature).decode("utf-8")
    r = await client.post(
        "/api/v1/auth/login",
        json={"pid": pid, "challenge": challenge, "signature": login_sig},
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def main() -> None:
    base_url = os.getenv("GEO_BASE_URL", "http://127.0.0.1:18000")
    eq = os.getenv("GEO_EQ", "UAH")
    scenario = os.getenv("GEO_SCENARIO", "greenfield-village-100-realistic-v2")
    intensity = int(os.getenv("GEO_INTENSITY", "90"))
    timeout_sec = float(os.getenv("GEO_TIMEOUT_SEC", "25"))

    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        headers = await _auth_headers(client)

        r = await client.post(
            "/api/v1/simulator/runs",
            headers=headers,
            json={"scenario_id": scenario, "mode": "real", "intensity_percent": intensity},
        )
        r.raise_for_status()
        run_id = r.json()["run_id"]

        url = f"/api/v1/simulator/runs/{run_id}/events"

        found: dict | None = None
        t0 = time.perf_counter()
        try:
            async with client.stream("GET", url, headers=headers, params={"equivalent": eq}) as s:
                s.raise_for_status()
                async for line in s.aiter_lines():
                    if time.perf_counter() - t0 > timeout_sec:
                        break
                    if not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ")
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    if payload.get("type") == "clearing.plan":
                        found = payload
                        break
        finally:
            try:
                await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=headers)
            except Exception:
                pass

        if not found:
            print("NO_CLEARING_PLAN")
            return

        steps = found.get("steps") if isinstance(found.get("steps"), list) else []
        print(json.dumps({"type": found.get("type"), "plan_id": found.get("plan_id"), "equivalent": found.get("equivalent"), "steps_n": len(steps)}, ensure_ascii=False))

        for i, st in enumerate(steps[:8]):
            if not isinstance(st, dict):
                continue
            he = st.get("highlight_edges")
            pe = st.get("particles_edges")
            print(
                json.dumps(
                    {
                        "i": i,
                        "at_ms": st.get("at_ms"),
                        "intensity_key": st.get("intensity_key"),
                        "highlight_edges_n": len(he) if isinstance(he, list) else 0,
                        "particles_edges_n": len(pe) if isinstance(pe, list) else 0,
                        "has_flash": "flash" in st,
                        "flash": st.get("flash"),
                        "highlight_head": (he[:3] if isinstance(he, list) else None),
                        "particles_head": (pe[:3] if isinstance(pe, list) else None),
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
