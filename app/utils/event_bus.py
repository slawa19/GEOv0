from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from app.utils.metrics import EVENT_BUS_DROPPED_TOTAL


@dataclass
class _Subscription:
    pid: str
    events: set[str]
    queue: "asyncio.Queue[dict[str, Any]]"
    loop: asyncio.AbstractEventLoop
    active: bool = True


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
            sub.active = False
            try:
                self._subs.remove(sub)
            except ValueError:
                return

    @staticmethod
    def _record_drop(reason: str) -> None:
        try:
            EVENT_BUS_DROPPED_TOTAL.labels(reason=reason).inc()
        except Exception:
            # Observability must not make a best-effort publication fail.
            pass

    def _deliver_if_active(
        self,
        sub: _Subscription,
        message: dict[str, Any],
    ) -> None:
        dropped = False
        with self._lock:
            if not sub.active:
                return
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                # Deterministic overload policy: preserve queued messages and drop
                # the newest publication without blocking the payment path.
                dropped = True
        if dropped:
            self._record_drop("queue_full")

    def _deactivate(self, sub: _Subscription) -> None:
        with self._lock:
            sub.active = False
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    def publish(self, *, recipient_pid: str, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            subs = [s for s in self._subs if s.pid == recipient_pid and event in s.events]

        message = {"event": event, "payload": payload}
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(self._deliver_if_active, sub, message)
            except RuntimeError:
                # A closed subscriber loop cannot consume future messages. Remove
                # it so subsequent publications do not repeat the same failure.
                self._deactivate(sub)
                self._record_drop("loop_closed")


event_bus = InMemoryEventBus()
