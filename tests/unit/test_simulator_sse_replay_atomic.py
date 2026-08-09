import asyncio
import logging
import threading

import pytest

import app.api.v1.simulator as simulator_api
from app.core.simulator.models import RunRecord, _Subscription
from app.core.simulator.sse_broadcast import SseBroadcast, SseReplayUnavailable


def _make_sse(*, queue_max: int = 8, buffer_max: int = 16):
    lock = threading.RLock()
    run = RunRecord(
        run_id="run_atomic", scenario_id="scenario", mode="fixtures", state="running"
    )
    runs = {run.run_id: run}
    sse = SseBroadcast(
        lock=lock,
        runs=runs,
        get_event_buffer_max=lambda: buffer_max,
        get_event_buffer_ttl_sec=lambda: 0,
        get_sub_queue_max=lambda: queue_max,
        enqueue_event_artifact=lambda _run_id, _payload: None,
        logger=logging.getLogger("test.sse.atomic_replay"),
    )
    return sse, run


def _event(sse: SseBroadcast, run: RunRecord, *, event_type: str = "tx.updated"):
    event = {
        "event_id": sse.next_event_id(run),
        "type": event_type,
        "equivalent": "EUR",
    }
    if event_type == "run_status":
        event.update({"run_id": run.run_id, "state": run.state})
    return event


@pytest.mark.asyncio
async def test_atomic_subscribe_queues_complete_replay_before_live_tail() -> None:
    sse, run = _make_sse()
    cursor = _event(sse, run)
    replay_1 = _event(sse, run)
    replay_2 = _event(sse, run, event_type="run_status")
    for event in (cursor, replay_1, replay_2):
        sse.broadcast(run.run_id, event)

    bootstrap: dict[str, object] = {}

    def build_status(event_id: str):
        event = {
            "event_id": event_id,
            "type": "run_status",
            "run_id": run.run_id,
            "state": run.state,
        }
        bootstrap.update(event)
        return event

    sub = await sse.subscribe(
        run.run_id,
        equivalent="EUR",
        after_event_id=str(cursor["event_id"]),
        bootstrap_event_factory=build_status,
    )
    live_1 = _event(sse, run)
    live_2 = _event(sse, run)
    sse.broadcast(run.run_id, live_1)
    sse.broadcast(run.run_id, live_2)

    assert [sub.queue.get_nowait()["event_id"] for _ in range(3)] == [
        replay_1["event_id"],
        replay_2["event_id"],
        bootstrap["event_id"],
    ]
    assert [event["event_id"] for event in sse.finish_replay_bootstrap(run_id=run.run_id, sub=sub)] == [
        live_1["event_id"],
        live_2["event_id"],
    ]

    post_bootstrap = _event(sse, run)
    sse.broadcast(run.run_id, post_bootstrap)
    assert sub.queue.get_nowait()["event_id"] == post_bootstrap["event_id"]


@pytest.mark.asyncio
async def test_subscribe_rejects_replay_that_cannot_fit_without_exposing_subscriber() -> (
    None
):
    sse, run = _make_sse(queue_max=2)
    cursor = _event(sse, run)
    replay_1 = _event(sse, run)
    replay_2 = _event(sse, run)
    for event in (cursor, replay_1, replay_2):
        sse.broadcast(run.run_id, event)

    with pytest.raises(SseReplayUnavailable, match="does not fit"):
        await sse.subscribe(
            run.run_id,
            equivalent="EUR",
            after_event_id=str(cursor["event_id"]),
            bootstrap_event_factory=lambda event_id: {
                "event_id": event_id,
                "type": "run_status",
                "run_id": run.run_id,
                "state": run.state,
            },
        )

    assert run._subs == []


@pytest.mark.asyncio
async def test_live_priority_event_follows_full_pending_replay_bootstrap() -> None:
    sse, run = _make_sse(queue_max=3)
    cursor = _event(sse, run)
    replay_1 = _event(sse, run)
    replay_2 = _event(sse, run)
    for event in (cursor, replay_1, replay_2):
        sse.broadcast(run.run_id, event)

    sub = await sse.subscribe(
        run.run_id,
        equivalent="EUR",
        after_event_id=str(cursor["event_id"]),
        bootstrap_event_factory=lambda event_id: {
            "event_id": event_id,
            "type": "run_status",
            "run_id": run.run_id,
            "state": run.state,
        },
    )
    priority_live = _event(sse, run)
    priority_live["amount_flyout"] = True
    sse.broadcast(run.run_id, priority_live)

    queued = [sub.queue.get_nowait() for _ in range(3)]
    assert [event["event_id"] for event in queued] == [
        replay_1["event_id"],
        replay_2["event_id"],
        f"evt_{run.run_id}_000004",
    ]
    assert [event["event_id"] for event in sub.replay_bootstrap_tail] == [
        priority_live["event_id"]
    ]


