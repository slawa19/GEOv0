from __future__ import annotations

import asyncio
import time
import threading
from typing import Any, Callable, Optional

from app.core.simulator.models import RunRecord, _Subscription


class SseBroadcast:
    def __init__(
        self,
        *,
        lock: threading.RLock,
        runs: dict[str, RunRecord],
        get_event_buffer_max: Callable[[], int],
        get_event_buffer_ttl_sec: Callable[[], int],
        enqueue_event_artifact: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._get_event_buffer_max = get_event_buffer_max
        self._get_event_buffer_ttl_sec = get_event_buffer_ttl_sec
        self._enqueue_event_artifact = enqueue_event_artifact

    def next_event_id(self, run: RunRecord) -> str:
        with self._lock:
            run._event_seq += 1
            return f"evt_{run.run_id}_{run._event_seq:06d}"

    def event_seq_from_event_id(self, *, run_id: str, event_id: str) -> Optional[int]:
        # Expected: evt_<run_id>_<seq>
        prefix = f"evt_{run_id}_"
        if not event_id.startswith(prefix):
            return None
        tail = event_id[len(prefix) :]
        if not tail.isdigit():
            return None
        try:
            return int(tail)
        except Exception:
            return None

    def prune_event_buffer_locked(self, run: RunRecord, *, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()

        ttl = max(0, int(self._get_event_buffer_ttl_sec()))
        if ttl:
            cutoff = now - ttl
            while run._event_buffer and run._event_buffer[0][0] < cutoff:
                run._event_buffer.popleft()

        max_len = max(1, int(self._get_event_buffer_max()))
        while len(run._event_buffer) > max_len:
            run._event_buffer.popleft()

    def append_to_event_buffer(self, *, run_id: str, payload: dict[str, Any]) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return

        event_id = str(payload.get("event_id") or "")
        if not event_id:
            return

        seq = self.event_seq_from_event_id(run_id=run_id, event_id=event_id)
        # Only buffer standard monotonically-increasing runtime event ids.
        if seq is None:
            return

        now = time.time()
        event_type = str(payload.get("type") or "")
        event_equivalent = str(payload.get("equivalent") or "")

        with self._lock:
            run._event_buffer.append((now, event_id, event_equivalent if event_type != "run_status" else "", payload))
            self.prune_event_buffer_locked(run, now=now)

    def replay_events(self, *, run_id: str, equivalent: str, after_event_id: str) -> list[dict[str, Any]]:
        run = self._runs.get(run_id)
        if run is None:
            return []

        after_seq = self.event_seq_from_event_id(run_id=run_id, event_id=after_event_id)
        if after_seq is None:
            return []

        with self._lock:
            self.prune_event_buffer_locked(run)
            buf = list(run._event_buffer)

        out: list[dict[str, Any]] = []
        for (_ts, event_id, event_equivalent, payload) in buf:
            seq = self.event_seq_from_event_id(run_id=run_id, event_id=event_id)
            if seq is None or seq <= after_seq:
                continue
            event_type = str(payload.get("type") or "")
            if event_type != "run_status" and event_equivalent != equivalent:
                continue
            out.append(payload)
        return out

    def broadcast(self, run_id: str, payload: dict[str, Any]) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return

        event_type = str(payload.get("type") or "")
        event_equivalent = str(payload.get("equivalent") or "")

        # Record for best-effort replay.
        self.append_to_event_buffer(run_id=run_id, payload=payload)

        # Best-effort raw events export.
        try:
            self._enqueue_event_artifact(run_id, payload)
        except Exception:
            pass

        with self._lock:
            subs = list(run._subs)

        for sub in subs:
            if event_type != "run_status" and sub.equivalent != event_equivalent:
                continue
            try:
                sub.queue.put_nowait(payload)
            except asyncio.QueueFull:
                if event_type == "run_status":
                    # run_status must not be skipped; drop one queued item to make room.
                    try:
                        _ = sub.queue.get_nowait()
                        sub.queue.put_nowait(payload)
                        continue
                    except Exception:
                        pass
                continue

    async def subscribe(self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None) -> _Subscription:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        sub = _Subscription(equivalent=equivalent, queue=queue)

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return sub
            run._subs.append(sub)

        if after_event_id:
            for evt in self.replay_events(run_id=run_id, equivalent=equivalent, after_event_id=after_event_id):
                try:
                    sub.queue.put_nowait(evt)
                except asyncio.QueueFull:
                    break

        return sub

    async def unsubscribe(self, run_id: str, sub: _Subscription) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            try:
                run._subs.remove(sub)
            except ValueError:
                return

    def is_replay_too_old(self, *, run_id: str, after_event_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False

        after_seq = self.event_seq_from_event_id(run_id=run_id, event_id=after_event_id)
        if after_seq is None:
            return False

        with self._lock:
            self.prune_event_buffer_locked(run)
            if not run._event_buffer:
                return False
            oldest_event_id = run._event_buffer[0][1]

        oldest_seq = self.event_seq_from_event_id(run_id=run_id, event_id=oldest_event_id)
        if oldest_seq is None:
            return False
        return after_seq < oldest_seq
