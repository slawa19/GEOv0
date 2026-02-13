from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator


class _AsyncSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_static_clearing_hard_timeout_cancels_and_does_not_leak_task(monkeypatch) -> None:
    coordinator = RealTickClearingCoordinator(
        lock=threading.Lock(),
        logger=logging.getLogger(__name__),
        clearing_every_n_ticks=1,
        real_clearing_time_budget_ms=250,
        clearing_policy="static",
    )

    # Make the timeout tiny so the test is fast.
    monkeypatch.setattr(coordinator, "compute_static_clearing_hard_timeout_sec", lambda *, safe_int_env: 0.02)

    session = _AsyncSession()
    run = RunRecord(
        run_id="run-1",
        scenario_id="scenario-1",
        mode="real",
        state="running",
        tick_index=0,
    )

    started = asyncio.Event()
    cancelled = asyncio.Event()
    task_holder: dict[str, asyncio.Task[object] | None] = {"task": None}

    async def run_clearing() -> dict[str, float]:
        task_holder["task"] = asyncio.current_task()
        started.set()
        try:
            # Simulate a clearing that would exceed the hard timeout.
            await asyncio.sleep(10)
            return {"USD": 0.0}
        except asyncio.CancelledError:
            cancelled.set()
            raise

    # Trigger static clearing on this tick.
    res = await coordinator.maybe_run_clearing(
        session=session,
        run_id=str(run.run_id),
        run=run,
        equivalents=["USD"],
        planned_len=1,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda _k, default: int(default),
        run_clearing=run_clearing,
        payments_result=None,
    )

    assert res == {"USD": 0.0}

    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)

    # Contract: no background clearing task should remain attached to the run.
    assert run._real_clearing_task is None

    t = task_holder["task"]
    assert t is not None
    assert t.done()
    assert t.cancelled()
