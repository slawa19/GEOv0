import logging
import threading

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast
from app.utils.exceptions import TooManyRequestsException


@pytest.mark.asyncio
async def test_sse_subscribe_rate_limit_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULATOR_SSE_MAX_CONNECTIONS", "100")
    monkeypatch.setenv("SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN", "1")

    lock = threading.RLock()
    runs: dict[str, RunRecord] = {}
    run = RunRecord(run_id="run_1", scenario_id="s", mode="fixtures", state="running")
    runs[run.run_id] = run

    sse = SseBroadcast(
        lock=lock,
        runs=runs,
        get_event_buffer_max=lambda: 10,
        get_event_buffer_ttl_sec=lambda: 0,
        get_sub_queue_max=lambda: 10,
        enqueue_event_artifact=lambda run_id, payload: None,
        logger=logging.getLogger("test"),
    )

    _ = await sse.subscribe("run_1", equivalent="UAH")
    with pytest.raises(TooManyRequestsException):
        _ = await sse.subscribe("run_1", equivalent="UAH")


@pytest.mark.asyncio
async def test_sse_subscribe_rate_limit_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULATOR_SSE_MAX_CONNECTIONS", "1")
    monkeypatch.setenv("SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN", "100")

    lock = threading.RLock()
    runs: dict[str, RunRecord] = {}
    run1 = RunRecord(run_id="run_1", scenario_id="s", mode="fixtures", state="running")
    run2 = RunRecord(run_id="run_2", scenario_id="s", mode="fixtures", state="running")
    runs[run1.run_id] = run1
    runs[run2.run_id] = run2

    sse = SseBroadcast(
        lock=lock,
        runs=runs,
        get_event_buffer_max=lambda: 10,
        get_event_buffer_ttl_sec=lambda: 0,
        get_sub_queue_max=lambda: 10,
        enqueue_event_artifact=lambda run_id, payload: None,
        logger=logging.getLogger("test"),
    )

    _ = await sse.subscribe("run_1", equivalent="UAH")
    with pytest.raises(TooManyRequestsException):
        _ = await sse.subscribe("run_2", equivalent="UAH")
