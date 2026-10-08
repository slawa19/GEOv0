"""034 S1b, F-034-10: an exception in the heartbeat does not leave a run `running` with nothing ticking it.

WHAT WAS WRONG (on `0f248b9c`). `_heartbeat_loop` (`app/core/simulator/runtime_impl.py`) caught `CancelledError` and
nothing else. Any other exception of an iteration - here the status publication, which the loop calls bare - ended
the task; the run stayed `running`, no tick ever came again, `last_error` said nothing and no log line was written
by the application (only asyncio's "Task exception was never retrieved", if and when the task was collected).

THE STAND is 024's (`test_p024_heartbeat_follows_every_entry_into_running.py`): the runtime singleton, a fixtures-mode
run, and a clock that replaces `asyncio.sleep` inside `runtime_impl` with a beat the test gives - no real time.

THE TARGET is an invariant, not a mechanism: after a failed iteration the run is either still being ticked or is no
longer `running` - and if it was stopped, it says why (`last_error`) and the failure is in the log.
"""

from __future__ import annotations

import logging

import pytest

import app.core.simulator.runtime_impl as runtime_impl
import app.core.simulator.storage as simulator_storage
from app.core.simulator.runtime import runtime
from tests.unit.test_p024_heartbeat_follows_every_entry_into_running import (  # noqa: F401 - `clock` is a fixture
    _heartbeats,
    _started,
    clock,
)


def _fail_the_status_publication(monkeypatch) -> list[str]:
    """Every status publication raises, as it does for a run the registry no longer has."""

    failed: list[str] = []

    def failing(run_id: str) -> str:
        failed.append(run_id)
        raise RuntimeError("p034: the status could not be published")

    monkeypatch.setattr(runtime, "publish_run_status", failing)
    return failed


@pytest.mark.asyncio
async def test_a_failed_heartbeat_iteration_does_not_leave_the_run_running_without_a_heartbeat(clock, monkeypatch, caplog) -> None:  # noqa: F811
    run = await _started(clock)
    failed = _fail_the_status_publication(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=runtime_impl.logger.name):
        ticked, _events = await clock.progress(run)

    # Controls: the iteration ran (it ticked) and it is the status publication that failed in it.
    assert ticked == 1 and failed, (ticked, failed)

    state, alive = run.state, _heartbeats(run.run_id)
    logged = [r.getMessage() for r in caplog.records
              if r.name == runtime_impl.logger.name and r.levelno >= logging.ERROR and r.exc_info]
    assert (state != "running" or alive == 1) and (state == "running" or (run.last_error and logged)), (
        f"after an exception in a heartbeat iteration the run is {state!r} with {alive} live heartbeat(s), "
        f"last_error {run.last_error!r}, errors logged with the exception: {logged}. Expected: still ticked, or "
        f"not `running` with `last_error` set and the failure logged"
    )
    # What goes OUT names the type of the exception and nothing of its text (`last_error` is published, served and
    # stored; an exception's text carries paths and SQL); the text is in the log, with the traceback.
    assert run.last_error == {"code": "HEARTBEAT_FAILED", "message": "The heartbeat failed: RuntimeError",
                              "at": run.last_error["at"]}, run.last_error
    assert any("the status could not be published" in str(r.exc_info[1]) for r in caplog.records if r.exc_info)


def _record_the_stored_states(monkeypatch) -> list[tuple[str, str]]:
    """(run_id, state) of every row the runtime hands to `simulator_storage.upsert_run`, in order. The storage
    writer is the boundary here (the stand has no database): what it is given is what the row would say."""

    stored: list[tuple[str, str]] = []

    async def recording(run) -> None:
        stored.append((run.run_id, str(run.state)))

    monkeypatch.setattr(simulator_storage, "upsert_run", recording)
    return stored


@pytest.mark.asyncio
async def test_a_run_failed_by_its_heartbeat_is_stored_as_failed_when_no_status_can_be_published(clock, monkeypatch) -> None:  # noqa: F811
    """The whole publication is broken - `SseBroadcast.publish_event`, not only the loop's own call - so `fail_run`,
    which the heartbeat takes, fails as well: it moves the state and then raises on its own status publication,
    before it stores anything. The run must still be `error` in memory AND in what is stored - a row that keeps
    saying `running` for a run nobody ticks is the defect this slice removes, one layer down."""

    run = await _started(clock)
    stored = _record_the_stored_states(monkeypatch)
    errors_before = run.errors_total

    def broken(*_args, **_kwargs):
        raise RuntimeError("p034: nothing can be published")

    with monkeypatch.context() as outage:
        outage.setattr(runtime._sse, "publish_event", broken)
        await clock.progress(run)

    assert (run.state, _heartbeats(run.run_id)) == ("error", 0), (run.state, run.last_error)
    assert run.last_error["code"] == "HEARTBEAT_FAILED" and "nothing can be published" not in run.last_error["message"], run.last_error
    assert run.errors_total == errors_before + 1, (errors_before, run.errors_total)  # counted once, not per layer
    assert stored and stored[-1] == (run.run_id, "error"), (
        f"the heartbeat failed the run while no status could be published; rows handed to the storage for it, in "
        f"order: {stored}. Expected the last one to say `error`"
    )


@pytest.mark.asyncio
async def test_the_run_is_marked_failed_even_when_the_failure_path_cannot_start(clock, monkeypatch) -> None:  # noqa: F811
    """`fail_run` raises before it has moved anything. The run still does not stay `running`: the state is set
    directly, with the reason, and stored."""

    run = await _started(clock)
    stored = _record_the_stored_states(monkeypatch)
    _fail_the_status_publication(monkeypatch)

    async def _fail_run_fails(_run_id: str, *, code: str, message: str) -> None:
        raise RuntimeError("p034: the failure path failed")

    monkeypatch.setattr(runtime._real_runner, "fail_run", _fail_run_fails)
    errors_before = run.errors_total
    await clock.progress(run)

    assert (run.state, _heartbeats(run.run_id)) == ("error", 0), (run.state, run.last_error)
    assert run.last_error["code"] == "HEARTBEAT_FAILED" and run.errors_total == errors_before + 1, run.last_error
    assert run.last_error["message"] == "The heartbeat failed: RuntimeError", run.last_error
    assert stored[-1] == (run.run_id, "error"), stored


@pytest.mark.asyncio
async def test_a_run_stopped_by_a_heartbeat_failure_can_be_resumed_and_ticks_again(clock, monkeypatch) -> None:  # noqa: F811
    """The way back, once the cause is gone: `resume` re-enters `running` with one live heartbeat (024 T2416.2)."""

    run = await _started(clock)
    with monkeypatch.context() as broken:
        _fail_the_status_publication(broken)
        await clock.progress(run)
    assert _heartbeats(run.run_id) == 0  # control: the failed iteration ended the loop, before and after the fix

    status = await runtime.resume(run.run_id)
    ticked, events = await clock.progress(run)
    assert (status.state, run.state, _heartbeats(run.run_id)) == ("running", "running", 1) and ticked == 1 and events >= 1, (
        f"after the failure was gone and the run resumed: resume answered {status.state!r}, the run is "
        f"{run.state!r} with {_heartbeats(run.run_id)} heartbeat(s), ticked {ticked}, events {events}"
    )
