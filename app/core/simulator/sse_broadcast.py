from __future__ import annotations

import asyncio
import logging
import time
import threading
from typing import Any, Callable, Optional

from app.core.simulator.models import RunRecord, _Subscription
from app.core.simulator.runtime_utils import safe_int_env as _safe_int_env
from app.utils.exceptions import TooManyRequestsException


class SseBroadcast:
    def __init__(
        self,
        *,
        lock: threading.RLock,
        runs: dict[str, RunRecord],
        get_event_buffer_max: Callable[[], int],
        get_event_buffer_ttl_sec: Callable[[], int],
        get_sub_queue_max: Callable[[], int],
        enqueue_event_artifact: Callable[[str, dict[str, Any]], None],
        logger: logging.Logger,
    ) -> None:
        self._lock = lock
        self._runs = runs
        self._get_event_buffer_max = get_event_buffer_max
        self._get_event_buffer_ttl_sec = get_event_buffer_ttl_sec
        self._get_sub_queue_max = get_sub_queue_max
        self._enqueue_event_artifact = enqueue_event_artifact
        self._logger = logger

        # Best-effort concurrent connection limits. Cached to avoid reading env on every
        # `subscribe()` call.
        self._max_subs_total = _safe_int_env("SIMULATOR_SSE_MAX_CONNECTIONS", 50)
        self._max_subs_per_run = _safe_int_env(
            "SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN", 10
        )

    def _count_total_subs_locked(self) -> int:
        """Counts subscriptions across all runs.

        Caller must hold `_lock`.
        """
        return sum(len(r._subs) for r in self._runs.values())

    def next_event_id(self, run: RunRecord) -> str:
        """Allocates a monotonically increasing event id for a run (best-effort)."""
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

    def prune_event_buffer_locked(
        self, run: RunRecord, *, now: Optional[float] = None
    ) -> None:
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
            run._event_buffer.append(
                (
                    now,
                    event_id,
                    event_equivalent if event_type != "run_status" else "",
                    payload,
                )
            )
            self.prune_event_buffer_locked(run, now=now)

    def replay_events(
        self, *, run_id: str, equivalent: str, after_event_id: str
    ) -> list[dict[str, Any]]:
        """Returns buffered events after `after_event_id` for SSE reconnect replay."""
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
        for _ts, event_id, event_equivalent, payload in buf:
            seq = self.event_seq_from_event_id(run_id=run_id, event_id=event_id)
            if seq is None or seq <= after_seq:
                continue
            event_type = str(payload.get("type") or "")
            if event_type != "run_status" and event_equivalent != equivalent:
                continue
            out.append(payload)
        return out

    def broadcast(self, run_id: str, payload: dict[str, Any]) -> None:
        """Broadcasts one event payload to current subscribers of the run."""
        run = self._runs.get(run_id)
        if run is None:
            return

        event_type = str(payload.get("type") or "")
        event_equivalent = str(payload.get("equivalent") or "")

        # Record for best-effort replay.
        self.append_to_event_buffer(run_id=run_id, payload=payload)

        # Best-effort raw events export.
        if event_type != "run_status":
            try:
                self._enqueue_event_artifact(run_id, payload)
            except Exception:
                self._logger.exception(
                    "simulator.sse.enqueue_event_artifact_failed run_id=%s", run_id
                )

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
                        self._logger.debug(
                            "simulator.sse.run_status_drop_failed run_id=%s",
                            run_id,
                            exc_info=True,
                        )
                continue

    async def subscribe(
        self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None
    ) -> _Subscription:
        """Creates a new SSE subscription queue.

        Enforces best-effort concurrent connection limits via env:
        `SIMULATOR_SSE_MAX_CONNECTIONS` and `SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN`.
        """
        queue_max = max(1, int(self._get_sub_queue_max()))
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_max)
        sub = _Subscription(equivalent=equivalent, queue=queue)

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return sub

            max_total = self._max_subs_total
            max_per_run = self._max_subs_per_run

            if max_total > 0:
                total = self._count_total_subs_locked()
                if total >= max_total:
                    raise TooManyRequestsException(
                        "Too many concurrent SSE connections",
                        details={"max_total": max_total, "total": total},
                    )

            if max_per_run > 0:
                cur = len(run._subs)
                if cur >= max_per_run:
                    raise TooManyRequestsException(
                        "Too many concurrent SSE connections for run",
                        details={
                            "max_per_run": max_per_run,
                            "run_subs": cur,
                            "run_id": run_id,
                        },
                    )

            run._subs.append(sub)

        if after_event_id:
            for evt in self.replay_events(
                run_id=run_id, equivalent=equivalent, after_event_id=after_event_id
            ):
                try:
                    sub.queue.put_nowait(evt)
                except asyncio.QueueFull:
                    break

        return sub

    async def unsubscribe(self, run_id: str, sub: _Subscription) -> None:
        """Removes a previously created subscription (best-effort)."""
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

        oldest_seq = self.event_seq_from_event_id(
            run_id=run_id, event_id=oldest_event_id
        )
        if oldest_seq is None:
            return False
        return after_seq < oldest_seq
