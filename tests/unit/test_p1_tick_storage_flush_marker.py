"""RT-009-8: a swallowed storage failure must not mark the tick as flushed.

Program 009, finding `F-009-2` (`B-A2b-001`, P1) -- the *second* half of it.

`T902` gave `write_tick_bottlenecks` a SAVEPOINT so a failed write stops destroying the
caller's staged work.  That closed the collateral damage.  It did not close what the
finding is actually named for: **the owner still reports success for work that was never
written.**  The writer swallows its own exception and returns `None`
(`app/core/simulator/storage.py:666-671`), which is indistinguishable from a successful
write, and `persist_tick_tail` then advances the flush marker regardless
(`app/core/simulator/real_tick_persistence.py:134-136`).

Why the loss is permanent rather than transient: `flush_pending_storage` is the retry
path, and it returns early when `flushed_tick >= last_tick`
(`app/core/simulator/real_tick_persistence.py:202-204`).  A tick marked flushed but never
written is therefore never retried -- the bottlenecks row is gone for good, and the only
trace is one `logger.exception` line.

The failure injected here is a real storage failure: the session's `flush()` raises inside
the savepoint, exactly as it would when a value exceeds the column's `Numeric` precision --
the failure class `T715` documented for the neighbouring metrics writer.  Nothing about the
error is fabricated at the boundary being tested.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import pytest

import app.core.simulator.storage as simulator_storage
from app.core.simulator.models import RunRecord
from app.core.simulator.real_tick_persistence import RealTickPersistence


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _Savepoint:
    def __init__(self, session: "_FailingFlushSession") -> None:
        self._session = session

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        self._session.savepoint_rollbacks += 1
        return None


class _FailingFlushSession:
    """A session whose `flush()` fails, as a real one does on a constraint or overflow."""

    def __init__(self) -> None:
        self.added: list = []
        self.savepoint_rollbacks = 0
        self.commits = 0
        self.rollbacks = 0

    def add_all(self, items) -> None:
        self.added.extend(items)

    async def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)

    async def flush(self) -> None:
        raise RuntimeError("numeric field overflow")

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _persistence(
    *, metrics_every_n: int = 1, bottlenecks_every_n: int = 1
) -> RealTickPersistence:
    return RealTickPersistence(
        lock=threading.RLock(),
        artifacts=None,
        utc_now=_utc_now,
        db_enabled=lambda: True,
        logger=logging.getLogger(__name__),
        real_db_metrics_every_n_ticks=metrics_every_n,
        real_db_bottlenecks_every_n_ticks=bottlenecks_every_n,
        # Keep artifact writing out of this test entirely.
        real_last_tick_write_every_ms=0,
        real_artifacts_sync_every_ms=0,
    )


def _run() -> RunRecord:
    run = RunRecord(run_id="r1", scenario_id="s1", mode="real", state="running")
    run.tick_index = 7
    run.sim_time_ms = 7000
    run._real_last_tick_storage_flushed_tick = -1
    return run


@pytest.mark.asyncio
async def test_swallowed_bottlenecks_failure_does_not_mark_the_tick_flushed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(simulator_storage, "db_enabled", lambda: True)

    session = _FailingFlushSession()
    run = _run()
    committed_callbacks: list[str] = []

    # tick 7 is not a multiple of 5, so the metrics writer is skipped and only the
    # bottlenecks writer can fail -- otherwise both roll back and the assertion below
    # would not tell us which one did.
    await _persistence(metrics_every_n=5).persist_tick_tail(
        session=session,
        run=run,
        equivalents=["HOUR"],
        tick_t0=0.0,
        planned_len=1,
        committed=1,
        rejected=0,
        errors=1,
        timeouts=0,
        per_eq={"HOUR": {"committed": 1, "rejected": 0, "errors": 1, "timeouts": 0}},
        per_eq_metric_values={"HOUR": {}},
        # attempts > 0 and errors > 0 -> score > 0, so the writer really builds a row.
        per_eq_edge_stats={"HOUR": {("a", "b"): {"attempts": 2, "errors": 1}}},
        on_commit=lambda: committed_callbacks.append("commit"),
    )

    # The savepoint did its job: the write was undone rather than the whole session.
    assert session.savepoint_rollbacks == 1, (
        "the savepoint must roll back the failed write; if it did not, this test is "
        "measuring the wrong failure"
    )

    assert int(run._real_last_tick_storage_flushed_tick) != 7, (
        "the bottlenecks row was never written, but the tick is marked flushed, so "
        "`flush_pending_storage` will skip it and the data is lost permanently"
    )


@pytest.mark.asyncio
async def test_swallowed_metrics_failure_does_not_mark_the_tick_flushed(
    monkeypatch,
) -> None:
    """The sibling writer swallows the same way; the marker must not lie for it either."""

    monkeypatch.setattr(simulator_storage, "db_enabled", lambda: True)

    session = _FailingFlushSession()
    run = _run()

    await _persistence(bottlenecks_every_n=5).persist_tick_tail(
        session=session,
        run=run,
        equivalents=["HOUR"],
        tick_t0=0.0,
        planned_len=1,
        committed=1,
        rejected=0,
        errors=0,
        timeouts=0,
        per_eq={"HOUR": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}},
        per_eq_metric_values={"HOUR": {"avg_route_length": 1.0}},
        # No edge stats -> the bottlenecks writer returns early and only metrics can fail.
        per_eq_edge_stats={"HOUR": {}},
    )

    assert int(run._real_last_tick_storage_flushed_tick) != 7, (
        "no metric point was written, but the tick is marked flushed"
    )
