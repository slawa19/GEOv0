import asyncio
import json
import os
import time
from typing import Any

import httpx

ADMIN_TOKEN = os.getenv("GEO_ADMIN_TOKEN", "dev-admin-token-change-me")
BASE_URL = os.getenv("GEO_BASE_URL", "http://127.0.0.1:18000")
SCENARIO_ID = os.getenv("GEO_SCENARIO_ID", "greenfield-village-100-realistic-v2")
EQUIVALENT = os.getenv("GEO_EQ", "UAH")
INTENSITY = int(os.getenv("GEO_INTENSITY", "90"))
DURATION_SEC = float(os.getenv("GEO_DURATION_SEC", "140"))


def _h() -> dict[str, str]:
    return {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}


async def main() -> None:
    timeout = httpx.Timeout(connect=5.0, read=3600.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as client:
        r = await client.post(
            "/api/v1/simulator/runs",
            headers=_h(),
            json={"scenario_id": SCENARIO_ID, "mode": "real", "intensity_percent": INTENSITY},
        )
        r.raise_for_status()
        run_id = r.json()["run_id"]
        print(f"run_id={run_id} scenario={SCENARIO_ID} intensity={INTENSITY} eq={EQUIVALENT} base={BASE_URL}")

        t0 = time.time()
        last_event_at = None
        last_run_status_at = None
        last_tx_at = None

        async def poll_status() -> None:
            nonlocal last_event_at
            while time.time() - t0 < DURATION_SEC:
                try:
                    st = await client.get(f"/api/v1/simulator/runs/{run_id}", headers=_h())
                    st.raise_for_status()
                    j = st.json()
                    print(
                        "status",
                        f"t+{time.time()-t0:6.1f}s",
                        f"state={j.get('state')}",
                        f"sim_time_ms={j.get('sim_time_ms')}",
                        f"ops_sec={j.get('ops_sec')}",
                        f"q={j.get('queue_depth')}",
                        f"phase={j.get('current_phase')}",
                        f"last_event={j.get('last_event_type')}",
                        f"errors_total={j.get('errors_total')}",
                    )
                except Exception as e:
                    print("status_poll_error", repr(e))
                await asyncio.sleep(5.0)

        async def read_sse() -> None:
            nonlocal last_event_at, last_run_status_at, last_tx_at
            url = f"/api/v1/simulator/runs/{run_id}/events"
            async with client.stream("GET", url, headers=_h(), params={"equivalent": EQUIVALENT}) as resp:
                resp.raise_for_status()
                event_id = None
                async for line in resp.aiter_lines():
                    if time.time() - t0 >= DURATION_SEC:
                        break
                    if line.startswith("id:"):
                        event_id = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ")
                    last_event_at = time.time()
                    try:
                        payload: dict[str, Any] = json.loads(raw)
                    except Exception:
                        print("evt", f"t+{time.time()-t0:6.1f}s", "<bad_json>")
                        continue
                    typ = str(payload.get("type") or "")
                    if typ == "run_status":
                        last_run_status_at = time.time()
                        print(
                            "evt",
                            f"t+{time.time()-t0:6.1f}s",
                            typ,
                            f"sim_time_ms={payload.get('sim_time_ms')}",
                            f"ops_sec={payload.get('ops_sec')}",
                            f"phase={payload.get('current_phase')}",
                            f"last_event={payload.get('last_event_type')}",
                            f"errors_total={payload.get('errors_total')}",
                            f"id={event_id}",
                        )
                    elif typ in {"tx.updated", "tx.failed"}:
                        last_tx_at = time.time()
                        print(
                            "evt",
                            f"t+{time.time()-t0:6.1f}s",
                            typ,
                            f"id={event_id}",
                        )

        try:
            await asyncio.gather(poll_status(), read_sse())
        finally:
            try:
                await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=_h())
            except Exception:
                pass
            print(
                "done",
                f"elapsed={time.time()-t0:.1f}s",
                f"last_event_age={(time.time()-last_event_at):.1f}s" if last_event_at else "last_event_age=<none>",
                f"last_run_status_age={(time.time()-last_run_status_at):.1f}s" if last_run_status_at else "last_run_status_age=<none>",
                f"last_tx_age={(time.time()-last_tx_at):.1f}s" if last_tx_at else "last_tx_age=<none>",
            )


if __name__ == "__main__":
    asyncio.run(main())
