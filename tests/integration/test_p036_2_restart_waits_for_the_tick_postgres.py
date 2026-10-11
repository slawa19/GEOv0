"""BACKLOG 036-2 (a) and 036-3 (c): `restart` did not wait for the tick that was running.

THE DEFECT. `RunLifecycle.restart` reset the tick numbers, bumped the launch epoch and cleared the fired set while a tick
of the launch before was still between two of its awaits. The epoch guards of 036 B1/B2 protect ONE operation each (a
marking, a progress record, the pause decision); the tick as a whole was not bounded. A tick that reached its scripted
`clearing` event after the restart read the NEW epoch (`RealTick.run_scripted_clearings`), cleared the cycle and spent the
event of a launch that had not ticked yet; the pause decision of that tick compared the epoch it had started under and
did nothing, so the `pause_after` of the new launch was lost with its episode.

THE STAND. The PRODUCT `runtime._heartbeat_loop` and the PRODUCT `runtime.restart` over the real runner, tick, clearing
and PostgreSQL of `test_p036_b1_scripted_events_postgres.py`. The schedule is driven by events, never by the wall clock:
the loop's `asyncio.sleep` waits for a beat of the test; the tick is held at one of its own awaits (right after the due
scenario events are applied - before the money phase and before the clearing event) until the test lets it go. Nothing of
the tick is replaced or reported "as if": the tick that straddles the restart is the real one, end to end.

Not shown here: the SSE serialisation and the artifacts of the tick (the stand's stand-ins), a restart through REST.
"""

from __future__ import annotations

import asyncio

import pytest

import app.core.simulator.run_lifecycle as run_lifecycle
import app.core.simulator.runtime_impl as runtime_impl
import app.core.simulator.storage as simulator_storage
from app.core.simulator.runtime import runtime
from app.utils.exceptions import ConflictException
from tests.integration.test_p036_b1_scripted_events_postgres import (  # noqa: F401 - `factory` is a fixture
    CYCLE_DEBTS,
    CYCLE_LINES,
    _clearing_event,
    _debts,
    _stand,
    factory,
)
from tests.p021_support import require_target

CAPTION = {"ru": "р", "en": "e"}
_WAIT = 60.0  # a bound for a broken stand, never a pace: every wait below ends on an event


class _Stage:
    """The product heartbeat of the stand's run, one tick per `beat`, with the first tick held inside itself."""

    def __init__(self, monkeypatch, run, runner) -> None:
        self.run, self.runner = run, runner
        self._beats = asyncio.Semaphore(0)
        self.tick_is_inside = asyncio.Event()
        self.let_the_tick_go = asyncio.Event()
        self._finished = asyncio.Semaphore(0)
        self.ticks: list[dict] = []  # one record per finished tick

        stage = self

        class _AsyncioWithBeats:
            @staticmethod
            async def sleep(_delay, *_a, **_kw):
                await stage._beats.acquire()

            def __getattr__(self, name):
                return getattr(asyncio, name)

        async def no_upsert(*_a, **_kw) -> None:
            return None

        apply_events = runner._apply_due_scenario_events

        async def apply_then_hold(session, **kwargs):
            await apply_events(session, **kwargs)
            if not stage.tick_is_inside.is_set():
                stage.tick_is_inside.set()
                await stage.let_the_tick_go.wait()

        tick = runner.tick_real_mode

        async def observed_tick(run_id):
            started_under = int(run._launch_epoch)
            try:
                await tick(run_id)
            finally:
                stage.ticks.append({
                    "started_under_epoch": started_under,
                    "state": str(run.state),
                    "fired": sorted(run._real_fired_scenario_event_indexes),
                    "progress_epochs": sorted({r.get("epoch") for r in run._real_story_progress.values()}),
                })
                stage._finished.release()

        monkeypatch.setattr(runtime_impl, "asyncio", _AsyncioWithBeats())
        monkeypatch.setattr(simulator_storage, "upsert_run", no_upsert)
        monkeypatch.setattr(runner, "_apply_due_scenario_events", apply_then_hold)
        monkeypatch.setattr(runner, "tick_real_mode", observed_tick)
        monkeypatch.setattr(runtime, "_real_runner", runner)
        monkeypatch.setattr(runtime, "publish_run_status", lambda _run_id: None)
        monkeypatch.setattr(runtime._run_lifecycle, "_publish_run_status", lambda _run_id: None)
        monkeypatch.setattr(runtime._artifacts, "start_events_writer", lambda _run_id: None)
        monkeypatch.setitem(runtime._runs, run.run_id, run)
        run.sim_time_ms = 0
        run._heartbeat_task = asyncio.create_task(runtime._heartbeat_loop(run.run_id), name=f"simulator-heartbeat:{run.run_id}")
        self.heartbeat = run._heartbeat_task

    def beat(self) -> None:
        self._beats.release()

    async def tick_finished(self) -> dict:
        await asyncio.wait_for(self._finished.acquire(), _WAIT)
        return self.ticks[-1]

    async def hold_the_first_tick(self) -> None:
        self.beat()
        await asyncio.wait_for(self.tick_is_inside.wait(), _WAIT)

    def snapshot(self) -> tuple:
        run = self.run
        return (int(run._launch_epoch), int(run.tick_index), str(run.state), sorted(run._real_fired_scenario_event_indexes))

    async def close(self) -> None:
        self.let_the_tick_go.set()
        tasks = {t for t in (self.heartbeat, self.run._heartbeat_task) if t is not None}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _settle() -> None:
    """Let every task that CAN run do so. On the code before the fix `restart` has no await that suspends in this stand
    (the storage upsert is a stand-in), so it completes on its first turn; fifty turns is not a pace."""

    for _ in range(50):
        await asyncio.sleep(0)


