"""T2416.2 (programme 024): every successful transition into `running` has exactly one live heartbeat.

Via the runtime facade the API calls; progress is `tick_index` and `_event_seq` (a new SSE event)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import app.core.simulator.runtime_impl as runtime_impl
import app.core.simulator.storage as simulator_storage
from app.core.simulator.runtime import runtime
from app.utils.exceptions import ConflictException
from tests.p019_support import TargetMismatch, require_target

RED = pytest.mark.xfail(raises=TargetMismatch, strict=True, reason="024 T2416.2: no heartbeat after resume/restart")


class _Clock:  # `asyncio` inside runtime_impl: `sleep` waits for a beat (no real time), the rest is asyncio's
    def __init__(self) -> None:
        self._tick, self.runs = asyncio.Event(), []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay, result=None):
        await self._tick.wait()

    async def progress(self, run) -> tuple[int, int]:
        for _ in range(10):  # settle, beat every sleeping heartbeat once, settle
            await asyncio.sleep(0)
        before = (run.tick_index, run._event_seq)
        tick, self._tick = self._tick, asyncio.Event()
        tick.set()
        for _ in range(10):
            await asyncio.sleep(0)
        return run.tick_index - before[0], run._event_seq - before[1]


@pytest.fixture
async def clock(monkeypatch):
    monkeypatch.setattr(simulator_storage, "upsert_run", AsyncMock())
    monkeypatch.setattr(simulator_storage, "sync_artifacts", AsyncMock())
    monkeypatch.setattr(runtime_impl, "asyncio", fake := _Clock())
    yield fake
    for run in fake.runs:
        await runtime.stop(run.run_id)


def _heartbeats(run_id: str) -> int:
    return sum(1 for t in asyncio.all_tasks() if t.get_name() == f"simulator-heartbeat:{run_id}" and not t.done())


async def _started(clock: _Clock, owner_id: str = ""):
    run_id = await runtime.create_run(scenario_id="greenfield-village-100-realistic-v2", mode="fixtures", intensity_percent=50, owner_id=owner_id)
    clock.runs.append(run := runtime.get_run(run_id))
    assert await clock.progress(run) >= (1, 1), "control: the stand sees the heartbeat tick"
    return run


@RED
async def test_stop_then_restart_ticks_and_emits_again(clock) -> None:
    run = await _started(clock)
    await runtime.stop(run.run_id)
    assert (await runtime.restart(run.run_id)).state == "running"
    progress = await clock.progress(run)
    require_target(progress[0] == 1 and progress[1] >= 1 and _heartbeats(run.run_id) == 1, f"after restart {progress=}")


@RED
@pytest.mark.parametrize("old_heartbeat", ["finished", "cancelled"])
async def test_error_then_resume_ticks_and_emits_again(clock, old_heartbeat) -> None:
    run = await _started(clock)
    run.state = "error"  # what the real runner's fail path does
    if old_heartbeat == "cancelled":  # fail path from another task: cancel, not awaited
        run._heartbeat_task.cancel()
    else:
        assert await clock.progress(run) == (0, 0) and run._heartbeat_task.done()
    assert (await runtime.resume(run.run_id)).state == "running"
    progress = await clock.progress(run)
    require_target(progress[0] == 1 and progress[1] >= 1 and _heartbeats(run.run_id) == 1, f"after resume {progress=}")


async def test_resume_keeps_the_live_heartbeat_and_stopped_stays_stopped(clock) -> None:
    run = await _started(clock)
    old = run._heartbeat_task
    await runtime.resume(run.run_id)  # already running
    await runtime.pause(run.run_id)
    await runtime.resume(run.run_id)
    assert run._heartbeat_task is old and _heartbeats(run.run_id) == 1
    assert (await clock.progress(run))[0] == 1  # one tick per beat: no second loop
    await runtime.stop(run.run_id)
    assert (await runtime.resume(run.run_id)).state == "stopped"
    assert _heartbeats(run.run_id) == 0 and (await clock.progress(run))[0] == 0


@RED
async def test_refused_restart_starts_no_worker_and_stays_stopped(clock) -> None:
    first = await _started(clock, "anon:p024-t2416-2")
    await runtime.stop(first.run_id)
    await _started(clock, "anon:p024-t2416-2")  # the owner's other active run
    with pytest.raises(ConflictException):
        await runtime.restart(first.run_id)
    assert _heartbeats(first.run_id) == 0 and (await clock.progress(first))[0] == 0
    require_target(first.state == "stopped", f"refused restart left state={first.state!r}")