@pytest.mark.asyncio
async def test_stream_emits_single_bootstrap_tail_in_producer_order(monkeypatch) -> None:
    sse, run = _make_sse(queue_max=4)
    cursor = _event(sse, run)
    replay = _event(sse, run)
    for event in (cursor, replay):
        sse.broadcast(run.run_id, event)

    bootstrap: dict[str, object] = {}

    def build_status(event_id: str):
        event = {
            "event_id": event_id,
            "type": "run_status",
            "run_id": run.run_id,
            "state": run.state,
        }
        bootstrap.update(event)
        return event

    sub = await sse.subscribe(
        run.run_id,
        equivalent="EUR",
        after_event_id=str(cursor["event_id"]),
        bootstrap_event_factory=build_status,
    )
    live_tail = [_event(sse, run) for _ in range(3)]
    for event in live_tail:
        sse.broadcast(run.run_id, event)

    class FakeRuntime:
        unsubscribed = False

        def finish_replay_bootstrap(
            self, target_run_id: str, target_sub: _Subscription
        ) -> list[dict[str, object]]:
            return sse.finish_replay_bootstrap(
                run_id=target_run_id, sub=target_sub
            )

        async def unsubscribe(self, _run_id: str, _sub: _Subscription) -> None:
            self.unsubscribed = True

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(simulator_api, "runtime", fake_runtime)

    frames = [
        frame
        async for frame in simulator_api._run_events_stream(
            run_id=run.run_id,
            equivalent="EUR",
            last_event_id=str(cursor["event_id"]),
            stop_after_types={"tx.updated"},
            subscription=sub,
            initial_status_event_id=str(bootstrap["event_id"]),
        )
    ]

    assert [frame.splitlines()[0] for frame in frames] == [
        f"id: {replay['event_id']}",
        f"id: {bootstrap['event_id']}",
        *(f"id: {event['event_id']}" for event in live_tail),
    ]
    assert fake_runtime.unsubscribed is True


@pytest.mark.asyncio
async def test_subscribe_rejects_pruned_replay_but_accepts_cursor_before_oldest() -> (
    None
):
    sse, run = _make_sse(buffer_max=2)
    events = [_event(sse, run) for _ in range(4)]
    for event in events:
        sse.broadcast(run.run_id, event)

    with pytest.raises(SseReplayUnavailable, match="no longer retained"):
        await sse.subscribe(
            run.run_id, equivalent="EUR", after_event_id=str(events[0]["event_id"])
        )

    sub = await sse.subscribe(
        run.run_id, equivalent="EUR", after_event_id=str(events[1]["event_id"])
    )
    assert [sub.queue.get_nowait()["event_id"] for _ in range(2)] == [
        events[2]["event_id"],
        events[3]["event_id"],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor, message",
    [
        ("", "not a runtime event id"),
        ("not-an-event-id", "not a runtime event id"),
        ("evt_other_run_000001", "not a runtime event id"),
        (f"evt_run_atomic_{'9' * 5000}", "not a runtime event id"),
        ("evt_run_atomic_999999", "ahead of the run event sequence"),
    ],
)
async def test_subscribe_rejects_invalid_future_and_oversized_cursors(
    cursor: str, message: str
) -> None:
    sse, run = _make_sse()
    sse.publish_event(
        run_id=run.run_id,
        payload_factory=lambda event_id: {
            "event_id": event_id,
            "type": "tx.updated",
            "equivalent": "EUR",
        },
    )

    with pytest.raises(SseReplayUnavailable, match=message):
        await sse.subscribe(run.run_id, equivalent="EUR", after_event_id=cursor)
    assert run._subs == []


