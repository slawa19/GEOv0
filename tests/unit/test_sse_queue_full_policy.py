import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast


@pytest.mark.asyncio
async def test_sse_queue_full_drops_non_run_status(caplog: pytest.LogCaptureFixture) -> None:
    lock = threading.RLock()
    runs: dict[str, RunRecord] = {}
    run = RunRecord(run_id="run_1", scenario_id="s", mode="fixtures", state="running")
    runs[run.run_id] = run

    logger = logging.getLogger("test.sse.queue_full")

    sse = SseBroadcast(
        lock=lock,
        runs=runs,
        get_event_buffer_max=lambda: 10,
        get_event_buffer_ttl_sec=lambda: 0,
        get_sub_queue_max=lambda: 1,
        enqueue_event_artifact=lambda run_id, payload: None,
        logger=logger,
    )

    sub = await sse.subscribe("run_1", equivalent="UAH")

    evt1 = {
        "event_id": sse.next_event_id(run),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "UAH",
    }
    evt2 = {
        "event_id": sse.next_event_id(run),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "UAH",
    }

    sse.broadcast("run_1", evt1)
    assert sub.queue.qsize() == 1

    with caplog.at_level(logging.WARNING):
        sse.broadcast("run_1", evt2)

    # Still full with the first event; second tx.updated is dropped.
    assert sub.queue.qsize() == 1
    got = sub.queue.get_nowait()
    assert got.get("event_id") == evt1["event_id"]

    assert any(
        rec.name == logger.name and "simulator.sse.queue_full_drop" in str(rec.message)
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_sse_queue_full_keeps_run_status_by_eviction() -> None:
    lock = threading.RLock()
    runs: dict[str, RunRecord] = {}
    run = RunRecord(run_id="run_1", scenario_id="s", mode="fixtures", state="running")
    runs[run.run_id] = run

    sse = SseBroadcast(
        lock=lock,
        runs=runs,
        get_event_buffer_max=lambda: 10,
        get_event_buffer_ttl_sec=lambda: 0,
        get_sub_queue_max=lambda: 1,
        enqueue_event_artifact=lambda run_id, payload: None,
        logger=logging.getLogger("test.sse.run_status"),
    )

    sub = await sse.subscribe("run_1", equivalent="UAH")

    # Fill the queue with a tx.updated.
    evt1 = {
        "event_id": sse.next_event_id(run),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "UAH",
    }
    sse.broadcast("run_1", evt1)
    assert sub.queue.qsize() == 1

    # run_status must not be skipped: it should evict one queued item.
    rs = {
        "event_id": sse.next_event_id(run),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "run_status",
        "run_id": "run_1",
        "scenario_id": "s",
        "state": "running",
        "sim_time_ms": 0,
        "intensity_percent": 0,
        "ops_sec": 0,
        "queue_depth": 0,
    }
    sse.broadcast("run_1", rs)

    assert sub.queue.qsize() == 1
    got = sub.queue.get_nowait()
    assert got.get("type") == "run_status"
    assert got.get("event_id") == rs["event_id"]


@pytest.mark.asyncio
async def test_sse_queue_full_prioritizes_amount_flyout_tx_updated() -> None:
    lock = threading.RLock()
    runs: dict[str, RunRecord] = {}
    run = RunRecord(run_id="run_1", scenario_id="s", mode="fixtures", state="running")
    runs[run.run_id] = run

    sse = SseBroadcast(
        lock=lock,
        runs=runs,
        get_event_buffer_max=lambda: 10,
        get_event_buffer_ttl_sec=lambda: 0,
        get_sub_queue_max=lambda: 1,
        enqueue_event_artifact=lambda run_id, payload: None,
        logger=logging.getLogger("test.sse.amount_flyout_priority"),
    )

    sub = await sse.subscribe("run_1", equivalent="UAH")

    low = {
        "event_id": sse.next_event_id(run),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "UAH",
        "amount_flyout": False,
    }
    hi = {
        "event_id": sse.next_event_id(run),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "UAH",
        "amount_flyout": True,
        "amount": "1.00",
        "from": "A",
        "to": "B",
    }

    sse.broadcast("run_1", low)
    assert sub.queue.qsize() == 1

    # Queue is full: amount_flyout=true should evict and be enqueued.
    sse.broadcast("run_1", hi)

    assert sub.queue.qsize() == 1
    got = sub.queue.get_nowait()
    assert got.get("event_id") == hi["event_id"]
    assert got.get("amount_flyout") is True