async def _cycle_stage(factory, monkeypatch):  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS,
        lambda e, q: [{**_clearing_event(e), "caption": CAPTION, "pause_after": True}],
    )
    return eq, p, run, _Stage(monkeypatch, run, runner)


@pytest.mark.asyncio
async def test_a_restart_during_a_tick_takes_effect_after_the_tick_and_the_new_launch_keeps_its_episode(factory, monkeypatch) -> None:  # noqa: F811
    """TARGET (red before the fix). The tick of launch 0 is inside itself when `restart` is asked for. The restart happens
    AFTER that tick: the tick's clearing event, its cycle and its pause belong to launch 0; launch 1 starts with its
    episode due, runs it on its own first tick and pauses after it."""

    eq, p, run, stage = await _cycle_stage(factory, monkeypatch)
    try:
        await stage.hold_the_first_tick()
        assert stage.snapshot() == (0, 1, "running", []), stage.snapshot()  # control: tick 1 of launch 0 is running

        restart = asyncio.create_task(runtime.restart(run.run_id))
        await _settle()
        while_the_tick_runs = (restart.done(), int(run._launch_epoch))

        stage.let_the_tick_go.set()
        old_tick = await stage.tick_finished()
        status = await asyncio.wait_for(restart, _WAIT)
        after_the_restart = stage.snapshot()
        debts_after_the_old_tick = await _debts(factory, eq, p)

        stage.beat()
        new_tick = await stage.tick_finished()
    finally:
        await stage.close()

    assert debts_after_the_old_tick == {}, debts_after_the_old_tick  # control: the real clearing ran and cleared the cycle
    assert status.state == "running" and run._launch_epoch == 1, (status, run._launch_epoch)  # control: a real restart
    require_target(
        while_the_tick_runs == (False, 0)
        and old_tick == {"started_under_epoch": 0, "state": "paused", "fired": [0], "progress_epochs": [0]}
        and after_the_restart == (1, 0, "running", [])
        and new_tick == {"started_under_epoch": 1, "state": "paused", "fired": [0], "progress_epochs": [1]},
        f"while the tick of launch 0 was running: (restart done, epoch) {while_the_tick_runs}, expected (False, 0); "
        f"that tick ended as {old_tick}; right after the restart (epoch, tick, state, fired) {after_the_restart}, "
        f"expected (1, 0, 'running', []); the first tick of launch 1 ended as {new_tick}, expected its episode spent "
        f"by itself and the run paused after it",
    )


@pytest.mark.asyncio
async def test_a_restart_that_cannot_get_the_tick_boundary_in_time_is_refused_and_changes_nothing(factory, monkeypatch) -> None:  # noqa: F811
    """A tick that does not end is not waited for without a bound: 409 with `conflict_kind: tick_in_progress`, the run as
    it was (024 T2416.2: a refused restart mutates nothing), the same heartbeat. Once the tick ends a restart goes through."""

    eq, p, run, stage = await _cycle_stage(factory, monkeypatch)
    monkeypatch.setattr(run_lifecycle, "RESTART_TICK_WAIT_SECONDS", 0.05)
    try:
        await stage.hold_the_first_tick()
        before = stage.snapshot()

        with pytest.raises(ConflictException) as refused:
            await asyncio.wait_for(runtime.restart(run.run_id), _WAIT)
        after_the_refusal = (stage.snapshot(), run._heartbeat_task is stage.heartbeat, stage.heartbeat.done())

        stage.let_the_tick_go.set()
        await stage.tick_finished()
        status = await asyncio.wait_for(runtime.restart(run.run_id), _WAIT)
    finally:
        await stage.close()

    assert refused.value.details["conflict_kind"] == "tick_in_progress", refused.value.details
    assert after_the_refusal == (before, True, False), (before, after_the_refusal)
    assert status.state == "running" and run._launch_epoch == 1  # the boundary was not left taken by the refused request