@pytest.mark.asyncio
async def test_atomic_publish_serializes_id_buffer_and_subscriber_order_across_producers() -> None:
    sse, run = _make_sse()
    sub = await sse.subscribe(run.run_id, equivalent="EUR")
    entered = threading.Event()
    release = threading.Event()
    published: list[str] = []

    def slow_factory(event_id: str) -> dict[str, object]:
        entered.set()
        assert release.wait(timeout=2)
        return {"event_id": event_id, "type": "tx.updated", "equivalent": "EUR"}

    def publish_slow() -> None:
        payload = sse.publish_event(run_id=run.run_id, payload_factory=slow_factory)
        assert payload is not None
        published.append(str(payload["event_id"]))

    def publish_fast() -> None:
        payload = sse.publish_event(
            run_id=run.run_id,
            payload_factory=lambda event_id: {
                "event_id": event_id,
                "type": "run_status",
                "run_id": run.run_id,
                "state": run.state,
            },
        )
        assert payload is not None
        published.append(str(payload["event_id"]))

    slow = threading.Thread(target=publish_slow)
    fast = threading.Thread(target=publish_fast)
    slow.start()
    assert entered.wait(timeout=2)
    fast.start()
    release.set()
    slow.join(timeout=2)
    fast.join(timeout=2)

    assert not slow.is_alive()
    assert not fast.is_alive()
    assert published == ["evt_run_atomic_000001", "evt_run_atomic_000002"]
    assert [item[1] for item in run._event_buffer] == published
    assert [sub.queue.get_nowait()["event_id"] for _ in range(2)] == published


@pytest.mark.asyncio
async def test_bootstrap_tail_overflow_closes_subscription_and_replay_recovers_gap() -> None:
    sse, run = _make_sse(queue_max=2, buffer_max=16)
    sub = await sse.subscribe(
        run.run_id,
        equivalent="EUR",
        bootstrap_event_factory=lambda event_id: {
            "event_id": event_id,
            "type": "run_status",
            "run_id": run.run_id,
            "state": run.state,
        },
    )
    cursor = str(sub.queue.get_nowait()["event_id"])

    for _ in range(3):
        sse.publish_event(
            run_id=run.run_id,
            payload_factory=lambda event_id: {
                "event_id": event_id,
                "type": "tx.updated",
                "equivalent": "EUR",
            },
        )

    assert sub.closed is True
    assert sub.close_reason == "replay_bootstrap_overflow"
    assert sub not in run._subs
    assert sub.queue.get_nowait()["type"] == "__subscription_closed__"

    sse._get_sub_queue_max = lambda: 8  # type: ignore[method-assign]
    recovered = await sse.subscribe(
        run.run_id,
        equivalent="EUR",
        after_event_id=cursor,
        bootstrap_event_factory=lambda event_id: {
            "event_id": event_id,
            "type": "run_status",
            "run_id": run.run_id,
            "state": run.state,
        },
    )
    assert [recovered.queue.get_nowait()["event_id"] for _ in range(4)] == [
        "evt_run_atomic_000002",
        "evt_run_atomic_000003",
        "evt_run_atomic_000004",
        "evt_run_atomic_000005",
    ]


@pytest.mark.asyncio
async def test_terminal_stream_flushes_replay_before_status_and_closes(
    monkeypatch,
) -> None:
    replay_status = {
        "event_id": "evt_run_atomic_000002",
        "type": "run_status",
        "run_id": "run_atomic",
        "state": "running",
    }
    replay_tx = {
        "event_id": "evt_run_atomic_000003",
        "type": "tx.updated",
        "equivalent": "EUR",
    }
    terminal_status = {
        "event_id": "evt_run_atomic_000004",
        "type": "run_status",
        "run_id": "run_atomic",
        "state": "stopped",
    }
    sub = _Subscription(equivalent="EUR", queue=asyncio.Queue(maxsize=8))
    sub.queue.put_nowait(replay_status)
    sub.queue.put_nowait(replay_tx)

    class FakeRuntime:
        unsubscribed = False

        def publish_run_status(self, _run_id: str) -> str:
            sub.queue.put_nowait(terminal_status)
            return str(terminal_status["event_id"])

        async def unsubscribe(self, _run_id: str, _sub: _Subscription) -> None:
            self.unsubscribed = True

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(simulator_api, "runtime", fake_runtime)

    frames = [
        frame
        async for frame in simulator_api._run_events_stream(
            run_id="run_atomic",
            equivalent="EUR",
            last_event_id="evt_run_atomic_000001",
            subscription=sub,
        )
    ]

    assert [frame.splitlines()[0] for frame in frames] == [
        "id: evt_run_atomic_000002",
        "id: evt_run_atomic_000003",
        "id: evt_run_atomic_000004",
    ]
    assert fake_runtime.unsubscribed is True
