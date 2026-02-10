from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Optional
from pathlib import Path
import secrets
import hashlib
import random

from app.schemas.simulator import (
    BottlenecksResponse,
    MetricsResponse,
    RunMode,
    RunStatus,
    ScenarioSummary,
    SimulatorGraphSnapshot,
    SimulatorRunStatusEvent,
    SimulatorTxUpdatedEvent,
    SimulatorClearingPlanEvent,
    SimulatorClearingDoneEvent,
)
from app.core.simulator.artifacts import ArtifactsManager
from app.core.simulator.fixtures_runner import FixturesRunner
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.core.simulator.models import RunRecord, ScenarioRecord, _Subscription
from app.core.simulator.real_runner import RealRunner
from app.core.simulator.run_lifecycle import RunLifecycle
from app.core.simulator.runtime_utils import (
    ACTIONS_PER_TICK_MAX,
    CLEARING_EVERY_N_TICKS,
    FIXTURES_DIR,
    REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT,
    REAL_MAX_ERRORS_TOTAL_DEFAULT,
    REAL_MAX_IN_FLIGHT_DEFAULT,
    REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT,
    SCENARIO_SCHEMA_PATH,
    TICK_MS_BASE,
    dict_to_last_error as _dict_to_last_error,
    edges_by_equivalent as _edges_by_equivalent,
    local_state_dir as _local_state_dir,
    new_run_id as _new_run_id,
    run_to_status as _run_to_status,
    safe_int_env as _safe_int_env,
    utc_now as _utc_now,
)
from app.core.simulator.scenario_registry import ScenarioRegistry
from app.core.simulator.snapshot_builder import SnapshotBuilder, scenario_to_snapshot
import app.core.simulator.storage as simulator_storage
from app.core.simulator.sse_broadcast import SseBroadcast
from app.utils.exceptions import NotFoundException
from app.utils.exceptions import ConflictException

logger = logging.getLogger(__name__)