@pytest.mark.asyncio
async def test_a_restart_request_cancelled_while_it_waits_changes_nothing_and_leaves_the_boundary_free(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, stage = await _cycle_stage(factory, monkeypatch)
    try:
        await stage.hold_the_first_tick()
        before = stage.snapshot()

        abandoned = asyncio.create_task(runtime.restart(run.run_id))
        await _settle()
        assert not abandoned.done()  # control: it is waiting
        abandoned.cancel()
        await asyncio.gather(abandoned, return_exceptions=True)
        after_the_cancel = stage.snapshot()

        stage.let_the_tick_go.set()
        old_tick = await stage.tick_finished()
        status = await asyncio.wait_for(runtime.restart(run.run_id), _WAIT)  # a boundary leaked by the cancel would refuse this
        stage.beat()
        new_tick = await stage.tick_finished()
    finally:
        await stage.close()

    assert abandoned.cancelled() and after_the_cancel == before, (abandoned, before, after_the_cancel)
    assert old_tick["started_under_epoch"] == 0 and old_tick["state"] == "paused", old_tick
    assert status.state == "running" and new_tick == {"started_under_epoch": 1, "state": "paused", "fired": [0], "progress_epochs": [1]}, new_tick


@pytest.mark.asyncio
async def test_two_restarts_asked_for_during_one_tick_both_happen_after_it(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, stage = await _cycle_stage(factory, monkeypatch)
    try:
        await stage.hold_the_first_tick()
        first = asyncio.create_task(runtime.restart(run.run_id))
        second = asyncio.create_task(runtime.restart(run.run_id))
        await _settle()
        while_the_tick_runs = (first.done(), second.done(), int(run._launch_epoch))

        stage.let_the_tick_go.set()
        old_tick = await stage.tick_finished()
        await asyncio.wait_for(asyncio.gather(first, second), _WAIT)
        after = stage.snapshot()
        stage.beat()
        new_tick = await stage.tick_finished()
    finally:
        await stage.close()

    assert while_the_tick_runs == (False, False, 0), while_the_tick_runs
    assert old_tick["started_under_epoch"] == 0 and old_tick["progress_epochs"] == [0], old_tick
    assert after == (2, 0, "running", []), after
    assert new_tick == {"started_under_epoch": 2, "state": "paused", "fired": [0], "progress_epochs": [2]}, new_tick


@pytest.mark.asyncio
async def test_a_stop_while_a_restart_waits_ends_the_tick_and_the_restart_then_starts_the_run_with_one_heartbeat(factory, monkeypatch) -> None:  # noqa: F811
    """`stop` cancels the heartbeat and so the tick; the waiting restart gets the boundary from the cancelled tick and
    restarts the stopped run (the order the two requests were served in: stop, then restart). Nobody waits for ever."""

    eq, p, run, stage = await _cycle_stage(factory, monkeypatch)

    async def nothing(*_a, **_kw) -> None:
        return None

    for name in ("stop_events_writer", "finalize_run_artifacts"):
        monkeypatch.setattr(runtime._artifacts, name, nothing)
    try:
        await stage.hold_the_first_tick()
        restart = asyncio.create_task(runtime.restart(run.run_id))
        await _settle()
        assert not restart.done()  # control: it is waiting for the tick

        await asyncio.wait_for(runtime.stop(run.run_id), _WAIT)
        status = await asyncio.wait_for(restart, _WAIT)
        live = [t for t in asyncio.all_tasks() if t.get_name() == f"simulator-heartbeat:{run.run_id}" and not t.done()]
        after = stage.snapshot()
        stage.let_the_tick_go.set()
        stage.beat()
        new_tick = await stage.tick_finished()  # the cancelled tick was recorded first; this waits for it, then the new one
        if new_tick["started_under_epoch"] == 0:
            new_tick = await stage.tick_finished()
    finally:
        await stage.close()

    assert stage.heartbeat.done() and stage.ticks[0]["started_under_epoch"] == 0, stage.ticks  # the old tick was cancelled
    assert status.state == "running" and after == (1, 0, "running", []), (status, after)
    assert len(live) == 1 and live[0] is not stage.heartbeat, live
    assert new_tick["started_under_epoch"] == 1 and new_tick["progress_epochs"] == [1], new_tick
