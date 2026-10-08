"""034 S1b, F-034-8 (the counting half): an event the run's artifact writer could not record is counted.

WHAT WAS WRONG (on `0f248b9c`). An event that did not fit the queue of the `events.ndjson` writer, a batch the
writer could not append, an event that could not be serialised - each was dropped without a trace, so "no events
were lost" and "nobody counted" looked the same (AGENTS.md §1, §12).

WHAT IS COUNTED NOW, and what is not - both are held here, because a counter that is silent about a case must not
be read as saying the case did not happen. Counted: `queue_full`, `write_failed`, `encode_failed`. NOT counted: an
event that arrives while the run has no writer (it was never going to be recorded), and events left in the queue of
a writer cancelled at stop (not exercised here: `stop_events_writer`, a 2 s wall-clock timeout).

THE STAND is the real `ArtifactsManager` over a temporary state directory - no database, no runtime singleton.
The retention of run directories (TTL, limit) is not this module's subject: it is slice S1c.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.models import RunRecord

_LOG = logging.getLogger("tests.p034.s1b.artifacts")
_DROPPED = "geo_simulator_artifact_events_dropped_total"
_REASONS = ("queue_full", "write_failed", "encode_failed")


def _stand(state_dir: Path, run_id: str) -> tuple[ArtifactsManager, RunRecord, Path]:
    """A manager, one running run registered in it, and the run's artifacts directory."""

    runs: dict[str, RunRecord] = {}
    manager = ArtifactsManager(
        lock=threading.RLock(), runs=runs, local_state_dir=lambda: state_dir,
        utc_now=lambda: datetime.now(timezone.utc), db_enabled=lambda: False, logger=_LOG,
    )
    artifacts = state_dir / "runs" / run_id / "artifacts"
    artifacts.mkdir(parents=True)
    run = RunRecord(run_id=run_id, scenario_id="s", mode="fixtures", state="running")
    run.artifacts_dir = artifacts
    runs[run_id] = run
    return manager, run, artifacts


def _counted() -> dict[str, float]:
    return {reason: REGISTRY.get_sample_value(_DROPPED, {"reason": reason}) or 0.0 for reason in _REASONS}


def _since(before: dict[str, float]) -> dict[str, float]:
    return {reason: value - before[reason] for reason, value in _counted().items() if value != before[reason]}


@pytest.mark.asyncio
async def test_an_event_the_artifact_writer_could_not_take_is_counted(tmp_path, caplog) -> None:
    """The writer's queue is full (its consumer is not running here): three more events are lost. Each is counted
    where an operator can read it - the `/metrics` counter - and the loss is logged; before, nothing said so."""

    manager, run, _artifacts = _stand(tmp_path, "r-drops")
    run._artifact_events_queue = asyncio.Queue(maxsize=2)
    before = _counted()

    with caplog.at_level(logging.WARNING, logger=_LOG.name):
        for seq in range(5):
            manager.enqueue_event_artifact(run.run_id, {"type": "tx.updated", "seq": seq})

    # Control: two were taken, so three were lost.
    assert run._artifact_events_queue.qsize() == 2
    counted = _since(before).get("queue_full", 0.0)
    logged = [r.getMessage() for r in caplog.records if r.name == _LOG.name and r.levelno >= logging.WARNING]
    assert counted == 3 and logged, (
        f"3 events did not fit the artifact writer's queue; counted by {_DROPPED}{{reason=\"queue_full\"}}: "
        f"{counted:g}; warnings logged: {logged}. Expected 3 counted and the loss logged"
    )
    assert _since(before) == {"queue_full": 3} and run._artifact_events_dropped == 3, (_since(before), run._artifact_events_dropped)
    assert len(logged) == 1, logged  # once for the run's first drop, not once per event


@pytest.mark.asyncio
async def test_a_batch_the_writer_could_not_append_is_counted(tmp_path) -> None:
    """The append itself fails (here `events.ndjson` is a directory). The real writer loop takes the two queued
    events as one batch, fails to append it, and counts both."""

    manager, run, artifacts = _stand(tmp_path, "r-io")
    (artifacts / "events.ndjson").mkdir()
    before = _counted()

    queue: asyncio.Queue = asyncio.Queue()
    for item in ('{"seq":0}\n', '{"seq":1}\n', None):
        queue.put_nowait(item)
    await manager._events_writer_loop(run_id=run.run_id, path=artifacts / "events.ndjson", queue=queue)

    assert (_since(before), run._artifact_events_dropped) == ({"write_failed": 2}, 2)


@pytest.mark.asyncio
async def test_an_event_that_cannot_be_serialised_is_counted_and_the_next_one_is_recorded(tmp_path) -> None:
    manager, run, _artifacts = _stand(tmp_path, "r-encode")
    run._artifact_events_queue = asyncio.Queue(maxsize=10)
    before = _counted()

    manager.enqueue_event_artifact(run.run_id, {"type": "note", "payload": object()})  # not JSON-serialisable
    manager.enqueue_event_artifact(run.run_id, {"type": "note", "payload": "ok"})

    assert (_since(before), run._artifact_events_dropped) == ({"encode_failed": 1}, 1)
    assert run._artifact_events_queue.qsize() == 1  # control: the serialisable event was taken


@pytest.mark.asyncio
async def test_an_event_for_a_run_without_a_writer_is_not_counted(tmp_path) -> None:
    """THE LIMIT OF THE COUNTER, held so that nobody reads it as more than it is: a run whose writer is not running
    (not started yet, or stopped with the run) records nothing and counts nothing. `events.ndjson` of such a run
    is not complete, and the counter does not say so."""

    manager, run, _artifacts = _stand(tmp_path, "r-no-writer")
    assert run._artifact_events_queue is None  # control: no writer
    before = _counted()

    manager.enqueue_event_artifact(run.run_id, {"type": "tx.updated", "seq": 0})

    assert (_since(before), run._artifact_events_dropped) == ({}, 0)