def _scenario_allowlist() -> Optional[set[str]]:
    """Allowlist for preset scenarios shown to the UI.

    - If SIMULATOR_SCENARIO_ALLOWLIST is unset: show only the canonical demo presets.
    - If set to '*' or 'all': disable filtering.
    - If set to comma-separated ids: show only those.
    """

    raw = str(os.environ.get("SIMULATOR_SCENARIO_ALLOWLIST", "")).strip()
    if raw:
        if raw in {"*", "all", "ALL"}:
            return None
        items = {x.strip() for x in raw.split(",") if x.strip()}
        return items or None

    return {
        "greenfield-village-100-realistic-v2",
        "riverside-town-50-realistic-v2",
        "clearing-demo-10",
    }


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

        self._is_shutting_down = False

        # Runtime knobs (env-configurable; defaults preserve existing behavior).
        self._tick_ms_base = _safe_int_env("SIMULATOR_TICK_MS_BASE", TICK_MS_BASE)
        self._actions_per_tick_max = _safe_int_env(
            "SIMULATOR_ACTIONS_PER_TICK_MAX", ACTIONS_PER_TICK_MAX
        )
        self._clearing_every_n_ticks = _safe_int_env(
            "SIMULATOR_CLEARING_EVERY_N_TICKS", CLEARING_EVERY_N_TICKS
        )
        self._max_active_runs = _safe_int_env("SIMULATOR_MAX_ACTIVE_RUNS", 1)
        self._max_run_records = _safe_int_env("SIMULATOR_MAX_RUN_RECORDS", 200)

        # DB persistence throttling for run status row.
        # Defaults reduce write amplification in heartbeat loop.
        self._run_persist_every_ms = _safe_int_env(
            "SIMULATOR_RUN_PERSIST_EVERY_MS", 5000
        )
        self._run_persist_dirty_every_ms = _safe_int_env(
            "SIMULATOR_RUN_PERSIST_DIRTY_EVERY_MS", 1000
        )

        # Local artifact retention (0 disables).
        self._artifacts_ttl_hours = _safe_int_env("SIMULATOR_ARTIFACTS_TTL_HOURS", 0)

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
            logger=logger,
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

        # Best-effort cleanup of old local artifacts (keeps dev dirs from growing forever).
        try:
            self._artifacts.cleanup_old_runs(ttl_hours=self._artifacts_ttl_hours)
        except Exception:
            logger.exception("simulator.artifacts.cleanup_failed")

        # SSE replay buffer sizing/TTL. Best-effort; does not change OpenAPI.
        self._event_buffer_max = _safe_int_env("SIMULATOR_EVENT_BUFFER_SIZE", 2000)
        self._event_buffer_ttl_sec = _safe_int_env(
            "SIMULATOR_EVENT_BUFFER_TTL_SEC", 600
        )
        self._sse_sub_queue_max = _safe_int_env("SIMULATOR_SSE_SUB_QUEUE_MAX", 500)

        # If enabled, SSE endpoints may return 410 when Last-Event-ID is too old
        # to be replayed from the in-memory ring buffer.
        self._sse_strict_replay = bool(_safe_int_env("SIMULATOR_SSE_STRICT_REPLAY", 0))

        self._sse = SseBroadcast(
            lock=self._lock,
            runs=self._runs,
            get_event_buffer_max=lambda: int(self._event_buffer_max),
            get_event_buffer_ttl_sec=lambda: int(self._event_buffer_ttl_sec),
            get_sub_queue_max=lambda: int(self._sse_sub_queue_max),
            enqueue_event_artifact=self._artifacts.enqueue_event_artifact,
            logger=logger,
        )

        self._run_lifecycle = RunLifecycle(
            lock=self._lock,
            runs=self._runs,
            set_active_run_id=lambda run_id: setattr(self, "_active_run_id", run_id),
            utc_now=_utc_now,
            new_run_id=_new_run_id,
            get_scenario_raw=lambda scenario_id: self.get_scenario(scenario_id).raw,
            edges_by_equivalent=_edges_by_equivalent,
            artifacts=self._artifacts,
            sse=self._sse,
            heartbeat_loop=self._heartbeat_loop,
            publish_run_status=self.publish_run_status,
            run_to_status=_run_to_status,
            get_run_status_payload_json=lambda run_id: self.get_run_status(
                run_id
            ).model_dump(mode="json", by_alias=True),
            real_max_in_flight_default=REAL_MAX_IN_FLIGHT_DEFAULT,
            get_max_active_runs=lambda: int(self._max_active_runs),
            get_max_run_records=lambda: int(self._max_run_records),
            logger=logger,
        )

        self._fixtures_runner = FixturesRunner(
            lock=self._lock,
            get_run=self.get_run,
            sse=self._sse,
            utc_now=_utc_now,
        )

        self._real_runner = RealRunner(
            lock=self._lock,
            get_run=self.get_run,
            get_scenario_raw=lambda scenario_id: self.get_scenario(scenario_id).raw,
            sse=self._sse,
            artifacts=self._artifacts,
            utc_now=_utc_now,
            publish_run_status=self.publish_run_status,
            db_enabled=simulator_storage.db_enabled,
            actions_per_tick_max=self._actions_per_tick_max,
            clearing_every_n_ticks=self._clearing_every_n_ticks,
            real_max_consec_tick_failures_default=REAL_MAX_CONSEC_TICK_FAILURES_DEFAULT,
            real_max_timeouts_per_tick_default=REAL_MAX_TIMEOUTS_PER_TICK_DEFAULT,
            real_max_errors_total_default=REAL_MAX_ERRORS_TOTAL_DEFAULT,
            logger=logger,
        )

    async def shutdown(self) -> None:
        """Best-effort graceful shutdown.

        Cancels heartbeat tasks, stops artifact writers, and clears subscriptions.
        """

        run_ids: list[str]
        with self._lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True
            run_ids = list(self._runs.keys())

        # Stop runs sequentially to avoid amplifying load on shutdown.
        for run_id in run_ids:
            try:
                await self.stop(run_id)
            except Exception:
                logger.exception(
                    "simulator.runtime.shutdown_stop_failed run_id=%s", run_id
                )

        # Ensure subscriptions are cleared even if individual streams didn't unsubscribe.
        with self._lock:
            for run in self._runs.values():
                try:
                    run._subs.clear()
                except Exception:
                    pass

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
        allow = _scenario_allowlist()
        with self._lock:
            items = sorted(self._scenarios.values(), key=lambda s: s.scenario_id)
        if allow is not None:
            items = [s for s in items if s.scenario_id in allow]
        return [s.summary() for s in items]

    def get_scenario(self, scenario_id: str) -> ScenarioRecord:
        with self._lock:
            rec = self._scenarios.get(scenario_id)
        if rec is None:
            raise NotFoundException(f"Scenario {scenario_id} not found")
        return rec

    def save_uploaded_scenario(self, scenario: dict[str, Any]) -> ScenarioRecord:
        return self._scenario_registry.save_uploaded_scenario(scenario)

    async def build_scenario_preview(
        self,
        *,
        scenario_id: str,
        equivalent: str,
        mode: RunMode,
        session=None,
    ) -> SimulatorGraphSnapshot:
        """Build a graph preview without starting a run.

        - mode=fixtures: topology-only preview (no DB enrichment)
        - mode=real: best-effort DB enrichment (balances/debts -> viz_*)
        """

        rec = self.get_scenario(scenario_id)
        snap = scenario_to_snapshot(rec.raw, equivalent=equivalent, utc_now=_utc_now)
        if mode != "real":
            return snap
        return await self._snapshot_builder.enrich_snapshot_from_db_if_enabled(
            snap, equivalent=equivalent, session=session
        )

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

    async def create_run(
        self, *, scenario_id: str, mode: RunMode, intensity_percent: int
    ) -> str:
        with self._lock:
            if self._is_shutting_down:
                raise ConflictException("Simulator runtime is shutting down")
        return await self._run_lifecycle.create_run(
            scenario_id=scenario_id,
            mode=mode,
            intensity_percent=intensity_percent,
        )

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

    async def list_artifacts(self, *, run_id: str):
        return await self._artifacts.list_artifacts(run_id=run_id)

    def get_artifact_path(self, *, run_id: str, name: str) -> Path:
        return self._artifacts.get_artifact_path(run_id=run_id, name=name)

    def publish_run_status(self, run_id: str) -> None:
        run = self.get_run(run_id)
        stall_ticks = int(run._real_consec_all_rejected_ticks or 0)
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
            attempts_total=run.attempts_total,
            committed_total=run.committed_total,
            rejected_total=run.rejected_total,
            errors_total=run.errors_total,
            timeouts_total=run.timeouts_total,
            last_event_type=run.last_event_type,
            current_phase=run.current_phase,
            last_error=_dict_to_last_error(run.last_error),
            consec_all_rejected_ticks=(stall_ticks if stall_ticks > 0 else None),
        ).model_dump(mode="json", by_alias=True)

        self._sse.broadcast(run_id, payload)

    def _ensure_run_accepts_actions(self, run_id: str) -> RunRecord:
        run = self.get_run(run_id)
        state = str(run.state or "")
        if state in {"stopped", "error"}:
            raise ConflictException(
                "Run does not accept actions in terminal state",
                details={"run_id": run_id, "state": state},
            )
        return run

    def emit_debug_tx_once(
        self,
        *,
        run_id: str,
        equivalent: str,
        from_: str | None,
        to: str | None,
        amount: str | None,
        ttl_ms: int | None,
        intensity_key: str | None,
        seed: str | int | None,
    ) -> str:
        run = self._ensure_run_accepts_actions(run_id)

        eq = str(equivalent or "").strip().upper()
        if not eq:
            raise ConflictException("equivalent is required")

        edges = None
        with self._lock:
            edges = (run._edges_by_equivalent or {}).get(eq)
        edges = list(edges or [])

        # Deterministic-ish selection for debug.
        seed_raw = f"{run_id}:{eq}:{seed}".encode("utf-8")
        seed_int = int.from_bytes(hashlib.sha256(seed_raw).digest()[:4], "big")
        rng = random.Random(seed_int)

        src = str(from_ or "").strip()
        dst = str(to or "").strip()

        if (not src or not dst) and edges:
            # Pick any 1-hop edge.
            a, b = rng.choice(edges)
            src = src or str(a)
            dst = dst or str(b)

        if not src or not dst:
            raise ConflictException(
                "Cannot choose from/to for tx-once (no edges available)",
                details={"run_id": run_id, "equivalent": eq},
            )

        if src == dst:
            raise ConflictException(
                "from and to must differ", details={"from": src, "to": dst}
            )

        amt = str(amount or "").strip() or "1.00"
        ttl = int(ttl_ms) if ttl_ms is not None else 1200
        ttl = max(50, min(ttl, 30_000))
        ik = str(intensity_key or "").strip() or "mid"

        evt = SimulatorTxUpdatedEvent(
            event_id=self._sse.next_event_id(run),
            ts=_utc_now(),
            type="tx.updated",
            equivalent=eq,
            from_=src,
            to=dst,
            amount=amt,
            amount_flyout=True,
            ttl_ms=ttl,
            intensity_key=ik,
            edges=[{"from": src, "to": dst}],
            node_badges=None,
        ).model_dump(mode="json", by_alias=True)

        with self._lock:
            run.last_event_type = "tx.updated"
        self._sse.broadcast(run_id, evt)
        return str(evt.get("event_id") or "")

    def emit_debug_clearing_once(
        self,
        *,
        run_id: str,
        equivalent: str,
        cycle_edges: list[dict[str, str]] | None,
        cleared_amount: str | None,
        seed: str | int | None,
    ) -> tuple[str, str, str, str]:
        """Emits (plan_id, plan_event_id, done_event_id, equivalent)."""
        run = self._ensure_run_accepts_actions(run_id)

        eq = str(equivalent or "").strip().upper()
        if not eq:
            raise ConflictException("equivalent is required")

        # Determine cycle edges: either user-provided, or best-effort find a small cycle.
        picked: list[tuple[str, str]] = []

        if cycle_edges:
            for e in cycle_edges:
                if not isinstance(e, dict):
                    continue
                a = str(e.get("from") or "").strip()
                b = str(e.get("to") or "").strip()
                if a and b and a != b:
                    picked.append((a, b))

        if not picked:
            with self._lock:
                edges = list(((run._edges_by_equivalent or {}).get(eq) or []))

            if not edges:
                raise ConflictException(
                    "Cannot choose clearing cycle (no edges available)",
                    details={"run_id": run_id, "equivalent": eq},
                )

            seed_raw = f"{run_id}:{eq}:clearing:{seed}".encode("utf-8")
            seed_int = int.from_bytes(hashlib.sha256(seed_raw).digest()[:4], "big")
            rng = random.Random(seed_int)
            rng.shuffle(edges)

            # Build adjacency and attempt to find a short directed cycle (3..6).
            adj: dict[str, list[str]] = {}
            for a, b in edges:
                adj.setdefault(str(a), []).append(str(b))

            nodes = list(adj.keys())
            rng.shuffle(nodes)
            max_depth = 6

            def _find_cycle(start: str) -> list[tuple[str, str]] | None:
                stack: list[str] = [start]
                seen: set[str] = {start}

                def dfs(cur: str, depth: int) -> list[str] | None:
                    if depth > max_depth:
                        return None
                    for nxt in adj.get(cur, []):
                        if nxt == start and depth >= 3:
                            return stack + [start]
                        if nxt in seen:
                            continue
                        seen.add(nxt)
                        stack.append(nxt)
                        res = dfs(nxt, depth + 1)
                        if res is not None:
                            return res
                        stack.pop()
                        seen.remove(nxt)
                    return None

                path = dfs(start, 1)
                if not path:
                    return None
                out: list[tuple[str, str]] = []
                for i in range(len(path) - 1):
                    out.append((path[i], path[i + 1]))
                return out

            for start in nodes[:50]:
                cyc = _find_cycle(str(start))
                if cyc:
                    picked = cyc
                    break

        if not picked:
            # Fallback: 2-edge "cycle-like" viz (won't be a true cycle).
            with self._lock:
                edges = list(((run._edges_by_equivalent or {}).get(eq) or []))
            if len(edges) >= 2:
                picked = [
                    (str(edges[0][0]), str(edges[0][1])),
                    (str(edges[1][0]), str(edges[1][1])),
                ]
            else:
                a, b = str(edges[0][0]), str(edges[0][1])
                picked = [(a, b)]

        plan_id = f"plan_dbg_{secrets.token_hex(6)}"
        plan_evt = SimulatorClearingPlanEvent(
            event_id=self._sse.next_event_id(run),
            ts=_utc_now(),
            type="clearing.plan",
            equivalent=eq,
            plan_id=plan_id,
            steps=[
                {
                    "at_ms": 0,
                    "intensity_key": "high",
                    "highlight_edges": [{"from": a, "to": b} for a, b in picked],
                },
                {
                    "at_ms": 400,
                    "intensity_key": "mid",
                    "particles_edges": [{"from": a, "to": b} for a, b in picked],
                },
                {
                    "at_ms": 900,
                    "flash": {"kind": "clearing"},
                },
            ],
        ).model_dump(mode="json", by_alias=True)

        with self._lock:
            run.last_event_type = "clearing.plan"
        self._sse.broadcast(run_id, plan_evt)

        done_event_id = self._sse.next_event_id(run)
        done_amt = str(cleared_amount or "").strip() or "10.00"

        async def _emit_done_later() -> None:
            try:
                await asyncio.sleep(1.1)
                done_evt = SimulatorClearingDoneEvent(
                    event_id=done_event_id,
                    ts=_utc_now(),
                    type="clearing.done",
                    equivalent=eq,
                    plan_id=plan_id,
                    cleared_cycles=1,
                    cleared_amount=done_amt,
                    node_patch=None,
                    edge_patch=None,
                ).model_dump(mode="json", by_alias=True)
                with self._lock:
                    run.last_event_type = "clearing.done"
                self._sse.broadcast(run_id, done_evt)
            except Exception:
                logger.exception(
                    "simulator.debug_clearing_done_emit_failed run_id=%s", run_id
                )

        try:
            asyncio.get_running_loop().create_task(_emit_done_later())
        except RuntimeError:
            # No running loop (should not happen under FastAPI); fall back to immediate emit.
            done_evt = SimulatorClearingDoneEvent(
                event_id=done_event_id,
                ts=_utc_now(),
                type="clearing.done",
                equivalent=eq,
                plan_id=plan_id,
                cleared_cycles=1,
                cleared_amount=done_amt,
                node_patch=None,
                edge_patch=None,
            ).model_dump(mode="json", by_alias=True)
            with self._lock:
                run.last_event_type = "clearing.done"
            self._sse.broadcast(run_id, done_evt)

        return plan_id, str(plan_evt.get("event_id") or ""), str(done_event_id), eq

    async def pause(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.pause(run_id)

    async def resume(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.resume(run_id)

    async def stop(
        self,
        run_id: str,
        *,
        source: Optional[str] = None,
        reason: Optional[str] = None,
        client: Optional[str] = None,
    ) -> RunStatus:
        status = await self._run_lifecycle.stop(
            run_id, source=source, reason=reason, client=client
        )

        # Best-effort final DB flush for real-mode tick metrics/bottlenecks.
        # Must not break stop() even if DB is unavailable.
        try:
            await self._real_runner.flush_pending_storage(run_id)
        except Exception:
            logger.warning(
                "simulator.runtime.flush_pending_storage_failed run_id=%s",
                run_id,
                exc_info=True,
            )

        return status

    async def restart(self, run_id: str) -> RunStatus:
        return await self._run_lifecycle.restart(run_id)

    async def set_intensity(self, run_id: str, intensity_percent: int) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            run.intensity_percent = int(intensity_percent)

        self.publish_run_status(run_id)
        await simulator_storage.upsert_run(run)
        return _run_to_status(run)

    async def subscribe(
        self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None
    ) -> _Subscription:
        run = self.get_run(run_id)
        sub = await self._sse.subscribe(
            run_id, equivalent=equivalent, after_event_id=after_event_id
        )

        # For fixtures-mode UX/tests: emit one domain event immediately after subscribe.
        if after_event_id is None and run.state == "running" and run.mode == "fixtures":
            evt = self._fixtures_runner.maybe_make_tx_updated(
                run_id=run_id, equivalent=equivalent
            )
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
        snap = await self.build_graph_snapshot(
            run_id=run_id, equivalent=equivalent, session=session
        )
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
        links = [
            edge
            for edge in snap.links
            if edge.source in visited and edge.target in visited
        ]
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
                    run.sim_time_ms = run.tick_index * int(self._tick_ms_base)

                    # Real-mode sets queue_depth during tick work; fixtures-mode has no queue.
                    if run.mode == "fixtures":
                        run.queue_depth = 0

                    # Fixtures-mode event generation (best-effort).
                    if run.mode == "fixtures":
                        self._fixtures_runner.tick_fixtures_events(run_id)

                # Real-mode runner work is intentionally outside the lock
                # (it can touch the DB and may take time).
                if run.mode == "real" and run.state == "running":
                    _t0 = time.monotonic()
                    await self._real_runner.tick_real_mode(run_id)
                    _tick_elapsed_s = time.monotonic() - _t0
                    if _tick_elapsed_s > 2.0:
                        logger.warning(
                            "simulator.heartbeat.slow_tick run_id=%s tick=%d elapsed=%.2fs",
                            run_id,
                            run.tick_index,
                            _tick_elapsed_s,
                        )

                self.publish_run_status(run_id)

                # Debounce DB writes to reduce disk IO.
                now_ms = int(time.time() * 1000)
                persist_every_ms = int(getattr(self, "_run_persist_every_ms", 0) or 0)
                dirty_every_ms = int(
                    getattr(self, "_run_persist_dirty_every_ms", 0) or 0
                )
                if dirty_every_ms <= 0:
                    dirty_every_ms = persist_every_ms

                if persist_every_ms <= 0:
                    await simulator_storage.upsert_run(run)
                    continue

                last_sig = run._persist_last_sig
                if last_sig is None:
                    # Initialize baseline from current state; create_run already upserts.
                    run._persist_last_at_ms = now_ms
                    run._persist_last_state = str(run.state)
                    run._persist_last_sig = (
                        str(run.state),
                        int(run.intensity_percent or 0),
                        float(run.ops_sec or 0.0),
                        int(run.queue_depth or 0),
                        int(run.errors_total or 0),
                        str(run.last_event_type or ""),
                        str(run.current_phase or ""),
                        str(run.stopped_at or ""),
                        str(run.last_error or ""),
                    )
                    continue

                sig = (
                    str(run.state),
                    int(run.intensity_percent or 0),
                    float(run.ops_sec or 0.0),
                    int(run.queue_depth or 0),
                    int(run.errors_total or 0),
                    str(run.last_event_type or ""),
                    str(run.current_phase or ""),
                    str(run.stopped_at or ""),
                    str(run.last_error or ""),
                )

                last_at = int(run._persist_last_at_ms or 0)
                last_state = str(run._persist_last_state or "")
                state_now = str(run.state)
                state_changed = state_now != last_state
                dirty = sig != last_sig
                due_ms = dirty_every_ms if dirty else persist_every_ms

                if state_changed or (due_ms > 0 and (now_ms - last_at) >= due_ms):
                    await simulator_storage.upsert_run(run)
                    run._persist_last_at_ms = now_ms
                    run._persist_last_state = state_now
                    run._persist_last_sig = sig
        except asyncio.CancelledError:
            return


class SimulatorRuntime(_SimulatorRuntimeBase):
    """Concrete runtime implementation.

    Public entrypoints should import `SimulatorRuntime`/`runtime` from
    `app.core.simulator.runtime` to keep imports stable.
    """

    def _db_enabled(self) -> bool:
        """Whether simulator DB persistence is enabled in the current process."""
        return simulator_storage.db_enabled()


runtime = SimulatorRuntime()
