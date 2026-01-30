from __future__ import annotations

import asyncio
import logging
import json
import os
import random
import secrets
import hashlib
import time
import threading
import uuid
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import select, func

from app.schemas.simulator import (
    SIMULATOR_API_VERSION,
    BottleneckItem,
    BottleneckTargetEdge,
    BottleneckReasonCode,
    BottlenecksResponse,
    MetricPoint,
    MetricSeries,
    MetricSeriesKey,
    MetricsResponse,
    RunMode,
    RunState,
    RunStatus,
    ScenarioSummary,
    ArtifactIndex,
    ArtifactItem,
    SimulatorGraphLink,
    SimulatorGraphNode,
    SimulatorGraphSnapshot,
    SimulatorVizSize,
    SimulatorClearingDoneEvent,
    SimulatorClearingPlanEvent,
    SimulatorTxFailedEvent,
    SimulatorTxUpdatedEvent,
    SimulatorRunStatusEvent,
)
from app.db.models.equivalent import Equivalent
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from app.db.models.simulator_storage import SimulatorRun, SimulatorRunBottleneck, SimulatorRunMetric
from app.db.models.trustline import TrustLine
import app.db.session as db_session
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.payments.service import PaymentService
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.core.simulator.models import RunRecord, ScenarioRecord, _Subscription
from app.core.simulator.scenario_registry import ScenarioRegistry
from app.core.simulator.snapshot_builder import SnapshotBuilder
import app.core.simulator.storage as simulator_storage
from app.core.simulator.sse_broadcast import SseBroadcast
from app.utils.exceptions import BadRequestException, NotFoundException, RoutingException, TimeoutException, GeoException

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _repo_root() -> Path:
    # runtime.py -> app/core/simulator/runtime.py
    return Path(__file__).resolve().parents[3]


def _local_state_dir() -> Path:
    # Ignored by .gitignore
    return _repo_root() / ".local-run" / "simulator"


FIXTURES_DIR = _repo_root() / "fixtures" / "simulator"
SCENARIO_SCHEMA_PATH = FIXTURES_DIR / "scenario.schema.json"

# Runner constants (MVP, see docs/ru/simulator/backend/runner-algorithm.md)
TICK_MS_BASE = 1000
ACTIONS_PER_TICK_MAX = 20
CLEARING_EVERY_N_TICKS = 25

# Real-mode guardrails (PR-B hardening). Values can be overridden via env vars.
REAL_MAX_IN_FLIGHT_DEFAULT = 1
REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT = 3
REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT = 5
REAL_MAX_ERRORS_TOTAL_DEFAULT = 200


def _map_rejection_code(err_details: Any) -> str:
    """Map structured error details to a stable simulator rejection code.

    This is intentionally a best-effort mapping for UI/analytics.
    Full diagnostics remain available in tx.failed.error.details.
    """

    default = "PAYMENT_REJECTED"
    if not isinstance(err_details, dict):
        return default

    exc_name = str(err_details.get("exc") or "")
    geo_code = str(err_details.get("geo_code") or "")
    message = str(err_details.get("message") or "")

    if exc_name == "RoutingException":
        # E002 = insufficient capacity, E001 = generic routing not-found.
        if geo_code == "E002":
            return "ROUTING_NO_CAPACITY"
        if geo_code == "E001":
            return "ROUTING_NO_ROUTE"
        return "ROUTING_REJECTED"

    if exc_name == "TrustLineException":
        # E003 = limit exceeded, E004 = trust line inactive.
        if geo_code == "E003":
            return "TRUSTLINE_LIMIT_EXCEEDED"
        if geo_code == "E004":
            return "TRUSTLINE_NOT_ACTIVE"
        return "TRUSTLINE_REJECTED"


