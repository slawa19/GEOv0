"""044 stage 2 (decision 2): a run nobody watches is stopped by its own heartbeat, reason `idle_no_subscribers`.

TARGET (spec 044, decision 2 and the §19.2 answers): a run with no SSE subscriber for `SIMULATOR_RUN_IDLE_STOP_SEC`
goes to `stopped` with `stop_source = "server"`, `stop_reason = "idle_no_subscribers"`; the check sits in the existing
`_heartbeat_loop` BEFORE the paused `continue` (a paused run nobody watches is stopped too); the window is counted from
the LAST subscriber leaving (a reconnect within the window resets it), and a run with a subscriber is never stopped.

HOW, WITHOUT `sleep`. The production `runtime._heartbeat_loop`, `runtime.subscribe` / `unsubscribe` and `runtime.stop`
of the process-wide runtime run against a virtual clock: the module's `asyncio.sleep` advances it by the requested
duration (and yields once), and the runtime's idle clock (`runtime._idle_now`) reads it. The tick itself is a stub
(counts ticks): what is under test is the loop's decision, not the money path. Storage writes and the final flush are
stubbed; nothing touches the database.

WHAT IT DOES NOT SEE: a real SSE stream over HTTP (subscriptions are made through the runtime API the route uses), the
UI's reaction (`simulator-ui/v2`, `runStatusErrorText`), and many runs at once.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.core.simulator.runtime_impl as runtime_impl
import app.core.simulator.storage as simulator_storage
from app.core.simulator.models import RunRecord, note_subscribers_locked
from app.core.simulator.runtime import runtime

THRESHOLD_S = 600
RUN_ID = "p044-s2-idle-run"
GUARD_SLEEPS = 5000


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = 0
        self.ticks = 0
        self.hooks: dict[float, list] = {}

    def at(self, t: float, fn) -> None:
        self.hooks.setdefault(float(t), []).append(fn)


def _install(monkeypatch, *, state: str = "running", stop_after_s: float | None = None) -> tuple[RunRecord, _Clock]:
    clock = _Clock()
    run = RunRecord(run_id=RUN_ID, scenario_id="p044-s2-idle", mode="real", state=state)
    real_sleep = asyncio.sleep
    pending: list = []

    async def fake_sleep(delay, *_a, **_kw):
        clock.sleeps += 1
        assert clock.sleeps < GUARD_SLEEPS, "the heartbeat loop did not stop"  # a broken stand fails, it does not hang
        before = clock.now
        clock.now += float(delay)
        for t in sorted(k for k in clock.hooks if before < k <= clock.now):
            pending.extend(clock.hooks.pop(t))
        while pending:
            await pending.pop(0)()
        if stop_after_s is not None and clock.now >= stop_after_s and run.state in ("running", "paused"):
            run.state = "stopped"  # the loop's own exit (the test's end), read after this sleep
        await real_sleep(0)

    class _AsyncioWithVirtualSleep:
        sleep = staticmethod(fake_sleep)

        def __getattr__(self, name):
            return getattr(asyncio, name)

    async def fake_tick(_run_id: str) -> None:
        clock.ticks += 1

    async def no_io(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(runtime_impl, "asyncio", _AsyncioWithVirtualSleep())
    monkeypatch.setattr(runtime._real_runner, "tick_real_mode", fake_tick)
    monkeypatch.setattr(runtime._real_runner, "flush_pending_storage", no_io)
    monkeypatch.setattr(simulator_storage, "upsert_run", no_io)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", no_io)
    monkeypatch.setattr(runtime, "_run_idle_stop_sec", THRESHOLD_S)
    monkeypatch.setattr(runtime, "_idle_now", lambda: clock.now)
    monkeypatch.setitem(runtime._runs, RUN_ID, run)
    with runtime._lock:
        note_subscribers_locked(run, clock.now)  # what `create_run` does when it registers the run
    return run, clock


async def _watch() -> tuple:
    """A subscriber that reads its stream, as the SSE route does (an unread queue fills and is closed by overflow)."""

    sub = await runtime.subscribe(RUN_ID, equivalent="UAH")

    async def drain() -> None:
        while not sub.closed:
            await sub.queue.get()

    return sub, asyncio.create_task(drain())


async def _loop(run: RunRecord) -> None:
    task = asyncio.create_task(runtime._heartbeat_loop(RUN_ID))
    run._heartbeat_task = task
    await task


@pytest.mark.asyncio
async def test_a_run_without_subscribers_stops_after_the_threshold(monkeypatch) -> None:
    run, clock = _install(monkeypatch)
    stopped_at: list[float] = []
    orig_stop = runtime.stop

    async def stop_spy(run_id, **kw):
        stopped_at.append(clock.now)
        return await orig_stop(run_id, **kw)

    monkeypatch.setattr(runtime, "stop", stop_spy)

    await _loop(run)

    assert run.state == "stopped", run.state
    assert (run.stop_source, run.stop_reason) == ("server", "idle_no_subscribers")
    assert stopped_at == [pytest.approx(THRESHOLD_S)], stopped_at
    assert clock.ticks == THRESHOLD_S - 1, "it ticked until the threshold (non-vacuity), not one tick after"
    status = runtime.get_run_status(RUN_ID)
    assert (status.state, status.stop_reason, status.stop_source) == ("stopped", "idle_no_subscribers", "server")
    assert run._heartbeat_task is None, "the loop detached itself; stop did not await its own task"


@pytest.mark.asyncio
async def test_counter_check_a_run_with_a_subscriber_is_not_stopped(monkeypatch) -> None:
    """The counter-check of the rule: with one live subscriber and the clock far past the threshold, nothing stops."""

    run, clock = _install(monkeypatch, stop_after_s=3 * THRESHOLD_S)
    sub, reader = await _watch()
    assert run._subs == [sub] and run._no_subscribers_since is None

    await _loop(run)
    reader.cancel()

    assert run.stop_reason is None, run.stop_reason
    assert clock.ticks >= 3 * THRESHOLD_S - 2, clock.ticks  # it kept ticking the whole time


@pytest.mark.asyncio
async def test_a_paused_run_without_subscribers_is_stopped_too(monkeypatch) -> None:
    run, clock = _install(monkeypatch, state="paused")

    await _loop(run)

    assert (run.state, run.stop_reason) == ("stopped", "idle_no_subscribers")
    assert clock.ticks == 0, "a paused run does not tick on its way to the idle stop"
    assert clock.now == pytest.approx(THRESHOLD_S)


@pytest.mark.asyncio
async def test_a_reconnect_within_the_window_resets_it_counted_from_the_last_leave(monkeypatch) -> None:
    run, clock = _install(monkeypatch)
    subs: list = []

    async def join():
        subs.append(await _watch())

    async def leave():
        sub, reader = subs.pop()
        reader.cancel()
        await runtime.unsubscribe(RUN_ID, sub)

    clock.at(100, join)
    clock.at(400, leave)    # idle from 400
    clock.at(900, join)     # 500 s later - inside the window: reset
    clock.at(1000, leave)   # idle from 1000 -> due at 1600, not at 1000 (from 400) nor 600 (from creation)

    await _loop(run)

    assert (run.state, run.stop_reason) == ("stopped", "idle_no_subscribers")
    assert clock.now == pytest.approx(1000 + THRESHOLD_S), clock.now


@pytest.mark.asyncio
async def test_a_subscription_closed_by_overflow_starts_the_window(monkeypatch) -> None:
    """The third removal point: the broadcaster's close of a subscriber whose queue is full."""

    run, clock = _install(monkeypatch)
    subs: list = []
    closed_at: list[float] = []
    orig_close = runtime._sse._close_subscription_locked

    def close_spy(**kw):
        closed_at.append(clock.now)
        return orig_close(**kw)

    monkeypatch.setattr(runtime._sse, "_close_subscription_locked", close_spy)

    async def join():  # a subscriber that never reads: the heartbeat's statuses fill its queue
        subs.append(await runtime.subscribe(RUN_ID, equivalent="UAH"))

    clock.at(100, join)

    await _loop(run)

    assert subs and subs[0].closed and subs[0].close_reason == "live_queue_overflow"
    assert len(closed_at) == 1 and closed_at[0] > 100, closed_at
    assert (run.state, run.stop_reason) == ("stopped", "idle_no_subscribers")
    assert clock.now == pytest.approx(closed_at[0] + THRESHOLD_S), (clock.now, closed_at)


