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


class SseReplayUnavailable(Exception):
    """The requested replay cannot be delivered as one ordered subscription prefix."""


SSE_SUBSCRIPTION_CLOSED_TYPE = "__subscription_closed__"


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
        self._queue_full_close_total = 0

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
        """Legacy allocation helper for tests that construct payloads directly.

        Production producers must use ``publish_event`` so ID allocation, replay
        admission and subscriber delivery share one ordering boundary.
        """
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

    def _append_to_event_buffer_locked(
        self, *, run: RunRecord, payload: dict[str, Any], now: Optional[float] = None
    ) -> None:
        event_id = str(payload.get("event_id") or "")
        if not event_id:
            return

        seq = self.event_seq_from_event_id(run_id=run.run_id, event_id=event_id)
        # Only buffer standard monotonically-increasing runtime event ids.
        if seq is None:
            return

        if now is None:
            now = time.time()
        event_type = str(payload.get("type") or "")
        event_equivalent = str(payload.get("equivalent") or "")
        run._event_buffer.append(
            (
                now,
                event_id,
                event_equivalent if event_type != "run_status" else "",
                payload,
            )
        )
        self.prune_event_buffer_locked(run, now=now)

    def _replay_events_locked(
        self,
        *,
        run: RunRecord,
        equivalent: str,
        after_event_id: str,
    ) -> list[dict[str, Any]]:
        """Return a validated replay snapshot while the caller holds ``_lock``."""
        after_seq = self.event_seq_from_event_id(
            run_id=run.run_id, event_id=after_event_id
        )
        if after_seq is None:
            raise SseReplayUnavailable("Last-Event-ID is not a runtime event id")

        self.prune_event_buffer_locked(run)
        current_seq = int(run._event_seq)
        if after_seq > current_seq:
            raise SseReplayUnavailable("Last-Event-ID is ahead of the run event sequence")

        if after_seq < current_seq:
            if not run._event_buffer:
                raise SseReplayUnavailable("Requested replay is no longer retained")
            oldest_seq = self.event_seq_from_event_id(
                run_id=run.run_id, event_id=run._event_buffer[0][1]
            )
            # A cursor immediately before the oldest retained event is replayable.
            if oldest_seq is None or after_seq < oldest_seq - 1:
                raise SseReplayUnavailable("Requested replay is no longer retained")

        out: list[dict[str, Any]] = []
        for _ts, event_id, event_equivalent, payload in run._event_buffer:
            seq = self.event_seq_from_event_id(run_id=run.run_id, event_id=event_id)
            if seq is None or seq <= after_seq:
                continue
            event_type = str(payload.get("type") or "")
            if event_type != "run_status" and event_equivalent != equivalent:
                continue
            out.append(payload)
        return out

    def _close_subscription_locked(
        self, *, run: RunRecord, sub: _Subscription, reason: str
    ) -> None:
        if sub.closed:
            return
        sub.closed = True
        sub.close_reason = reason
        sub.replay_bootstrap_pending = False
        sub.replay_bootstrap_tail.clear()
        try:
            run._subs.remove(sub)
        except ValueError:
            pass

        # Discard every not-yet-delivered event after the gap and wake the stream.
        # Reconnect uses the last event actually acknowledged by the client and
        # recovers the complete suffix from the replay buffer.
        while True:
            try:
                sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        sub.queue.put_nowait({"type": SSE_SUBSCRIPTION_CLOSED_TYPE, "reason": reason})

    def _dispatch_locked(self, *, run: RunRecord, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type") or "")
        event_equivalent = str(payload.get("equivalent") or "")
        self._append_to_event_buffer_locked(run=run, payload=payload)

        for sub in list(run._subs):
            if sub.closed:
                continue
            if event_type != "run_status" and sub.equivalent != event_equivalent:
                continue
            if sub.replay_bootstrap_pending:
                tail_max = max(1, int(getattr(sub.queue, "maxsize", 0) or 0))
                if len(sub.replay_bootstrap_tail) >= tail_max:
                    self._queue_full_close_total += 1
                    self._close_subscription_locked(
                        run=run, sub=sub, reason="replay_bootstrap_overflow"
                    )
                    self._logger.warning(
                        "simulator.sse.bootstrap_overflow_close run_id=%s qmax=%d closes_total=%d",
                        run.run_id,
                        tail_max,
                        self._queue_full_close_total,
                    )
                    continue
                sub.replay_bootstrap_tail.append(payload)
                continue

            try:
                sub.queue.put_nowait(payload)
            except asyncio.QueueFull:
                self._queue_full_drop_total += 1
                self._queue_full_drop_by_type[event_type] = (
                    self._queue_full_drop_by_type.get(event_type, 0) + 1
                )
                self._queue_full_close_total += 1
                self._close_subscription_locked(
                    run=run, sub=sub, reason="live_queue_overflow"
                )
                self._logger.warning(
                    "simulator.sse.queue_overflow_close event_type=%s run_id=%s qmax=%d subs_total=%d drops_total=%d drops_by_type=%d closes_total=%d",
                    event_type,
                    run.run_id,
                    int(getattr(sub.queue, "maxsize", 0) or 0),
                    int(self._count_total_subs_locked()),
                    int(self._queue_full_drop_total),
                    int(self._queue_full_drop_by_type.get(event_type, 0)),
                    int(self._queue_full_close_total),
                )

    def publish_event(
        self,
        *,
        run_id: str,
        payload_factory: Callable[[str], dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Allocate and publish one event under the shared producer-order lock."""
        artifact_payload: dict[str, Any] | None = None
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            next_seq = int(run._event_seq) + 1
            event_id = f"evt_{run.run_id}_{next_seq:06d}"
            payload = payload_factory(event_id)
            payload = dict(payload)
            payload["event_id"] = event_id
            run._event_seq = next_seq
            self._dispatch_locked(run=run, payload=payload)
            if str(payload.get("type") or "") != "run_status":
                artifact_payload = payload

        if artifact_payload is not None:
            try:
                self._enqueue_event_artifact(run_id, artifact_payload)
            except Exception:
                self._logger.exception(
                    "simulator.sse.enqueue_event_artifact_failed run_id=%s", run_id
                )
        return payload

    def broadcast(self, run_id: str, payload: dict[str, Any]) -> None:
        """Compatibility path for already-ID'd test payloads.

        Runtime producers use ``publish_event`` to make allocation and dispatch
        indivisible. This method still serializes buffer/queue delivery.
        """
        artifact_payload: dict[str, Any] | None = None
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            self._dispatch_locked(run=run, payload=payload)
            if str(payload.get("type") or "") != "run_status":
                artifact_payload = payload

        # Best-effort raw events export.
        if artifact_payload is not None:
            try:
                self._enqueue_event_artifact(run_id, artifact_payload)
            except Exception:
                self._logger.exception(
                    "simulator.sse.enqueue_event_artifact_failed run_id=%s", run_id
                )

    async def subscribe(
        self,
        run_id: str,
        *,
        equivalent: str,
        after_event_id: Optional[str] = None,
        bootstrap_event_factory: Optional[Callable[[str], dict[str, Any]]] = None,
    ) -> _Subscription:
        """Creates a new SSE subscription queue.

        Enforces best-effort concurrent connection limits via env:
        `SIMULATOR_SSE_MAX_CONNECTIONS` and `SIMULATOR_SSE_MAX_CONNECTIONS_PER_RUN`.
        """
        queue_max = max(1, int(self._get_sub_queue_max()))
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_max)
        sub = _Subscription(
            equivalent=equivalent,
            queue=queue,
            replay_bootstrap_pending=bootstrap_event_factory is not None,
        )

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

            replay: list[dict[str, Any]] = []
            if after_event_id is not None:
                replay = self._replay_events_locked(
                    run=run,
                    equivalent=equivalent,
                    after_event_id=after_event_id,
                )
                # Reserve one queue slot for the authoritative status published by
                # the stream bootstrap. Silently truncating replay would advance the
                # client cursor past state it never received.
                if len(replay) + (1 if bootstrap_event_factory else 0) > queue_max:
                    raise SseReplayUnavailable(
                        "Requested replay does not fit the subscriber queue"
                    )

            bootstrap_event = None
            if bootstrap_event_factory is not None:
                next_seq = int(run._event_seq) + 1
                bootstrap_event_id = f"evt_{run.run_id}_{next_seq:06d}"
                bootstrap_event = dict(bootstrap_event_factory(bootstrap_event_id))
                bootstrap_event["event_id"] = bootstrap_event_id
                run._event_seq = next_seq
            if bootstrap_event is not None:
                self._append_to_event_buffer_locked(run=run, payload=bootstrap_event)

            # Replay and its authoritative status are installed under the same lock
            # that exposes the subscriber. Any broadcast that can see this
            # subscription therefore queues after the complete bootstrap prefix.
            for evt in replay:
                sub.queue.put_nowait(evt)
            if bootstrap_event is not None:
                sub.queue.put_nowait(bootstrap_event)
            run._subs.append(sub)

        return sub

    def finish_replay_bootstrap(
        self, *, run_id: str, sub: _Subscription
    ) -> Optional[list[dict[str, Any]]]:
        """Freeze the pre-finalization live tail and expose future events to the queue."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or sub.closed or sub not in run._subs:
                return None
            tail = list(sub.replay_bootstrap_tail)
            sub.replay_bootstrap_tail.clear()
            sub.replay_bootstrap_pending = False
            return tail

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

    def _publish(
        self,
        *,
        run_id: str,
        run: RunRecord,
        payload_factory: Callable[[str], dict[str, Any]],
    ) -> Optional[str]:
        publish_event = getattr(self._sse, "publish_event", None)
        if callable(publish_event):
            payload = publish_event(run_id=run_id, payload_factory=payload_factory)
            return str(payload["event_id"]) if payload is not None else None

        # Compatibility for narrow test doubles that predate atomic publishing.
        event_id = self._sse.next_event_id(run)
        payload = payload_factory(event_id)
        self._sse.broadcast(run_id, payload)
        return event_id

    def emit_topology_edge_patch(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        edge_patch: list[dict[str, Any]],
        reason: str,
    ) -> Optional[str]:
        """Emit topology.changed with an edge_patch payload (no full refresh needed)."""

        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper or not edge_patch:
                return None

            payload = TopologyChangedPayload(edge_patch=edge_patch)
            return self._publish(
                run_id=run_id,
                run=run,
                payload_factory=lambda event_id: SimulatorTopologyChangedEvent(
                    event_id=event_id,
                    ts=self._utc_now(),
                    type="topology.changed",
                    equivalent=eq_upper,
                    payload=payload,
                    reason=reason,
                ).model_dump(mode="json", by_alias=True),
            )
        except Exception:
            self._logger.warning(
                "simulator.real.topology_edge_patch_broadcast_error eq=%s reason=%s",
                str(equivalent),
                str(reason),
                exc_info=True,
            )
            return None

    def emit_topology_changed(
        self,
        *,
        run_id: str,
        run: RunRecord,
        equivalent: str,
        payload: TopologyChangedPayload,
        reason: str | None = None,
    ) -> Optional[str]:
        """Emit a topology.changed event with an explicit payload.

        Caller is responsible for deciding whether an empty payload should be skipped.
        """

        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return None

            def build(event_id: str) -> dict[str, Any]:
                evt_kwargs: dict[str, Any] = {
                    "event_id": event_id,
                    "ts": self._utc_now(),
                    "type": "topology.changed",
                    "equivalent": eq_upper,
                    "payload": payload,
                }
                if reason is not None:
                    evt_kwargs["reason"] = reason
                return SimulatorTopologyChangedEvent(**evt_kwargs).model_dump(
                    mode="json", by_alias=True
                )

            return self._publish(
                run_id=run_id,
                run=run,
                payload_factory=build,
            )
        except Exception:
            self._logger.warning(
                "simulator.sse.topology_changed_emit_error eq=%s reason=%s",
                str(equivalent),
                str(reason),
                exc_info=True,
            )
            return None

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
    ) -> Optional[str]:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return None

            return self._publish(
                run_id=run_id,
                run=run,
                payload_factory=lambda allocated_id: SimulatorTxFailedEvent(
                    event_id=allocated_id,
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
                ).model_dump(mode="json", by_alias=True),
            )
        except Exception:
            self._logger.warning(
                "simulator.sse.tx_failed_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )
            return None

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
    ) -> Optional[str]:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return None

            def build(allocated_id: str) -> dict[str, Any]:
                evt_kwargs: dict[str, Any] = {
                    "event_id": allocated_id,
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
                # The model declares both patch fields (011/T1104), so they go through
                # the constructor instead of being appended to the dumped dict. They are
                # excluded from the dump when absent because the wire never carried them
                # as explicit nulls, and this program does not change wire shapes.
                evt_kwargs["edge_patch"] = edge_patch or None
                evt_kwargs["node_patch"] = node_patch or None
                absent = {
                    key
                    for key in ("edge_patch", "node_patch")
                    if evt_kwargs[key] is None
                }
                return SimulatorTxUpdatedEvent(**evt_kwargs).model_dump(
                    mode="json", by_alias=True, exclude=absent or None
                )

            return self._publish(run_id=run_id, run=run, payload_factory=build)
        except Exception:
            self._logger.warning(
                "simulator.sse.tx_updated_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )
            return None

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
    ) -> Optional[str]:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return None

            def build(allocated_id: str) -> dict[str, Any]:
                done_kwargs: dict[str, Any] = {
                    "event_id": allocated_id,
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
                return SimulatorClearingDoneEvent(**done_kwargs).model_dump(
                    mode="json", by_alias=True
                )

            return self._publish(run_id=run_id, run=run, payload_factory=build)
        except Exception:
            self._logger.warning(
                "simulator.sse.clearing_done_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )
            return None

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
    ) -> Optional[str]:
        try:
            eq_upper = str(equivalent or "").strip().upper()
            if not eq_upper:
                return None

            return self._publish(
                run_id=run_id,
                run=run,
                payload_factory=lambda allocated_id: SimulatorAuditDriftEvent(
                    event_id=allocated_id,
                    ts=self._utc_now(),
                    type="audit.drift",
                    equivalent=eq_upper,
                    tick_index=int(tick_index),
                    severity=str(severity),
                    total_drift=str(total_drift),
                    drifts=list(drifts or []),
                    source=str(source),
                ).model_dump(mode="json", by_alias=True),
            )
        except Exception:
            self._logger.warning(
                "simulator.sse.audit_drift_emit_error eq=%s",
                str(equivalent),
                exc_info=True,
            )
            return None
