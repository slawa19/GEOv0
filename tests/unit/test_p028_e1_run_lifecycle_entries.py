"""028 E1: every entry back into `running` restores what `create` gives a run (F-028-4, F-028-5).

F-028-4: `restart` after `stop` starts the events writer again - an event after it reaches `events.ndjson`.
F-028-5: `resume` from `error` and `restart` after `stop` pass the owner limit AND the global limit, as `create`
does; a refused entry leaves the run where it was. Positive controls: once the other run stops, the same entry
succeeds. Via the runtime facade the API calls; the heartbeat clock is the p024 stand (no real time)."""

import pytest

from app.core.simulator.runtime import runtime
from app.utils.exceptions import ConflictException
from tests.p019_support import require_target
from tests.unit.test_p024_heartbeat_follows_every_entry_into_running import _started, clock  # noqa: F401


@pytest.fixture(autouse=True)
def _limits_of_one(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_max_active_runs", 1)
    monkeypatch.setattr(runtime, "_max_active_runs_per_owner", 1)


def _lines(run) -> int:
    path = run.artifacts_dir / "events.ndjson"
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


async def test_restart_writes_events_again(clock) -> None:  # noqa: F811
    run = await _started(clock)
    assert run.artifacts_dir is not None, "control: artifacts are enabled on this stand"
    writer = run._artifact_events_task
    runtime._artifacts.start_events_writer(run.run_id)  # idempotent: a second start keeps the one writer
    assert writer is not None and run._artifact_events_task is writer
    await runtime.stop(run.run_id)
    before = _lines(run)
    await runtime.restart(run.run_id)
    runtime._artifacts.enqueue_event_artifact(run.run_id, {"type": "tx.updated", "event_id": "evt_p028_e1"})
    await runtime.stop(run.run_id)  # drains the writer
    require_target(_lines(run) == before + 1, f"events.ndjson after restart: {before} -> {_lines(run)}")


def _kind(exc: ConflictException) -> str:
    return str((exc.details or {}).get("conflict_kind"))


async def test_resume_from_error_respects_the_owner_limit(clock) -> None:  # noqa: F811
    first = await _started(clock, "anon:p028-e1-a")
    first.state = "error"  # what the real runner's fail path does; it keeps the owner mapping
    second = await _started(clock, "anon:p028-e1-a")  # create clears the stale mapping
    try:
        status = await runtime.resume(first.run_id)
    except ConflictException as exc:
        assert _kind(exc) == "owner_active_exists" and first.state == "error", (_kind(exc), first.state)
    else:
        require_target(False, f"resume of an errored run beside the owner's live one gave {status.state!r}")
    await runtime.stop(second.run_id)
    assert (await runtime.resume(first.run_id)).state == "running"  # positive control: nothing else is live


async def test_restart_respects_the_global_limit(clock) -> None:  # noqa: F811
    first = await _started(clock, "anon:p028-e1-b")
    await runtime.stop(first.run_id)
    other = await _started(clock, "anon:p028-e1-c")  # another owner takes the only global slot
    try:
        status = await runtime.restart(first.run_id)
    except ConflictException as exc:
        assert _kind(exc) == "global_active_limit" and first.state == "stopped", (_kind(exc), first.state)
    else:
        require_target(False, f"restart beside another owner's live run at limit 1 gave {status.state!r}")
    await runtime.stop(other.run_id)
    assert (await runtime.restart(first.run_id)).state == "running"  # positive control
    await runtime.pause(first.run_id)
    assert (await runtime.resume(first.run_id)).state == "running"  # resume from paused is not an entry