@pytest.mark.asyncio
async def test_zero_disables_the_idle_stop(monkeypatch) -> None:
    run, clock = _install(monkeypatch, stop_after_s=2 * THRESHOLD_S)
    monkeypatch.setattr(runtime, "_run_idle_stop_sec", 0)

    await _loop(run)

    assert run.stop_reason is None
    assert clock.ticks >= 2 * THRESHOLD_S - 2


def test_shutdown_and_the_create_path_record_the_window(monkeypatch) -> None:
    """The fourth writer (shutdown clears every subscription) and the rule itself, without a loop."""

    run = RunRecord(run_id="p044-s2-note", scenario_id="x", mode="real", state="running")
    run._subs.append(SimpleNamespace())
    note_subscribers_locked(run, 5.0)
    assert run._no_subscribers_since is None
    run._subs.clear()
    note_subscribers_locked(run, 7.0)
    assert run._no_subscribers_since == 7.0


@pytest.mark.asyncio
async def test_create_starts_the_window_and_restart_and_resume_restart_it(monkeypatch) -> None:
    """The writers on the lifecycle side: `create_run`, `restart`, `resume` (from `paused`) - through the production
    lifecycle, the clock standing still between them except where moved."""

    async def no_io(*_a, **_kw) -> None:
        return None

    now = {"t": 1000.0}
    monkeypatch.setattr(simulator_storage, "upsert_run", no_io)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", no_io)
    monkeypatch.setattr(runtime._real_runner, "flush_pending_storage", no_io)
    monkeypatch.setattr(runtime, "_idle_now", lambda: now["t"])
    monkeypatch.setattr(runtime, "_max_active_runs", 0)
    monkeypatch.setattr(runtime, "_max_active_runs_per_owner", 0)
    run_id = await runtime.create_run(scenario_id="clearing-demo-10", mode="real", owner_id="p044-s2-owner")
    try:
        run = runtime.get_run(run_id)
        assert run._no_subscribers_since == 1000.0
        now["t"] = 1500.0
        await runtime.pause(run_id)
        assert run._no_subscribers_since == 1000.0, "a pause does not restart the window"
        await runtime.resume(run_id)
        assert run._no_subscribers_since == 1500.0
        await runtime.stop(run_id)
        now["t"] = 2000.0
        await runtime.restart(run_id)
        assert run._no_subscribers_since == 2000.0
    finally:
        await runtime.stop(run_id)
        with runtime._lock:
            runtime._runs.pop(run_id, None)
        runtime.clear_active_run_id(owner_id="p044-s2-owner")
