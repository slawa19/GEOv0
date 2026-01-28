import asyncio
from datetime import datetime, timezone

import pytest

from app.core.simulator.runtime import runtime


@pytest.mark.asyncio
async def test_subscribe_replays_events_after_last_event_id() -> None:
    scenario_id = runtime.list_scenarios()[0].scenario_id
    run_id = await runtime.create_run(scenario_id=scenario_id, mode="fixtures", intensity_percent=50)
    try:
        run = runtime.get_run(run_id)

        evt1 = {
            "event_id": runtime._next_event_id(run),  # type: ignore[attr-defined]
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "tx.updated",
            "equivalent": "USD",
        }
        runtime._broadcast(run_id, evt1)  # type: ignore[attr-defined]

        evt2 = {
            "event_id": runtime._next_event_id(run),  # type: ignore[attr-defined]
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "tx.updated",
            "equivalent": "USD",
        }
        runtime._broadcast(run_id, evt2)  # type: ignore[attr-defined]

        sub = await runtime.subscribe(run_id, equivalent="USD", after_event_id=str(evt1["event_id"]))
        try:
            got = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            assert got.get("event_id") == evt2["event_id"]
        finally:
            await runtime.unsubscribe(run_id, sub)
    finally:
        await runtime.stop(run_id)
