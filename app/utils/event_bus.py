from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class _Subscription:
    pid: str
    events: set[str]
    queue: "asyncio.Queue[dict[str, Any]]"
    loop: asyncio.AbstractEventLoop


class InMemoryEventBus:
    """Best-effort in-process event bus.

    This is intentionally minimal for MVP/testing. It provides at-most-once delivery
    to currently connected subscribers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[_Subscription] = []

    async def subscribe(self, *, pid: str, events: list[str]) -> _Subscription:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        sub = _Subscription(pid=pid, events=set(events), queue=queue, loop=loop)
        with self._lock:
            self._subs.append(sub)
        return sub

    async def unsubscribe(self, sub: _Subscription) -> None:
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                return

    def publish(self, *, recipient_pid: str, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            subs = [s for s in self._subs if s.pid == recipient_pid and event in s.events]

        message = {"event": event, "payload": payload}
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, message)
            except asyncio.QueueFull:
                # Drop on overload (best-effort)
                pass


event_bus = InMemoryEventBus()
