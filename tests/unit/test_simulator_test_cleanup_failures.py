"""Failure controls for the fixture cleanup, independent of its real PostgreSQL boundary test."""

import asyncio

import pytest

import app.core.simulator.storage as storage
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime import SimulatorRuntime
from tests.conftest import _stop_test_simulator_runs


@pytest.fixture
async def stand(monkeypatch):
    runtime = SimulatorRuntime()
    run = RunRecord(run_id="test-cleanup-control", scenario_id="minimal", mode="real", state="stopped")
    runtime._runs[run.run_id] = run

    async def no_io(*args, **kwargs):
        pass

    monkeypatch.setattr(storage, "upsert_run", no_io)
    monkeypatch.setattr(storage, "sync_artifacts", no_io)
    monkeypatch.setattr(runtime._real_runner, "flush_pending_storage", no_io)
    run._heartbeat_task = asyncio.create_task(asyncio.Event().wait())
    tasks = {run._heartbeat_task}
    yield runtime, run, tasks
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("broken_stop", ["raises", "no_op"])
async def test_failed_or_mocked_cleanup_is_loud_drains_tasks_and_can_retry(stand, monkeypatch, broken_stop):
    runtime, run, tasks = stand
    original = runtime.stop

    async def broken(run_id):
        if broken_stop == "raises":
            raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(runtime, "stop", broken)
    with pytest.raises(BaseExceptionGroup, match="simulator test cleanup failed"):
        await _stop_test_simulator_runs(runtime, [run], tasks)
    assert all(task.done() for task in tasks), "failure still drains tasks before fixtures disappear"
    assert runtime.get_run(run.run_id) is run, "failed cleanup must not discard its retry target"
    monkeypatch.setattr(runtime, "stop", original)
    await _stop_test_simulator_runs(runtime, [run], tasks)
    assert run._heartbeat_task is None


async def test_interrupted_cleanup_finishes_before_propagating_cancellation(stand, monkeypatch):
    runtime, run, tasks = stand
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_upsert(stored):
        entered.set()
        await release.wait()

    monkeypatch.setattr(storage, "upsert_run", held_upsert)
    cleanup = asyncio.create_task(_stop_test_simulator_runs(runtime, [run], tasks))
    await entered.wait()
    cleanup.cancel()
    await asyncio.sleep(0)
    cleanup.cancel()  # a second cancellation must not tear down the shielded drain
    await asyncio.sleep(0)
    assert not cleanup.done(), "an interrupted cleanup must keep its bindings until the final I/O ends"
    assert runtime.get_run(run.run_id) is run
    release.set()
    with pytest.raises(asyncio.CancelledError, match="cleanup was interrupted"):
        await cleanup
    assert all(task.done() for task in tasks)
    assert runtime._run_lifecycle._cleanup_in_progress == {}
    assert runtime.get_run(run.run_id) is run, "interruption remains a failure, not successful retirement"


async def test_a_detached_clearing_child_cannot_be_reported_as_a_clean_stop(stand):
    runtime, run, tasks = stand
    child = run._real_clearing_task = asyncio.create_task(asyncio.Event().wait())
    tasks.add(child)
    with pytest.raises(BaseExceptionGroup, match="simulator test cleanup failed") as failure:
        await _stop_test_simulator_runs(runtime, [run], tasks)
    assert "clearing task" in str(failure.value.exceptions[0])
    assert child.done(), "a failed cleanup still drains the child before clone disposal"
    assert runtime.get_run(run.run_id) is run


async def test_another_stop_still_finalizing_cannot_be_reported_as_drained(stand):
    runtime, run, tasks = stand
    runtime._run_lifecycle.pin_cleanup_locked(run.run_id)
    try:
        with pytest.raises(BaseExceptionGroup, match="simulator test cleanup failed") as failure:
            await _stop_test_simulator_runs(runtime, [run], tasks)
        assert "still in progress" in str(failure.value.exceptions[0])
        assert runtime.get_run(run.run_id) is run
    finally:
        runtime._run_lifecycle.unpin_cleanup_locked(run.run_id)


@pytest.fixture
def synchronous_owner(monkeypatch):
    from app.core.simulator.runtime import runtime

    async def no_io(*args, **kwargs):
        pass

    monkeypatch.setattr(storage, "upsert_run", no_io)
    monkeypatch.setattr(storage, "sync_artifacts", no_io)
    monkeypatch.setattr(runtime._real_runner, "flush_pending_storage", no_io)
    run = RunRecord(run_id="synchronous-cleanup-control", scenario_id="minimal", mode="real", state="running")
    runtime._runs[run.run_id] = run
    yield run
    assert run.state == "stopped", "taskless synchronous owners also run the full stop before finalizers"
    assert runtime.get_run(run.run_id) is run


def test_taskless_synchronous_test_has_no_function_loop_requirement(synchronous_owner):
    assert synchronous_owner.state == "running"
