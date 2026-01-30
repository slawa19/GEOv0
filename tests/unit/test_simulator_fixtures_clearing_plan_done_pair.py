import asyncio

import pytest

from app.core.simulator.runtime import runtime


@pytest.mark.asyncio
async def test_fixtures_mode_emits_clearing_plan_and_done_with_matching_plan_id() -> None:
    run_id = await runtime.create_run(
        scenario_id="greenfield-village-100",
        mode="fixtures",
        intensity_percent=50,
    )

    # Default fixtures-mode clearing cadence starts later (25s).
    # For test speed, force the first clearing cycle to trigger immediately.
    run = runtime.get_run(run_id)
    run._next_clearing_at_ms = 0
    run._clearing_pending_done_at_ms = None
    run._clearing_pending_plan_id_by_eq.clear()

    sub = await runtime.subscribe(run_id, equivalent="UAH")
    try:
        plan_id: str | None = None
        seen_plan = False
        seen_done = False

        async def _read_until() -> None:
            nonlocal plan_id, seen_plan, seen_done
            deadline = asyncio.get_running_loop().time() + 6.0
            while asyncio.get_running_loop().time() < deadline:
                try:
                    evt = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                if evt.get("type") == "clearing.plan" and evt.get("equivalent") == "UAH":
                    seen_plan = True
                    plan_id = evt.get("plan_id")
                    assert isinstance(plan_id, str) and plan_id

                if evt.get("type") == "clearing.done" and evt.get("equivalent") == "UAH":
                    seen_done = True
                    assert evt.get("plan_id") == plan_id

                if seen_plan and seen_done:
                    return

            raise AssertionError("Did not receive both clearing.plan and clearing.done within timeout")

        await _read_until()
    finally:
        await runtime.unsubscribe(run_id, sub)
        await runtime.stop(run_id)
