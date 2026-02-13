from __future__ import annotations

import asyncio
import logging
import time
import threading
from typing import Any, Callable, Optional

from app.core.simulator.models import RunRecord, _Subscription
from app.core.simulator.runtime_utils import safe_int_env as _safe_int_env
from app.schemas.simulator import (
    SimulatorAuditDriftEvent,
    SimulatorClearingDoneEvent,
    SimulatorTopologyChangedEvent,
    SimulatorTxFailedEvent,
    SimulatorTxUpdatedEvent,
    TopologyChangedPayload,
)
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

        # Best-effort observability counters (in-memory only).
        # Used to make drops visible in logs, per simulator plan section 10.
        self._queue_full_drop_total = 0
        self._queue_full_drop_by_type: dict[str, int] = {}
        self._queue_full_eviction_total = 0

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
                        self._queue_full_eviction_total += 1
                        continue
                    except Exception:
                        self._logger.debug(
                            "simulator.sse.run_status_drop_failed run_id=%s",
                            run_id,
                            exc_info=True,
                        )
                elif (
                    event_type == "tx.updated"
                    and bool(payload.get("amount_flyout")) is True
                ):
                    # Best-effort priority for tx.updated that are meant to produce amount flyouts.
                    # Evict one queued item to make room (same strategy as run_status).
                    try:
                        _ = sub.queue.get_nowait()
                        sub.queue.put_nowait(payload)
                        self._queue_full_eviction_total += 1
                        continue
                    except Exception:
                        self._logger.debug(
                            "simulator.sse.amount_flyout_priority_drop_failed run_id=%s",
                            run_id,
                            exc_info=True,
                        )

                # Record drop counters.
                self._queue_full_drop_total += 1
                self._queue_full_drop_by_type[event_type] = (
                    self._queue_full_drop_by_type.get(event_type, 0) + 1
                )
                self._logger.warning(
                    "simulator.sse.queue_full_drop event_type=%s run_id=%s qsize=%d qmax=%d subs_total=%d drops_total=%d drops_by_type=%d evictions_total=%d",
                    event_type,
                    run_id,
                    sub.queue.qsize(),
                    int(getattr(sub.queue, "maxsize", 0) or 0),
                    int(self._count_total_subs_locked()),
                    int(self._queue_full_drop_total),
                    int(self._queue_full_drop_by_type.get(event_type, 0)),
                    int(self._queue_full_eviction_total),
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


class SseEventEmitter:
    """Domain-level SSE event construction.

    SseBroadcast is a transport (queues + replay). This emitter centralizes
    strict alias serialization policy: always `model_dump(mode="json", by_alias=True)`.
    """

    def __init__(
        self,
        *,
        sse: SseBroadcast,
        utc_now,
        logger: logging.Logger,
    ) -> None:
        self._sse = sse
        self._utc_now = utc_now
        self._logger = logger

    def emit_topology_edge_patch(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        edge_patch: list[dict[str, Any]],
        reason: str,
    ) -> None:
        """Emit topology.changed with an edge_patch payload (no full refresh needed)."""

        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper or not edge_patch:
                return

            payload = TopologyChangedPayload(edge_patch=edge_patch)
            evt = SimulatorTopologyChangedEvent(
                event_id=self._sse.next_event_id(run),
                ts=self._utc_now(),
                type="topology.changed",
                equivalent=eq_upper,
                payload=payload,
                reason=reason,
            ).model_dump(mode="json", by_alias=True)

            self._sse.broadcast(run_id, evt)
        except Exception:
            self._logger.warning(
                "simulator.real.topology_edge_patch_broadcast_error eq=%s reason=%s",
                str(equivalent),
                str(reason),
                exc_info=True,
            )

    def emit_topology_changed(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        payload: TopologyChangedPayload,
        reason: str | None = None,
    ) -> None:
        """Emit a topology.changed event with an explicit payload.

        Caller is responsible for deciding whether an empty payload should be skipped.
        """

        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return

            evt_kwargs: dict[str, Any] = {
                "event_id": self._sse.next_event_id(run),
                "ts": self._utc_now(),
                "type": "topology.changed",
                "equivalent": eq_upper,
                "payload": payload,
            }
            if reason is not None:
                evt_kwargs["reason"] = reason

            evt = SimulatorTopologyChangedEvent(**evt_kwargs).model_dump(
                mode="json",
                by_alias=True,
            )
            self._sse.broadcast(run_id, evt)
        except Exception:
            self._logger.warning(
                "simulator.sse.topology_changed_emit_error eq=%s reason=%s",
                str(equivalent),
                str(reason),
                exc_info=True,
            )

    def emit_tx_failed(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        from_pid: str,
        to_pid: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> None:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return

            failed_evt = SimulatorTxFailedEvent(
                event_id=str(event_id) if event_id is not None else self._sse.next_event_id(run),
                ts=self._utc_now(),
                type="tx.failed",
                equivalent=eq_upper,
                from_=str(from_pid),
                to=str(to_pid),
                error={
                    "code": str(error_code),
                    "message": str(error_message),
                    "at": self._utc_now(),
                    "details": error_details,
                },
            ).model_dump(mode="json", by_alias=True)
            self._sse.broadcast(run_id, failed_evt)
        except Exception:
            self._logger.warning(
                "simulator.sse.tx_failed_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )

    def emit_tx_updated(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        from_pid: str | None,
        to_pid: str | None,
        amount: str | None,
        amount_flyout: bool,
        ttl_ms: int,
        edges: list[dict[str, Any]],
        node_badges: list[dict[str, Any]] | None = None,
        intensity_key: str | None = None,
        edge_patch: list[dict[str, Any]] | None = None,
        node_patch: list[dict[str, Any]] | None = None,
        event_id: str | None = None,
    ) -> None:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return

            evt_kwargs: dict[str, Any] = {
                "event_id": str(event_id) if event_id is not None else self._sse.next_event_id(run),
                "ts": self._utc_now(),
                "type": "tx.updated",
                "equivalent": eq_upper,
                "amount_flyout": bool(amount_flyout),
                "ttl_ms": int(ttl_ms),
                "edges": edges,
                "node_badges": node_badges,
            }
            if from_pid is not None:
                evt_kwargs["from_"] = str(from_pid)
            if to_pid is not None:
                evt_kwargs["to"] = str(to_pid)
            if amount is not None:
                evt_kwargs["amount"] = str(amount)
            if intensity_key is not None:
                evt_kwargs["intensity_key"] = str(intensity_key)

            evt = SimulatorTxUpdatedEvent(**evt_kwargs).model_dump(
                mode="json", by_alias=True
            )

            # These patches are intentionally added post-schema (runtime extension).
            if edge_patch:
                evt["edge_patch"] = edge_patch
            if node_patch:
                evt["node_patch"] = node_patch

            self._sse.broadcast(run_id, evt)
        except Exception:
            self._logger.warning(
                "simulator.sse.tx_updated_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )

    def emit_clearing_done(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        plan_id: str,
        cleared_cycles: int | None = None,
        cleared_amount: str | None = None,
        cycle_edges: list[dict[str, Any]] | None = None,
        node_patch: list[dict[str, Any]] | None = None,
        edge_patch: list[dict[str, Any]] | None = None,
        event_id: str | None = None,
    ) -> None:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return

            done_kwargs: dict[str, Any] = {
                "event_id": str(event_id) if event_id is not None else self._sse.next_event_id(run),
                "ts": self._utc_now(),
                "type": "clearing.done",
                "equivalent": eq_upper,
                "plan_id": str(plan_id),
            }
            if cleared_cycles is not None:
                done_kwargs["cleared_cycles"] = int(cleared_cycles)
            if cleared_amount is not None:
                done_kwargs["cleared_amount"] = str(cleared_amount)
            if cycle_edges is not None:
                done_kwargs["cycle_edges"] = cycle_edges
            if node_patch is not None:
                done_kwargs["node_patch"] = node_patch
            if edge_patch is not None:
                done_kwargs["edge_patch"] = edge_patch

            done_evt = SimulatorClearingDoneEvent(**done_kwargs).model_dump(
                mode="json", by_alias=True
            )
            self._sse.broadcast(run_id, done_evt)
        except Exception:
            self._logger.warning(
                "simulator.sse.clearing_done_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )

    def emit_audit_drift(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        tick_index: int,
        severity: str,
        total_drift: str,
        drifts: list[dict[str, Any]],
        source: str,
        event_id: str | None = None,
    ) -> None:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return

            evt = SimulatorAuditDriftEvent(
                event_id=str(event_id) if event_id is not None else self._sse.next_event_id(run),
                ts=self._utc_now(),
                type="audit.drift",
                equivalent=eq_upper,
                tick_index=int(tick_index),
                severity=str(severity),
                total_drift=str(total_drift),
                drifts=list(drifts or []),
                source=str(source),
            ).model_dump(mode="json", by_alias=True)

            self._sse.broadcast(run_id, evt)
        except Exception:
            self._logger.warning(
                "simulator.sse.audit_drift_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )
