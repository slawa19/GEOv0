import asyncio
import threading

import pytest

from app.utils.event_bus import InMemoryEventBus
from app.utils.metrics import EVENT_BUS_DROPPED_TOTAL


def _drop_count(reason: str) -> float:
    return EVENT_BUS_DROPPED_TOTAL.labels(reason=reason)._value.get()


@pytest.mark.asyncio
async def test_publish_on_subscriber_loop_delivers() -> None:
    bus = InMemoryEventBus()
    sub = await bus.subscribe(pid="recipient", events=["payment.received"])

    bus.publish(
        recipient_pid="recipient",
        event="payment.received",
        payload={"tx_id": "tx-same-loop"},
    )

    message = await asyncio.wait_for(sub.queue.get(), timeout=1)
    assert message["payload"] == {"tx_id": "tx-same-loop"}


@pytest.mark.asyncio
async def test_publish_from_another_thread_delivers_without_blocking() -> None:
    bus = InMemoryEventBus()
    sub = await bus.subscribe(pid="recipient", events=["payment.received"])

    publisher = threading.Thread(
        target=bus.publish,
        kwargs={
            "recipient_pid": "recipient",
            "event": "payment.received",
            "payload": {"tx_id": "tx-cross-thread"},
        },
    )
    publisher.start()
    publisher.join(timeout=1)

    assert publisher.is_alive() is False
    message = await asyncio.wait_for(sub.queue.get(), timeout=1)
    assert message == {
        "event": "payment.received",
        "payload": {"tx_id": "tx-cross-thread"},
    }


@pytest.mark.asyncio
async def test_full_queue_drops_newest_inside_scheduled_callback() -> None:
    bus = InMemoryEventBus()
    sub = await bus.subscribe(pid="recipient", events=["payment.received"])
    for item in range(sub.queue.maxsize):
        sub.queue.put_nowait({"existing": item})
    drops_before = _drop_count("queue_full")

    bus.publish(
        recipient_pid="recipient",
        event="payment.received",
        payload={"tx_id": "dropped"},
    )
    await asyncio.sleep(0)

    assert sub.queue.qsize() == sub.queue.maxsize
    assert _drop_count("queue_full") == drops_before + 1
    queued = [sub.queue.get_nowait() for _ in range(sub.queue.maxsize)]
    assert queued == [{"existing": item} for item in range(sub.queue.maxsize)]


@pytest.mark.asyncio
async def test_unsubscribe_is_a_delivery_cutoff_for_scheduled_messages() -> None:
    bus = InMemoryEventBus()
    sub = await bus.subscribe(pid="recipient", events=["payment.received"])

    bus.publish(
        recipient_pid="recipient",
        event="payment.received",
        payload={"tx_id": "scheduled-before-unsubscribe"},
    )
    await bus.unsubscribe(sub)
    await asyncio.sleep(0)

    assert sub.queue.empty()


@pytest.mark.asyncio
async def test_closed_subscriber_loop_is_dropped_and_deactivated() -> None:
    bus = InMemoryEventBus()
    sub = await bus.subscribe(pid="recipient", events=["payment.received"])
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    sub.loop = closed_loop
    drops_before = _drop_count("loop_closed")

    bus.publish(
        recipient_pid="recipient",
        event="payment.received",
        payload={"tx_id": "undeliverable"},
    )

    assert _drop_count("loop_closed") == drops_before + 1
    assert sub.active is False
    assert sub not in bus._subs
