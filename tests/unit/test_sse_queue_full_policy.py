import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast


@pytest.mark.asyncio
async def test_sse_queue_overflow_closes_stream_before_any_higher_id_is_delivered(
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    # The gap invalidates this live stream: pending data is discarded and a
    # close marker wakes the consumer. Later events cannot reach this queue.
    assert sub.queue.qsize() == 1
    got = sub.queue.get_nowait()
    assert got.get("type") == "__subscription_closed__"
    assert sub.closed is True
    assert sub not in run._subs

    evt3 = {
        "event_id": sse.next_event_id(run),
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "UAH",
    }
    sse.broadcast("run_1", evt3)
    assert sub.queue.empty()

    sse._get_sub_queue_max = lambda: 8  # type: ignore[method-assign]
    recovered = await sse.subscribe(
        "run_1", equivalent="UAH", after_event_id="evt_run_1_000000"
    )
    assert [recovered.queue.get_nowait()["event_id"] for _ in range(3)] == [
        evt1["event_id"],
        evt2["event_id"],
        evt3["event_id"],
    ]

    assert any(
        rec.name == logger.name and "simulator.sse.queue_overflow_close" in str(rec.message)
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_sse_queue_full_run_status_terminates_instead_of_skipping_lower_event() -> None:
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

    # Even a high-priority status cannot jump over a dropped lower event.
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
    assert got.get("type") == "__subscription_closed__"
    assert sub.closed is True


@pytest.mark.asyncio
async def test_sse_queue_full_amount_flyout_terminates_instead_of_skipping_lower_event() -> None:
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

    # Priority cannot violate the replay cursor: reconnect must recover both.
    sse.broadcast("run_1", hi)

    assert sub.queue.qsize() == 1
    got = sub.queue.get_nowait()
    assert got.get("type") == "__subscription_closed__"
    assert sub.closed is True