class _SimulatorRuntimeBase:
    """In-process simulator runtime (MVP).

    Provides:
    - Scenario registry (fixtures + uploaded)
    - Runs registry
    - SSE-friendly pub/sub per run

    NOTE: This is intentionally in-memory and best-effort for MVP.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scenarios: dict[str, ScenarioRecord] = {}
        self._runs: dict[str, RunRecord] = {}
        self._active_run_id: Optional[str] = None

        self._artifacts = ArtifactsManager(
            lock=self._lock,
            runs=self._runs,
            local_state_dir=_local_state_dir,
            utc_now=_utc_now,
            db_enabled=simulator_storage.db_enabled,
            logger=logger,
        )

        self._metrics_bottlenecks = MetricsBottlenecks(
            lock=self._lock,
            runs=self._runs,
            scenarios=self._scenarios,
            utc_now=_utc_now,
            db_enabled=simulator_storage.db_enabled,
        )

        self._snapshot_builder = SnapshotBuilder(
            lock=self._lock,
            runs=self._runs,
            scenarios=self._scenarios,
            utc_now=_utc_now,
            db_enabled=simulator_storage.db_enabled,
        )

        self._scenario_registry = ScenarioRegistry(
            lock=self._lock,
            scenarios=self._scenarios,
            fixtures_dir=FIXTURES_DIR,
            schema_path=SCENARIO_SCHEMA_PATH,
            local_state_dir=_local_state_dir(),
            utc_now=_utc_now,
            logger=logger,
        )
        self._scenario_registry.load_all()

        # SSE replay buffer sizing/TTL. Best-effort; does not change OpenAPI.
        self._event_buffer_max = int(os.environ.get("SIMULATOR_EVENT_BUFFER_SIZE", "2000"))
        self._event_buffer_ttl_sec = int(os.environ.get("SIMULATOR_EVENT_BUFFER_TTL_SEC", "600"))

        # If enabled, SSE endpoints may return 410 when Last-Event-ID is too old
        # to be replayed from the in-memory ring buffer.
        self._sse_strict_replay = bool(int(os.environ.get("SIMULATOR_SSE_STRICT_REPLAY", "0")))

        self._sse = SseBroadcast(
            lock=self._lock,
            runs=self._runs,
            get_event_buffer_max=lambda: int(self._event_buffer_max),
            get_event_buffer_ttl_sec=lambda: int(self._event_buffer_ttl_sec),
            enqueue_event_artifact=self._artifacts.enqueue_event_artifact,
        )

    def is_sse_strict_replay_enabled(self) -> bool:
        return self._sse_strict_replay

    def is_replay_too_old(self, *, run_id: str, after_event_id: str) -> bool:
        """Returns True if after_event_id is older than the oldest event in buffer.

        This is best-effort and only applies to standard runtime event ids.
        """
        return self._sse.is_replay_too_old(run_id=run_id, after_event_id=after_event_id)

    # -----------------------------
    # Scenarios
    # -----------------------------

    def list_scenarios(self) -> list[ScenarioSummary]:
        with self._lock:
            items = sorted(self._scenarios.values(), key=lambda s: s.scenario_id)
        return [s.summary() for s in items]

    def get_scenario(self, scenario_id: str) -> ScenarioRecord:
        with self._lock:
            rec = self._scenarios.get(scenario_id)
        if rec is None:
            raise NotFoundException(f"Scenario {scenario_id} not found")
        return rec

    def save_uploaded_scenario(self, scenario: dict[str, Any]) -> ScenarioRecord:
        return self._scenario_registry.save_uploaded_scenario(scenario)

    # -----------------------------
    # Runs
    # -----------------------------

    def get_active_run_id(self) -> Optional[str]:
        with self._lock:
            return self._active_run_id

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException(f"Run {run_id} not found")
        return run

    def get_run_status(self, run_id: str) -> RunStatus:
        run = self.get_run(run_id)
        return _run_to_status(run)

    async def create_run(self, *, scenario_id: str, mode: RunMode, intensity_percent: int) -> str:
        # Validate scenario exists (even for real mode for now).
        _ = self.get_scenario(scenario_id)

        run_id = _new_run_id()
        seed_material = hashlib.sha256(run_id.encode("utf-8")).digest()
        seed = int.from_bytes(seed_material[:4], "big")
        run = RunRecord(
            run_id=run_id,
            scenario_id=scenario_id,
            mode=mode,
            state="running",
            started_at=_utc_now(),
            seed=seed,
            tick_index=0,
            intensity_percent=intensity_percent,
        )

        if mode == "real":
            try:
                run._real_max_in_flight = max(
                    1,
                    int(os.getenv("SIMULATOR_REAL_MAX_IN_FLIGHT", str(REAL_MAX_IN_FLIGHT_DEFAULT))),
                )
            except Exception:
                run._real_max_in_flight = REAL_MAX_IN_FLIGHT_DEFAULT

        # Precompute per-equivalent edges for quick event generation.
        scenario = self.get_scenario(scenario_id).raw
        run._edges_by_equivalent = _edges_by_equivalent(scenario)
        run._rng = random.Random(run_id)
        run._next_tx_at_ms = 0
        run._next_clearing_at_ms = 25_000
        run._clearing_pending_done_at_ms = None

        # Minimal local artifacts (best-effort).
        self._artifacts.init_run_artifacts(run)

        with self._lock:
            self._runs[run_id] = run
            self._active_run_id = run_id

        # Start artifacts writer (best-effort; no-op if artifacts disabled).
        self._artifacts.start_events_writer(run_id)

        # Start heartbeat loop.
        run._heartbeat_task = asyncio.create_task(self._heartbeat_loop(run_id), name=f"simulator-heartbeat:{run_id}")
        # Emit immediate status event (so SSE clients don't wait for first tick).
        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        await simulator_storage.sync_artifacts(run)
        return run_id

    async def build_metrics(
        self,
        *,
        run_id: str,
        equivalent: str,
        from_ms: int,
        to_ms: int,
        step_ms: int,
    ) -> MetricsResponse:
        return await self._metrics_bottlenecks.build_metrics(
            run_id=run_id,
            equivalent=equivalent,
            from_ms=from_ms,
            to_ms=to_ms,
            step_ms=step_ms,
        )

    async def build_bottlenecks(
        self,
        *,
        run_id: str,
        equivalent: str,
        limit: int,
        min_score: Optional[float],
    ) -> BottlenecksResponse:
        return await self._metrics_bottlenecks.build_bottlenecks(
            run_id=run_id,
            equivalent=equivalent,
            limit=limit,
            min_score=min_score,
        )

    async def list_artifacts(self, *, run_id: str) -> ArtifactIndex:
        return await self._artifacts.list_artifacts(run_id=run_id)

    def get_artifact_path(self, *, run_id: str, name: str) -> Path:
        return self._artifacts.get_artifact_path(run_id=run_id, name=name)

    def publish_run_status(self, run_id: str) -> None:
        run = self.get_run(run_id)
        payload = SimulatorRunStatusEvent(
            event_id=self._sse.next_event_id(run),
            ts=_utc_now(),
            type="run_status",
            run_id=run.run_id,
            scenario_id=run.scenario_id,
            state=run.state,
            sim_time_ms=run.sim_time_ms,
            intensity_percent=run.intensity_percent,
            ops_sec=run.ops_sec,
            queue_depth=run.queue_depth,
            last_event_type=run.last_event_type,
            current_phase=run.current_phase,
            last_error=_dict_to_last_error(run.last_error),
        ).model_dump(mode="json")
        self._sse.broadcast(run_id, payload)

    async def pause(self, run_id: str) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            if run.state == "paused":
                pass
            elif run.state == "running":
                run.state = "paused"
            elif run.state in ("stopping", "stopped", "error"):
                # Idempotent, but no-op.
                pass
            else:
                run.state = "paused"

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return _run_to_status(run)

    async def resume(self, run_id: str) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            if run.state == "running":
                pass
            elif run.state == "paused":
                run.state = "running"
            elif run.state in ("stopping", "stopped"):
                # Idempotent: keep stopped
                pass
            else:
                run.state = "running"

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return _run_to_status(run)

    async def stop(self, run_id: str) -> RunStatus:
        run = self.get_run(run_id)
        task = None
        events_task: Optional[asyncio.Task[None]] = None
        with self._lock:
            if run.state in ("stopped",):
                return _run_to_status(run)

            if run.state != "stopping":
                run.state = "stopping"
                task = run._heartbeat_task
                events_task = run._artifact_events_task

        self.publish_run_status(run_id)

        # Transition to stopped.
        with self._lock:
            run.state = "stopped"
            run.stopped_at = _utc_now()

        self.publish_run_status(run_id)

        await simulator_storage.upsert_run(run)

        # Enforce TTL-based pruning even if no further events are appended.
        with self._lock:
            self._sse.prune_event_buffer_locked(run)

        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if events_task is not None:
            await self._artifacts.stop_events_writer(run_id)

        # Finalize artifacts (best-effort): status.json, summary.json, bundle.zip.
        await self._artifacts.finalize_run_artifacts(
            run_id=run_id,
            status_payload=self.get_run_status(run_id).model_dump(mode="json"),
        )
        return _run_to_status(run)

    async def restart(self, run_id: str) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            run.sim_time_ms = 0
            run.tick_index = 0
            run.errors_total = 0
            run.last_error = None
            run.last_event_type = None
            run.current_phase = None
            run.queue_depth = 0
            run.ops_sec = 0.0
            run.stopped_at = None
            run.state = "running"
            run.started_at = _utc_now()

            # Keep buffer but prune to avoid unbounded growth across long sessions.
            self._sse.prune_event_buffer_locked(run)

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return _run_to_status(run)

    async def set_intensity(self, run_id: str, intensity_percent: int) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            run.intensity_percent = int(intensity_percent)

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return _run_to_status(run)

    async def subscribe(self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None) -> _Subscription:
        run = self.get_run(run_id)
        sub = await self._sse.subscribe(run_id, equivalent=equivalent, after_event_id=after_event_id)

        # For fixtures-mode UX/tests: emit one domain event immediately after subscribe.
        if after_event_id is None and run.state == "running" and run.mode == "fixtures":
            evt = self._maybe_make_tx_updated(run_id=run_id, equivalent=equivalent)
            if evt is not None:
                try:
                    sub.queue.put_nowait(evt)
                except asyncio.QueueFull:
                    pass
        return sub

    async def unsubscribe(self, run_id: str, sub: _Subscription) -> None:
        await self._sse.unsubscribe(run_id, sub)

    async def build_graph_snapshot(
        self,
        *,
        run_id: str,
        equivalent: str,
        session=None,
    ) -> SimulatorGraphSnapshot:
        return await self._snapshot_builder.build_graph_snapshot(
            run_id=run_id,
            equivalent=equivalent,
            session=session,
        )

    async def build_ego_snapshot(
        self,
        *,
        run_id: str,
        equivalent: str,
        pid: str,
        depth: int,
        session=None,
    ) -> SimulatorGraphSnapshot:
        snap = await self.build_graph_snapshot(run_id=run_id, equivalent=equivalent, session=session)
        if depth <= 0:
            return snap

        # Undirected BFS on links.
        neighbors: dict[str, set[str]] = {}
        for link in snap.links:
            neighbors.setdefault(link.source, set()).add(link.target)
            neighbors.setdefault(link.target, set()).add(link.source)

        visited: set[str] = {pid}
        frontier: set[str] = {pid}

        for _ in range(depth):
            nxt: set[str] = set()
            for cur in frontier:
                nxt |= neighbors.get(cur, set())
            nxt -= visited
            if not nxt:
                break
            visited |= nxt
            frontier = nxt

        nodes = [n for n in snap.nodes if n.id in visited]
        links = [l for l in snap.links if l.source in visited and l.target in visited]
        return SimulatorGraphSnapshot(
            equivalent=snap.equivalent,
            generated_at=snap.generated_at,
            nodes=nodes,
            links=links,
            palette=snap.palette,
            limits=snap.limits,
        )

    async def _heartbeat_loop(self, run_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                run = self.get_run(run_id)

                with self._lock:
                    if run.state != "running":
                        continue

                    # Runner-algorithm: fixed sim-time tick; intensity controls action budget.
                    run.tick_index += 1
                    run.sim_time_ms = run.tick_index * TICK_MS_BASE

                    # Real-mode sets queue_depth during tick work; fixtures-mode has no queue.
                    if run.mode == "fixtures":
                        run.queue_depth = 0

                    # Fixtures-mode event generation (best-effort).
                    if run.mode == "fixtures":
                        self._tick_fixtures_events(run_id)

                # Real-mode runner work is intentionally outside the lock
                # (it can touch the DB and may take time).
                if run.mode == "real" and run.state == "running":
                    await self._tick_real_mode(run_id)

                self.publish_run_status(run_id)
                await simulator_storage.upsert_run(run)
        except asyncio.CancelledError:
            return

    async def _tick_real_mode(self, run_id: str) -> None:
        run = self.get_run(run_id)
        scenario = self.get_scenario(run.scenario_id).raw

        try:
            async with db_session.AsyncSessionLocal() as session:
                if not run._real_seeded:
                    await self._seed_scenario_into_db(session, scenario)
                    await session.commit()
                    run._real_seeded = True

                if run._real_participants is None or run._real_equivalents is None:
                    run._real_participants = await self._load_real_participants(session, scenario)
                    run._real_equivalents = [str(x) for x in (scenario.get("equivalents") or []) if str(x).strip()]

                participants = run._real_participants or []
                equivalents = run._real_equivalents or []
                if len(participants) < 2 or not equivalents:
                    return

                planned = self._plan_real_payments(run, scenario)
                with self._lock:
                    run.ops_sec = float(len(planned))
                    run.queue_depth = len(planned)
                    run._real_in_flight = 0
                    run.current_phase = "payments" if planned else None
                sender_id_by_pid = {pid: participant_id for (participant_id, pid) in participants}

                max_timeouts_per_tick = int(
                    os.getenv("SIMULATOR_REAL_MAX_TIMEOUTS_PER_TICK", str(REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT))
                )
                max_errors_total = int(
                    os.getenv("SIMULATOR_REAL_MAX_ERRORS_TOTAL", str(REAL_MAX_ERRORS_TOTAL_DEFAULT))
                )

                committed = 0
                rejected = 0
                errors = 0
                timeouts = 0

                sem = asyncio.Semaphore(max(1, int(run._real_max_in_flight)))

                per_eq: dict[str, dict[str, int]] = {
                    str(eq): {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0} for eq in equivalents
                }
                per_eq_route: dict[str, dict[str, float]] = {
                    str(eq): {"route_len_sum": 0.0, "route_len_n": 0.0} for eq in equivalents
                }
                per_eq_metric_values: dict[str, dict[str, float]] = {str(eq): {} for eq in equivalents}

                per_eq_edge_stats: dict[str, dict[tuple[str, str], dict[str, int]]] = {str(eq): {} for eq in equivalents}

                def _edge_inc(eq: str, src: str, dst: str, key: str, n: int = 1) -> None:
                    m = per_eq_edge_stats.setdefault(str(eq), {})
                    st = m.setdefault((str(src), str(dst)), {"attempts": 0, "committed": 0, "rejected": 0, "errors": 0, "timeouts": 0})
                    st[key] = int(st.get(key, 0)) + int(n)

                async def _do_one(
                    action: _RealPaymentAction,
                ) -> tuple[
                    int,
                    str,
                    str,
                    str,
                    str | None,
                    str | None,
                    dict[str, Any] | None,
                    float,
                    list[tuple[str, str]],
                ]:
                    """Returns (seq, eq, sender_pid, receiver_pid, status, error_code, error_details, avg_route_len, route_edges)."""

                    sender_id = sender_id_by_pid.get(action.sender_pid)
                    if sender_id is None:
                        return (
                            action.seq,
                            action.equivalent,
                            action.sender_pid,
                            action.receiver_pid,
                            None,
                            "SENDER_NOT_FOUND",
                            {"reason": "SENDER_NOT_FOUND"},
                            0.0,
                            [],
                        )

                    idem = self._sim_idempotency_key(
                        run_id=run.run_id,
                        tick_ms=run.tick_index,
                        sender_pid=action.sender_pid,
                        receiver_pid=action.receiver_pid,
                        equivalent=action.equivalent,
                        amount=action.amount,
                        seq=action.seq,
                    )

                    async with sem:
                        with self._lock:
                            run._real_in_flight += 1

                        try:
                            async with db_session.AsyncSessionLocal() as s2:
                                service = PaymentService(s2)
                                res = await service.create_payment_internal(
                                    sender_id,
                                    to_pid=action.receiver_pid,
                                    equivalent=action.equivalent,
                                    amount=action.amount,
                                    idempotency_key=idem,
                                )
                                await s2.commit()

                            status = str(getattr(res, "status", None) or "")

                            route_edges: list[tuple[str, str]] = []
                            try:
                                routes = getattr(res, "routes", None)
                                if routes:
                                    for r in routes:
                                        path = getattr(r, "path", None)
                                        if not path or len(path) < 2:
                                            continue
                                        route_edges = [(str(a), str(b)) for a, b in zip(path, path[1:])]
                                        if route_edges:
                                            break
                            except Exception:
                                route_edges = []

                            avg_route_len = 0.0
                            try:
                                routes = getattr(res, "routes", None)
                                if routes:
                                    lens: list[float] = []
                                    for r in routes:
                                        path = getattr(r, "path", None)
                                        if not path or len(path) < 2:
                                            continue
                                        lens.append(float(max(1, int(len(path) - 1))))
                                    if lens:
                                        avg_route_len = float(sum(lens) / len(lens))
                            except Exception:
                                avg_route_len = 0.0

                            return (
                                action.seq,
                                action.equivalent,
                                action.sender_pid,
                                action.receiver_pid,
                                status,
                                None,
                                None,
                                float(avg_route_len),
                                route_edges,
                            )
                        except Exception as e:
                            # Classification:
                            # - Timeouts are errors.
                            # - GeoException with 4xx status are expected rejections (bad route, not found, etc).
                            # - Everything else is INTERNAL_ERROR.
                            code = "INTERNAL_ERROR"
                            status = None
                            err_details: dict[str, Any] | None = {
                                "exc": type(e).__name__,
                                "message": str(e),
                            }

                            if isinstance(e, TimeoutException):
                                code = "PAYMENT_TIMEOUT"
                                err_details = {
                                    "exc": type(e).__name__,
                                    "geo_code": getattr(e, "code", None),
                                    "message": getattr(e, "message", str(e)),
                                    "details": getattr(e, "details", None),
                                }
                            elif isinstance(e, GeoException):
                                geo_status = int(getattr(e, "status_code", 500) or 500)
                                # Treat client errors as clean rejection, not simulator errors_total.
                                if 400 <= geo_status < 500:
                                    status = "REJECTED"
                                    code = None
                                err_details = {
                                    "exc": type(e).__name__,
                                    "geo_code": getattr(e, "code", None),
                                    "message": getattr(e, "message", str(e)),
                                    "details": getattr(e, "details", None),
                                    "status_code": geo_status,
                                }
                            return (
                                action.seq,
                                action.equivalent,
                                action.sender_pid,
                                action.receiver_pid,
                                status,
                                code,
                                err_details,
                                0.0,
                                [],
                            )
                        finally:
                            with self._lock:
                                run._real_in_flight = max(0, run._real_in_flight - 1)

                tasks = [asyncio.create_task(_do_one(a)) for a in planned]

                next_seq = 0
                ready: dict[
                    int,
                    tuple[
                        str,
                        str,
                        str,
                        str | None,
                        str | None,
                        dict[str, Any] | None,
                        float,
                        list[tuple[str, str]],
                        list[dict[str, Any]] | None,
                    ],
                ] = {}

                def _inc(eq: str, key: str, n: int = 1) -> None:
                    d = per_eq.setdefault(
                        str(eq),
                        {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0},
                    )
                    d[key] = int(d.get(key, 0)) + int(n)

                def _route_add(eq: str, route_len: float) -> None:
                    d = per_eq_route.setdefault(str(eq), {"route_len_sum": 0.0, "route_len_n": 0.0})
                    d["route_len_sum"] = float(d.get("route_len_sum", 0.0)) + float(route_len)
                    d["route_len_n"] = float(d.get("route_len_n", 0.0)) + 1.0

                def _emit_if_ready() -> None:
                    nonlocal next_seq, committed, rejected, errors, timeouts
                    while True:
                        item = ready.get(next_seq)
                        if item is None:
                            return
                        del ready[next_seq]
                        eq, sender_pid, receiver_pid, status, err_code, err_details, avg_route_len, route_edges, edge_patch = item

                        edges_pairs = route_edges or [(sender_pid, receiver_pid)]

                        if err_code is not None:
                            errors += 1
                            _inc(eq, "errors")
                            for a, b in edges_pairs:
                                _edge_inc(eq, a, b, "attempts")
                                _edge_inc(eq, a, b, "errors")
                            if err_code == "PAYMENT_TIMEOUT":
                                timeouts += 1
                                _inc(eq, "timeouts")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "timeouts")
                            if err_code == "PAYMENT_REJECTED":
                                _inc(eq, "rejected")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "rejected")

                            with self._lock:
                                run.errors_total += 1
                                run._error_timestamps.append(time.time())
                                cutoff = time.time() - 60.0
                                while run._error_timestamps and run._error_timestamps[0] < cutoff:
                                    run._error_timestamps.popleft()
                                run.last_error = {
                                    "code": err_code,
                                    "message": str((err_details or {}).get("message") or err_code),
                                    "at": _utc_now().isoformat(),
                                }
                                run.last_event_type = "tx.failed"

                            failed_evt = SimulatorTxFailedEvent(
                                event_id=self._sse.next_event_id(run),
                                ts=_utc_now(),
                                type="tx.failed",
                                equivalent=eq,
                                from_=sender_pid,
                                to=receiver_pid,
                                error={
                                    "code": err_code,
                                    "message": str((err_details or {}).get("message") or err_code),
                                    "at": _utc_now(),
                                    "details": err_details,
                                },
                            ).model_dump(mode="json", by_alias=True)
                            self._sse.broadcast(run_id, failed_evt)
                        else:
                            for a, b in edges_pairs:
                                _edge_inc(eq, a, b, "attempts")
                            if status == "COMMITTED":
                                committed += 1
                                _inc(eq, "committed")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "committed")
                                if float(avg_route_len) > 0:
                                    _route_add(eq, float(avg_route_len))

                                evt_dict = SimulatorTxUpdatedEvent(
                                    event_id=self._sse.next_event_id(run),
                                    ts=_utc_now(),
                                    type="tx.updated",
                                    equivalent=eq,
                                    ttl_ms=1200,
                                    edges=[{"from": a, "to": b} for a, b in edges_pairs],
                                    node_badges=None,
                                ).model_dump(mode="json")
                                if edge_patch:
                                    evt_dict["edge_patch"] = edge_patch
                                with self._lock:
                                    run.last_event_type = "tx.updated"
                                self._sse.broadcast(run_id, evt_dict)
                            else:
                                rejected += 1
                                _inc(eq, "rejected")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "rejected")

                                try:
                                    rejection_code = _map_rejection_code(err_details)
                                except Exception:
                                    rejection_code = "PAYMENT_REJECTED"

                                with self._lock:
                                    # Track last_error for diagnostics even on clean rejections.
                                    run.last_error = {
                                        "code": rejection_code,
                                        "message": rejection_code,
                                        "at": _utc_now().isoformat(),
                                    }
                                    run.last_event_type = "tx.failed"

                                failed_evt = SimulatorTxFailedEvent(
                                    event_id=self._sse.next_event_id(run),
                                    ts=_utc_now(),
                                    type="tx.failed",
                                    equivalent=eq,
                                    from_=sender_pid,
                                    to=receiver_pid,
                                    error={
                                        "code": rejection_code,
                                        "message": rejection_code,
                                        "at": _utc_now(),
                                        "details": err_details,
                                    },
                                ).model_dump(mode="json", by_alias=True)
                                self._sse.broadcast(run_id, failed_evt)

                        with self._lock:
                            run.queue_depth = max(0, run.queue_depth - 1)

                        next_seq += 1

                try:
                    if tasks:
                        for t in asyncio.as_completed(tasks):
                            seq, eq, sender_pid, receiver_pid, status, err_code, err_details, avg_route_len, route_edges = await t

                            if run.state != "running":
                                break

                            # Build edge_patch for committed transactions (Variant A: SSE patches)
                            edge_patch_list: list[dict[str, Any]] | None = None
                            if status == "COMMITTED":
                                edges_pairs = route_edges or [(sender_pid, receiver_pid)]

                                edge_patch_list = []
                                try:
                                    async with db_session.AsyncSessionLocal() as patch_session:
                                        eq_id = await patch_session.scalar(select(Equivalent.id).where(Equivalent.code == eq))
                                        if eq_id is None:
                                            raise ValueError(f"Equivalent {eq} not found")

                                        # Fetch participants in one query.
                                        pids = sorted({pid for ab in edges_pairs for pid in ab if pid})
                                        res = await patch_session.execute(select(Participant).where(Participant.pid.in_(pids)))
                                        pid_to_participant = {p.pid: p for p in res.scalars().all()}

                                        for src_pid, dst_pid in edges_pairs:
                                            src_part = pid_to_participant.get(src_pid)
                                            dst_part = pid_to_participant.get(dst_pid)
                                            if not src_part or not dst_part:
                                                continue

                                            debt = await patch_session.scalar(
                                                select(Debt).where(
                                                    Debt.creditor_id == src_part.id,
                                                    Debt.debtor_id == dst_part.id,
                                                    Debt.equivalent_id == eq_id,
                                                )
                                            )
                                            tl = await patch_session.scalar(
                                                select(TrustLine).where(
                                                    TrustLine.from_participant_id == src_part.id,
                                                    TrustLine.to_participant_id == dst_part.id,
                                                    TrustLine.equivalent_id == eq_id,
                                                )
                                            )

                                            used_amt = debt.amount if debt else Decimal("0")
                                            limit_amt = tl.limit if tl else Decimal("0")
                                            available_amt = max(Decimal("0"), limit_amt - used_amt)

                                            edge_patch_list.append(
                                                {
                                                    "source": src_pid,
                                                    "target": dst_pid,
                                                    "used": str(used_amt),
                                                    "available": str(available_amt),
                                                    # NOTE: viz_* are NOT included — snapshot refetch handles them
                                                }
                                            )
                                except Exception as e_patch:
                                    logger.warning(f"Failed to build edge_patch: {e_patch}")
                                    edge_patch_list = None

                                if edge_patch_list == []:
                                    edge_patch_list = None

                            ready[seq] = (
                                eq,
                                sender_pid,
                                receiver_pid,
                                status,
                                err_code,
                                err_details,
                                avg_route_len,
                                route_edges,
                                edge_patch_list,
                            )
                            _emit_if_ready()

                            if max_timeouts_per_tick > 0 and timeouts >= max_timeouts_per_tick:
                                await self._fail_run(
                                    run_id,
                                    code="REAL_MODE_TOO_MANY_TIMEOUTS",
                                    message=f"Too many payment timeouts in one tick: {timeouts}",
                                )
                                break
                finally:
                    # Cancel any remaining tasks if we bailed early.
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Emit anything that completed but wasn't emitted yet.
                _emit_if_ready()

                with self._lock:
                    run._real_in_flight = 0
                    run.queue_depth = 0
                    run.current_phase = None
                    run._real_consec_tick_failures = 0

                    if max_errors_total > 0 and run.errors_total >= max_errors_total:
                        # Mark error; heartbeat task will be cancelled in _fail_run.
                        pass

                if max_errors_total > 0 and run.errors_total >= max_errors_total:
                    await self._fail_run(
                        run_id,
                        code="REAL_MODE_TOO_MANY_ERRORS",
                        message=f"Too many total errors: {run.errors_total}",
                    )
                    return

                # Best-effort clearing (optional MVP): once in a while, attempt clearing per equivalent.
                clearing_volume_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
                if run.tick_index % CLEARING_EVERY_N_TICKS == 0 and bool(getattr(settings, "CLEARING_ENABLED", True)):
                    clearing_volume_by_eq = await self._tick_real_mode_clearing(session, run_id, run, equivalents)

                # Real total debt snapshot (sum of all debts for the equivalent).
                total_debt_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
                try:
                    eq_rows = (
                        await session.execute(select(Equivalent.id, Equivalent.code).where(Equivalent.code.in_(list(equivalents))))
                    ).all()
                    eq_id_by_code = {str(code): eq_id for (eq_id, code) in eq_rows}
                    for eq_code, eq_id in eq_id_by_code.items():
                        total = (
                            await session.execute(select(func.coalesce(func.sum(Debt.amount), 0)).where(Debt.equivalent_id == eq_id))
                        ).scalar_one()
                        total_debt_by_eq[str(eq_code)] = float(total)
                except Exception:
                    pass

                # Avg route length for this tick (successful payments).
                for eq in equivalents:
                    r = per_eq_route.get(str(eq), {})
                    n = float(r.get("route_len_n", 0.0) or 0.0)
                    s = float(r.get("route_len_sum", 0.0) or 0.0)
                    per_eq_metric_values[str(eq)]["avg_route_length"] = float(s / n) if n > 0 else 0.0
                    per_eq_metric_values[str(eq)]["total_debt"] = float(total_debt_by_eq.get(str(eq), 0.0) or 0.0)
                    per_eq_metric_values[str(eq)]["clearing_volume"] = float(clearing_volume_by_eq.get(str(eq), 0.0) or 0.0)

                await simulator_storage.write_tick_metrics(
                    run_id=run.run_id,
                    t_ms=int(run.sim_time_ms),
                    per_equivalent=per_eq,
                    metric_values_by_eq=per_eq_metric_values,
                    session=session,
                )

                # Persist bottlenecks snapshot derived from actual tick outcomes.
                if self._db_enabled():
                    computed_at = _utc_now()
                    for eq in equivalents:
                        await simulator_storage.write_tick_bottlenecks(
                            run_id=run.run_id,
                            equivalent=str(eq),
                            computed_at=computed_at,
                            edge_stats=per_eq_edge_stats.get(str(eq), {}),
                            session=session,
                            limit=50,
                        )

                self._artifacts.write_real_tick_artifact(
                    run,
                    {
                        "tick_index": run.tick_index,
                        "sim_time_ms": run.sim_time_ms,
                        "budget": len(planned),
                        "committed": committed,
                        "rejected": rejected,
                        "errors": errors,
                        "timeouts": timeouts,
                    },
                )
                await simulator_storage.sync_artifacts(run)
        except Exception as e:
            with self._lock:
                run.errors_total += 1
                run._error_timestamps.append(time.time())
                cutoff = time.time() - 60.0
                while run._error_timestamps and run._error_timestamps[0] < cutoff:
                    run._error_timestamps.popleft()
                run._real_consec_tick_failures += 1
                run.last_error = {
                    "code": "REAL_MODE_TICK_FAILED",
                    "message": str(e),
                    "at": _utc_now().isoformat(),
                }

            max_consec = int(
                os.getenv(
                    "SIMULATOR_REAL_MAX_CONSEC_TICK_FAILURES",
                    str(REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT),
                )
            )
            if max_consec > 0 and run._real_consec_tick_failures >= max_consec:
                await self._fail_run(
                    run_id,
                    code="REAL_MODE_TICK_FAILED_REPEATED",
                    message=f"Real-mode tick failed {run._real_consec_tick_failures} times in a row",
                )

    async def _fail_run(self, run_id: str, *, code: str, message: str) -> None:
        run = self.get_run(run_id)
        task = None
        with self._lock:
            if run.state in ("stopped", "stopping", "error"):
                return

            run.state = "error"
            run.stopped_at = _utc_now()
            run.current_phase = None
            run.queue_depth = 0
            run._real_in_flight = 0
            run.errors_total += 1
            run._error_timestamps.append(time.time())
            cutoff = time.time() - 60.0
            while run._error_timestamps and run._error_timestamps[0] < cutoff:
                run._error_timestamps.popleft()
            run.last_error = {"code": code, "message": message, "at": _utc_now().isoformat()}
            task = run._heartbeat_task

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        if task is not None:
            task.cancel()

    async def _tick_real_mode_clearing(
        self,
        session,
        run_id: str,
        run: RunRecord,
        equivalents: list[str],
    ) -> dict[str, float]:
        service = ClearingService(session)
        try:
            max_depth = int(os.getenv("SIMULATOR_CLEARING_MAX_DEPTH", "6"))
        except Exception:
            max_depth = 6
        cleared_amount_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
        for eq in equivalents:
            try:
                # Plan step: find at least one cycle to visualize.
                cycles = await service.find_cycles(eq, max_depth=max_depth)
                if not cycles:
                    continue

                plan_id = f"plan_{secrets.token_hex(6)}"
                plan_evt = SimulatorClearingPlanEvent(
                    event_id=self._sse.next_event_id(run),
                    ts=_utc_now(),
                    type="clearing.plan",
                    equivalent=eq,
                    plan_id=plan_id,
                    steps=[
                        {
                            "at_ms": 0,
                            "intensity_key": "mid",
                            "flash": {"kind": "info", "title": "Clearing", "detail": "Auto clearing"},
                        }
                    ],
                ).model_dump(mode="json")

                with self._lock:
                    run.last_event_type = "clearing.plan"
                    run.current_phase = "clearing"
                self._sse.broadcast(run_id, plan_evt)

                # Execute with stats (volume = sum of cleared amounts).
                cleared_cycles = 0
                cleared_amount = 0.0
                while True:
                    cycles = await service.find_cycles(eq, max_depth=max_depth)
                    if not cycles:
                        break

                    executed = False
                    for cycle in cycles:
                        # Clearing amount is min edge amount in cycle.
                        try:
                            amts: list[float] = []
                            for edge in cycle:
                                if isinstance(edge, dict):
                                    amts.append(float(edge.get("amount")))
                                else:
                                    amts.append(float(getattr(edge, "amount")))
                            clear_amount = float(min(amts)) if amts else 0.0
                        except Exception:
                            clear_amount = 0.0

                        success = await service.execute_clearing(cycle)
                        if success:
                            cleared_cycles += 1
                            cleared_amount += float(max(0.0, clear_amount))
                            executed = True
                            break

                    if not executed:
                        break
                    if cleared_cycles > 100:
                        break

                await session.commit()
                cleared_amount_by_eq[str(eq)] = float(cleared_amount)

                done_evt = SimulatorClearingDoneEvent(
                    event_id=self._sse.next_event_id(run),
                    ts=_utc_now(),
                    type="clearing.done",
                    equivalent=eq,
                ).model_dump(mode="json")
                with self._lock:
                    run.last_event_type = "clearing.done"
                    run.current_phase = None
                self._sse.broadcast(run_id, done_evt)
            except Exception as e:
                with self._lock:
                    run.errors_total += 1
                    run._error_timestamps.append(time.time())
                    cutoff = time.time() - 60.0
                    while run._error_timestamps and run._error_timestamps[0] < cutoff:
                        run._error_timestamps.popleft()
                    run.last_error = {
                        "code": "CLEARING_ERROR",
                        "message": str(e),
                        "at": _utc_now().isoformat(),
                    }
                continue

        return cleared_amount_by_eq

    def _real_candidates_from_scenario(self, scenario: dict[str, Any]) -> list[dict[str, Any]]:
        tls = scenario.get("trustlines") or []
        out: list[dict[str, Any]] = []
        for tl in tls:
            status = str(tl.get("status") or "active").strip().lower()
            if status != "active":
                continue

            eq = str(tl.get("equivalent") or "").strip()
            frm = str(tl.get("from") or "").strip()
            to = str(tl.get("to") or "").strip()
            if not eq or not frm or not to:
                continue

            try:
                limit = Decimal(str(tl.get("limit")))
            except Exception:
                continue
            if limit <= 0:
                continue

            # TrustLine direction is creditor->debtor. Payment from debtor->creditor.
            # TODO: Consider pre-filtering by DB-derived available capacity (taking current "used" into account)
            # to reduce rejected payment attempts under high load.
            out.append({"equivalent": eq, "sender_pid": to, "receiver_pid": frm, "limit": limit})

        out.sort(key=lambda x: (x["equivalent"], x["receiver_pid"], x["sender_pid"]))
        return out


@dataclass(frozen=True)
class _RealPaymentAction:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str


class SimulatorRuntime(_SimulatorRuntimeBase):

    def _db_enabled(self) -> bool:
        return simulator_storage.db_enabled()

    def _plan_real_payments(self, run: RunRecord, scenario: dict[str, Any]) -> list[_RealPaymentAction]:
        """Deterministic planner for Real Mode payment actions.

        Important property for SB-NF-04:
        - planning for a given (seed, tick_index, scenario) is deterministic.
        - changing intensity only changes *how many* actions we take from the same
          per-tick ordering (prefix-stable), so it doesn't affect later ticks.
        """

        intensity = max(0.0, min(1.0, float(run.intensity_percent) / 100.0))
        budget = int(ACTIONS_PER_TICK_MAX * intensity)
        if budget <= 0:
            return []

        candidates = self._real_candidates_from_scenario(scenario)
        if not candidates:
            return []

        tick_seed = (int(run.seed) * 1_000_003 + int(run.tick_index)) & 0xFFFFFFFF
        tick_rng = random.Random(tick_seed)

        order = list(candidates)
        tick_rng.shuffle(order)

        planned: list[_RealPaymentAction] = []
        for i in range(budget):
            c = order[i % len(order)]
            limit = c["limit"]

            action_seed = (tick_seed * 1_000_003 + i) & 0xFFFFFFFF
            action_rng = random.Random(action_seed)
            amount = self._real_pick_amount(action_rng, limit)
            if amount is None:
                continue

            planned.append(
                _RealPaymentAction(
                    seq=i,
                    equivalent=c["equivalent"],
                    sender_pid=c["sender_pid"],
                    receiver_pid=c["receiver_pid"],
                    amount=amount,
                )
            )

        return planned

    def _real_pick_amount(self, rng: random.Random, limit: Decimal) -> str | None:
        # Keep it small and <= limit.
        cap = min(limit, Decimal("3"))
        if cap <= 0:
            return None

        raw = Decimal(str(0.1 + rng.random() * float(cap)))
        amt = min(raw, cap).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if amt <= 0:
            return None
        return format(amt, "f")

    def _sim_idempotency_key(
        self,
        *,
        run_id: str,
        tick_ms: int,
        sender_pid: str,
        receiver_pid: str,
        equivalent: str,
        amount: str,
        seq: int,
    ) -> str:
        material = f"{run_id}|{tick_ms}|{sender_pid}|{receiver_pid}|{equivalent}|{amount}|{seq}"
        return "sim:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    async def _load_real_participants(
        self, session, scenario: dict[str, Any]
    ) -> list[tuple[uuid.UUID, str]]:
        pids = [str(p.get("id") or "").strip() for p in (scenario.get("participants") or [])]
        pids = [p for p in pids if p]
        if not pids:
            return []

        rows = (
            await session.execute(select(Participant).where(Participant.pid.in_(pids)))
        ).scalars().all()
        by_pid = {p.pid: p for p in rows}
        out: list[tuple[uuid.UUID, str]] = []
        for pid in sorted(pids):
            rec = by_pid.get(pid)
            if rec is None:
                continue
            out.append((rec.id, rec.pid))
        return out

    async def _seed_scenario_into_db(self, session, scenario: dict[str, Any]) -> None:
        # Equivalents
        eq_codes = [str(x).strip().upper() for x in (scenario.get("equivalents") or [])]
        eq_codes = [c for c in eq_codes if c]

        if eq_codes:
            existing_eq = (
                await session.execute(select(Equivalent).where(Equivalent.code.in_(eq_codes)))
            ).scalars().all()
            have = {e.code for e in existing_eq}
            for code in eq_codes:
                if code in have:
                    continue
                session.add(Equivalent(code=code, is_active=True, metadata_={} ))

        # Participants
        participants = scenario.get("participants") or []
        pids = [str(p.get("id") or "").strip() for p in participants]
        pids = [p for p in pids if p]
        if pids:
            existing_p = (
                await session.execute(select(Participant).where(Participant.pid.in_(pids)))
            ).scalars().all()
            have_p = {p.pid for p in existing_p}
            for p in participants:
                pid = str(p.get("id") or "").strip()
                if not pid or pid in have_p:
                    continue
                name = str(p.get("name") or pid)
                p_type = str(p.get("type") or "person").strip() or "person"
                status = str(p.get("status") or "active").strip().lower()
                if status == "frozen":
                    status = "suspended"
                elif status == "banned":
                    status = "deleted"
                elif status not in {"active", "suspended", "left", "deleted"}:
                    status = "active"
                public_key = hashlib.sha256(pid.encode("utf-8")).hexdigest()
                session.add(
                    Participant(
                        pid=pid,
                        display_name=name,
                        public_key=public_key,
                        type=p_type if p_type in {"person", "business", "hub"} else "person",
                        status=status,
                        profile={},
                    )
                )

        # NOTE: app.db.session.AsyncSessionLocal has autoflush=False.
        # We must flush pending inserts before querying IDs for trustlines.
        await session.flush()

        # Trustlines
        trustlines = scenario.get("trustlines") or []
        if trustlines and eq_codes and pids:
            default_policy = {
                "auto_clearing": True,
                "can_be_intermediate": True,
                "max_hop_usage": None,
                "daily_limit": None,
                "blocked_participants": [],
            }
            # Load ids
            eq_rows = (
                await session.execute(select(Equivalent).where(Equivalent.code.in_(eq_codes)))
            ).scalars().all()
            eq_by_code = {e.code: e for e in eq_rows}

            p_rows = (
                await session.execute(select(Participant).where(Participant.pid.in_(pids)))
            ).scalars().all()
            p_by_pid = {p.pid: p for p in p_rows}

            for tl in trustlines:
                eq = str(tl.get("equivalent") or "").strip().upper()
                if not eq or eq not in eq_by_code:
                    continue
                from_pid = str(tl.get("from") or "").strip()
                to_pid = str(tl.get("to") or "").strip()
                if not from_pid or not to_pid:
                    continue
                p_from = p_by_pid.get(from_pid)
                p_to = p_by_pid.get(to_pid)
                if p_from is None or p_to is None:
                    continue

                raw_limit = tl.get("limit")
                try:
                    limit = Decimal(str(raw_limit))
                except (InvalidOperation, ValueError):
                    continue
                if limit < 0:
                    continue

                existing = (
                    await session.execute(
                        select(TrustLine).where(
                            TrustLine.from_participant_id == p_from.id,
                            TrustLine.to_participant_id == p_to.id,
                            TrustLine.equivalent_id == eq_by_code[eq].id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                status = str(tl.get("status") or "active").strip().lower()
                if status not in {"active", "frozen", "closed"}:
                    status = "active"

                policy = tl.get("policy")
                if not isinstance(policy, dict):
                    policy = default_policy

                session.add(
                    TrustLine(
                        from_participant_id=p_from.id,
                        to_participant_id=p_to.id,
                        equivalent_id=eq_by_code[eq].id,
                        limit=limit,
                        status=status,
                        policy=policy,
                    )
                )

    def _tick_fixtures_events(self, run_id: str) -> None:
        run = self.get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return

        # Clearing lifecycle
        if run._clearing_pending_done_at_ms is not None and run.sim_time_ms >= run._clearing_pending_done_at_ms:
            # Emit clearing.done for all equivalents (best-effort). UI can ignore if not subscribed.
            for eq in list(run._edges_by_equivalent.keys()):
                evt = SimulatorClearingDoneEvent(
                    event_id=self._sse.next_event_id(run),
                    ts=_utc_now(),
                    type="clearing.done",
                    equivalent=eq,
                ).model_dump(mode="json")
                run.last_event_type = "clearing.done"
                run.current_phase = None
                self._sse.broadcast(run_id, evt)

            run._clearing_pending_done_at_ms = None
            run._next_clearing_at_ms = run.sim_time_ms + 45_000
            return

        if run._clearing_pending_done_at_ms is None and run.sim_time_ms >= run._next_clearing_at_ms:
            # Emit clearing.plan for all equivalents.
            for eq in list(run._edges_by_equivalent.keys()):
                plan = self._make_clearing_plan(run_id=run_id, equivalent=eq)
                if plan is None:
                    continue
                run.last_event_type = "clearing.plan"
                run.current_phase = "clearing"
                self._sse.broadcast(run_id, plan)

            run._clearing_pending_done_at_ms = run.sim_time_ms + 2_000
            return

        # tx.updated cadence (based on intensity)
        if run.sim_time_ms < run._next_tx_at_ms:
            return

        # Higher intensity -> more frequent tx events.
        base_interval_ms = 2_000
        scale = max(0.25, 1.0 - (run.intensity_percent / 100.0) * 0.75)
        jitter = int(run._rng.randint(0, 600))
        run._next_tx_at_ms = run.sim_time_ms + int(base_interval_ms * scale) + jitter

        # Pick an equivalent that likely has edges.
        candidates = [eq for eq, edges in run._edges_by_equivalent.items() if edges]
        if not candidates:
            return
        eq = run._rng.choice(candidates)
        evt = self._maybe_make_tx_updated(run_id=run_id, equivalent=eq)
        if evt is None:
            return
        run.last_event_type = "tx.updated"
        self._sse.broadcast(run_id, evt)

    def _maybe_make_tx_updated(self, *, run_id: str, equivalent: str) -> Optional[dict[str, Any]]:
        run = self.get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return None
        edges = (run._edges_by_equivalent or {}).get(equivalent) or []
        if not edges:
            return None

        (src, dst) = run._rng.choice(edges)
        evt = SimulatorTxUpdatedEvent(
            event_id=self._sse.next_event_id(run),
            ts=_utc_now(),
            type="tx.updated",
            equivalent=equivalent,
            ttl_ms=1200,
            intensity_key="mid" if run.intensity_percent < 70 else "hi",
            edges=[
                {
                    "from": src,
                    "to": dst,
                    "style": {"viz_width_key": "highlight", "viz_alpha_key": "hi"},
                }
            ],
            node_badges=[
                {"id": src, "viz_badge_key": "tx"},
                {"id": dst, "viz_badge_key": "tx"},
            ],
        ).model_dump(mode="json")
        return evt

    def _make_clearing_plan(self, *, run_id: str, equivalent: str) -> Optional[dict[str, Any]]:
        run = self.get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return None
        edges = (run._edges_by_equivalent or {}).get(equivalent) or []
        if not edges:
            return None

        (e1_from, e1_to) = run._rng.choice(edges)
        (e2_from, e2_to) = run._rng.choice(edges)
        plan_id = f"clr_{run.run_id}_{run._event_seq + 1:06d}"
        evt = SimulatorClearingPlanEvent(
            event_id=self._sse.next_event_id(run),
            ts=_utc_now(),
            type="clearing.plan",
            equivalent=equivalent,
            plan_id=plan_id,
            steps=[
                {"at_ms": 0, "highlight_edges": [{"from": e1_from, "to": e1_to}], "intensity_key": "hi"},
                {"at_ms": 180, "particles_edges": [{"from": e2_from, "to": e2_to}], "intensity_key": "mid"},
                {"at_ms": 420, "flash": {"kind": "clearing"}},
            ],
        ).model_dump(mode="json")
        return evt


def _new_run_id() -> str:
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    return f"run_{ts}_{secrets.token_hex(4)}"


def _edges_by_equivalent(raw: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    trustlines = raw.get("trustlines") or []
    out: dict[str, list[tuple[str, str]]] = {}
    for tl in trustlines:
        eq = str(tl.get("equivalent") or "").strip()
        if not eq:
            continue
        src = str(tl.get("from") or "").strip()
        dst = str(tl.get("to") or "").strip()
        if not src or not dst:
            continue
        out.setdefault(eq, []).append((src, dst))
    return out


def _run_to_status(run: RunRecord) -> RunStatus:
    cutoff = time.time() - 60.0
    # Best-effort: timestamps are pruned on write; we only count here.
    errors_last_1m = sum(1 for ts in run._error_timestamps if ts >= cutoff)
    return RunStatus(
        api_version=SIMULATOR_API_VERSION,
        run_id=run.run_id,
        scenario_id=run.scenario_id,
        mode=run.mode,
        state=run.state,
        started_at=run.started_at,
        stopped_at=run.stopped_at,
        sim_time_ms=run.sim_time_ms,
        intensity_percent=run.intensity_percent,
        ops_sec=run.ops_sec,
        queue_depth=run.queue_depth,
        errors_total=run.errors_total,
        errors_last_1m=int(errors_last_1m),
        last_error=_dict_to_last_error(run.last_error),
        last_event_type=run.last_event_type,
        current_phase=run.current_phase,
    )


def _dict_to_last_error(raw: Optional[dict[str, Any]]):
    if not raw:
        return None
    # Expecting {code,message,at}
    if "at" not in raw:
        raw = dict(raw)
        raw["at"] = _utc_now()
    return raw


runtime = SimulatorRuntime()
