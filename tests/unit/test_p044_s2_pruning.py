"""044 stage 2 (stage 0 finding (n), stage 1 item (e)): ended run records are pruned by one rule, also when a run ends.

TARGET: `SIMULATOR_MAX_RUN_RECORDS` drops the oldest ENDED records - `stopped` and `error` (until stage 2 an `error`
record was never dropped) - and the pass runs when a run ends (`runtime.stop`), not only on create. An `error` record
keeps its events writer (`fail_run` does not stop it): the writer is ended first, then the record dropped. The owner
mapping of a dropped record is cleared with it (`fail_run` leaves an `error` run mapped; admin stop-all and the 409 of
`restart` read the mapping), and the mapping of a record that stays is untouched.

The production runtime and lifecycle; records are registered by hand (no tick, no database: storage writes and the
final flush are stubbed). WHAT IT DOES NOT SEE: `fail_run` itself (the tick's, outside 044's surface) - the `error`
records here are in the state it leaves; the artifacts on disk (never deleted, the 2026-10-10 exception).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.core.simulator.storage as simulator_storage
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime import runtime
from app.utils.exceptions import ConflictException

T0 = datetime(2026, 10, 18, tzinfo=timezone.utc)


@pytest.fixture
def registry(monkeypatch):
    async def no_io(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(simulator_storage, "upsert_run", no_io)
    monkeypatch.setattr(simulator_storage, "sync_artifacts", no_io)
    monkeypatch.setattr(runtime._real_runner, "flush_pending_storage", no_io)
    monkeypatch.setattr(runtime, "_max_run_records", 3)
    with runtime._lock:
        saved_runs, saved_map = dict(runtime._runs), dict(runtime._active_run_id_by_owner)
        runtime._runs.clear()
        runtime._active_run_id_by_owner.clear()
    yield runtime
    with runtime._lock:
        runtime._runs.clear()
        runtime._runs.update(saved_runs)
        runtime._active_run_id_by_owner.clear()
        runtime._active_run_id_by_owner.update(saved_map)


def _add(run_id: str, state: str, *, minute: int, owner: str = "", mapped: bool = False) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="x", mode="real", state=state, owner_id=owner)
    run.started_at = T0 + timedelta(minutes=minute)
    if state in ("stopped", "error"):
        run.stopped_at = run.started_at + timedelta(seconds=30)
    with runtime._lock:
        runtime._runs[run_id] = run
        if mapped:
            runtime._active_run_id_by_owner[owner] = run_id
    return run


def _with_writer(run: RunRecord) -> asyncio.Queue:
    """The events writer an `error` run keeps: a task that ends on the `None` sentinel `stop_events_writer` sends."""

    q: asyncio.Queue = asyncio.Queue()

    async def writer() -> None:
        while await q.get() is not None:
            pass

    run._artifact_events_queue = q
    run._artifact_events_task = asyncio.create_task(writer())
    return q


@pytest.mark.asyncio
async def test_error_records_are_pruned_when_a_run_ends(registry) -> None:
    _add("e1", "error", minute=1, owner="anon:a", mapped=True)
    _add("e2", "error", minute=2, owner="anon:b", mapped=True)
    _add("s3", "stopped", minute=3)
    _add("r4", "running", minute=4, owner="anon:d", mapped=True)
    assert len(runtime._runs) == 4  # one over the limit of 3

    await runtime.stop("r4")

    assert sorted(runtime._runs) == ["e2", "r4", "s3"], "the oldest ended record (an `error` one) is dropped"
    assert "anon:a" not in runtime.get_all_active_runs(), "the dropped record's mapping went with it"
    assert runtime.get_all_active_runs().get("anon:b") == "e2", "a kept `error` record stays mapped (admin stop-all)"


@pytest.mark.asyncio
async def test_an_error_record_with_a_running_writer_is_dropped_after_its_writer_ends(registry) -> None:
    e1 = _add("e1", "error", minute=1)
    _with_writer(e1)
    writer = e1._artifact_events_task
    _add("s2", "stopped", minute=2)
    _add("s3", "stopped", minute=3)
    _add("r4", "running", minute=4)

    await runtime.stop("r4")

    assert writer.done(), "the writer was ended, not abandoned"
    assert "e1" not in runtime._runs
    assert len(runtime._runs) == 3


@pytest.mark.asyncio
async def test_counter_check_live_runs_are_never_pruned_and_nothing_under_the_limit(registry) -> None:
    _add("p1", "paused", minute=1)
    _add("r2", "running", minute=2)
    _add("e3", "error", minute=3)
    _add("r5", "running", minute=5)
    await runtime.stop("r5")  # 4 records, limit 3: one ended goes - the OLDEST ENDED, never a live one
    assert sorted(runtime._runs) == ["p1", "r2", "r5"]
    await runtime.stop("r2")  # 3 records: at the limit, nothing goes
    assert sorted(runtime._runs) == ["p1", "r2", "r5"]


@pytest.mark.asyncio
async def test_restart_409_does_not_cite_a_pruned_error_run(registry) -> None:
    """The 409 of `restart` reads the owner mapping: an `error` run left mapped by `fail_run` refuses the owner's other
    run - while it exists; once pruned, its mapping is gone and no longer answers for it."""

    _add("e1", "error", minute=1, owner="anon:o", mapped=True)
    _add("s2", "stopped", minute=2, owner="anon:o")
    with pytest.raises(ConflictException) as refused:
        await runtime.restart("s2")
    assert refused.value.details["active_run_id"] == "e1"

    _add("s3", "stopped", minute=3)
    _add("r4", "running", minute=4)
    await runtime.stop("r4")
    assert "e1" not in runtime._runs and "anon:o" not in runtime.get_all_active_runs()
    await runtime.restart("s2")
    assert runtime.get_run("s2").state == "running"
    await runtime.stop("s2")
