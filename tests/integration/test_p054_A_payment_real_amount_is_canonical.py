"""054-A-17: `payment-real` reports the amount it moved in the equivalent's canonical form, not the request's spelling.

The answer and the `tx.updated` event used to echo the raw request string (`"1"`, `"1.0"`), so one payment was spelled
differently from every other amount the simulator writes (`to_money_str`: the equivalent's step, `"1.00"` for UAH).

Real path: a real run (`POST /simulator/runs`), real HTTP `payment-real`, PostgreSQL mode B, the run's own SSE
subscription. Nothing is substituted.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B

pytestmark = MODE_B

SCENARIO = "riverside-town-50-realistic-v2"


def _admin() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _start_paused(client) -> str:
    r = await client.post(
        "/api/v1/simulator/runs",
        headers=_admin(),
        json={"scenario_id": SCENARIO, "mode": "real", "intensity_percent": 0},
    )
    assert r.status_code == 200, r.text
    run_id = str(r.json()["run_id"])
    observer = await runtime.subscribe(run_id, equivalent="UAH")
    try:
        async with asyncio.timeout(120):
            while True:
                event = await observer.queue.get()
                if event.get("type") == "run_status" and event.get("state") == "running" and event.get("sim_time_ms", 0) >= 1000:
                    await runtime.pause(run_id)
                    break
    finally:
        await runtime.unsubscribe(run_id, observer)
    return run_id


@pytest.mark.asyncio
async def test_p054_A_17_payment_real_amount_is_the_canonical_moved_value(client, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    run_id = await _start_paused(client)
    p1, p6 = f"{run_id}:p1", f"{run_id}:p6"  # line p6 -> p1 (creditor p6) with room: p1 can pay p6
    observer = await runtime.subscribe(run_id, equivalent="UAH")
    answers = []
    try:
        for raw in ("1", "1.0"):
            r = await client.post(
                f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
                headers=_admin(),
                json={"from_pid": p1, "to_pid": p6, "equivalent": "UAH", "amount": raw},
            )
            assert r.status_code == 200, r.text
            answers.append(r.json()["amount"])
        events = []
        while not observer.queue.empty():
            events.append(observer.queue.get_nowait())
    finally:
        await runtime.unsubscribe(run_id, observer)
    published = [e.get("amount") for e in events if e.get("type") == "tx.updated"]
    print("054-A-17 answers:", answers, "tx.updated amounts:", published)
    assert answers == ["1.00", "1.00"], answers
    assert published == ["1.00", "1.00"], published
