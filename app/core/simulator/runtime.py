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
import zipfile
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Optional

from jsonschema import Draft202012Validator
from sqlalchemy import delete, select, func

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
from app.db.models.simulator_storage import SimulatorRun, SimulatorRunArtifact, SimulatorRunBottleneck, SimulatorRunMetric
from app.db.models.trustline import TrustLine
import app.db.session as db_session
from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.payments.service import PaymentService
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


_SCENARIO_VALIDATOR: Optional[Draft202012Validator] = None


def _get_scenario_validator() -> Optional[Draft202012Validator]:
    global _SCENARIO_VALIDATOR
    if _SCENARIO_VALIDATOR is not None:
        return _SCENARIO_VALIDATOR
    if not SCENARIO_SCHEMA_PATH.exists():
        return None
    schema = json.loads(SCENARIO_SCHEMA_PATH.read_text(encoding="utf-8"))
    _SCENARIO_VALIDATOR = Draft202012Validator(schema)
    return _SCENARIO_VALIDATOR


def _validate_scenario_or_400(raw: dict[str, Any]) -> None:
    validator = _get_scenario_validator()
    if validator is None:
        return
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
    if not errors:
        return

    def _err(e):
        return {
            "path": "/".join(str(p) for p in e.path),
            "message": e.message,
        }

    raise BadRequestException(
        "Scenario invalid",
        details={
            "simulator_error": "SCENARIO_INVALID",
            "errors": [_err(e) for e in errors[:50]],
        },
    )


@dataclass(frozen=True)
class ScenarioRecord:
    scenario_id: str
    name: Optional[str]
    created_at: Optional[datetime]
    participants_count: int
    trustlines_count: int
    equivalents: list[str]
    raw: dict[str, Any]
    source_path: Optional[Path]

    def summary(self) -> ScenarioSummary:
        return ScenarioSummary(
            api_version=SIMULATOR_API_VERSION,
            scenario_id=self.scenario_id,
            name=self.name,
            created_at=self.created_at,
            participants_count=self.participants_count,
            trustlines_count=self.trustlines_count,
            equivalents=self.equivalents,
        )


@dataclass
class _Subscription:
    equivalent: str
    queue: "asyncio.Queue[dict[str, Any]]"


@dataclass
class RunRecord:
    run_id: str
    scenario_id: str
    mode: RunMode
    state: RunState

    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None

    sim_time_ms: int = 0
    tick_index: int = 0
    seed: int = 0
    intensity_percent: int = 0
    ops_sec: float = 0.0
    queue_depth: int = 0

    errors_total: int = 0
    last_error: Optional[dict[str, Any]] = None

    last_event_type: Optional[str] = None
    current_phase: Optional[str] = None

    _event_seq: int = 0
    _subs: list[_Subscription] = None  # type: ignore[assignment]
    _heartbeat_task: Optional[asyncio.Task[None]] = None

    # Best-effort in-memory replay buffer for SSE reconnects.
    # Stores recent emitted events (both run_status and domain events).
    _event_buffer: "deque[tuple[float, str, str, dict[str, Any]]]" = field(default_factory=deque)

    _rng: random.Random | None = None
    _edges_by_equivalent: dict[str, list[tuple[str, str]]] | None = None
    _next_tx_at_ms: int = 0
    _next_clearing_at_ms: int = 0
    _clearing_pending_done_at_ms: int | None = None

    artifacts_dir: Optional[Path] = None

    # Artifacts writer (events.ndjson). Best-effort and async-safe.
    _artifact_events_queue: "asyncio.Queue[Optional[str]]" | None = None
    _artifact_events_task: Optional[asyncio.Task[None]] = None

    # Real-mode in-process runner state (best-effort MVP).
    _real_seeded: bool = False
    _real_participants: list[tuple[uuid.UUID, str]] | None = None
    _real_equivalents: list[str] | None = None

    _real_max_in_flight: int = REAL_MAX_IN_FLIGHT_DEFAULT
    _real_in_flight: int = 0
    _real_consec_tick_failures: int = 0

    def __post_init__(self) -> None:
        if self._subs is None:
            self._subs = []


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

        self._load_fixture_scenarios()
        self._load_uploaded_scenarios()

        # SSE replay buffer sizing/TTL. Best-effort; does not change OpenAPI.
        self._event_buffer_max = int(os.environ.get("SIMULATOR_EVENT_BUFFER_SIZE", "2000"))
        self._event_buffer_ttl_sec = int(os.environ.get("SIMULATOR_EVENT_BUFFER_TTL_SEC", "600"))

        # If enabled, SSE endpoints may return 410 when Last-Event-ID is too old
        # to be replayed from the in-memory ring buffer.
        self._sse_strict_replay = bool(int(os.environ.get("SIMULATOR_SSE_STRICT_REPLAY", "0")))

    def is_sse_strict_replay_enabled(self) -> bool:
        return self._sse_strict_replay

    def is_replay_too_old(self, *, run_id: str, after_event_id: str) -> bool:
        """Returns True if after_event_id is older than the oldest event in buffer.

        This is best-effort and only applies to standard runtime event ids.
        """
        run = self.get_run(run_id)
        after_seq = self._event_seq_from_event_id(run_id=run_id, event_id=after_event_id)
        if after_seq is None:
            return False

        with self._lock:
            self._prune_event_buffer_locked(run)
            if not run._event_buffer:
                return False
            oldest_event_id = run._event_buffer[0][1]

        oldest_seq = self._event_seq_from_event_id(run_id=run_id, event_id=oldest_event_id)
        if oldest_seq is None:
            return False
        return after_seq < oldest_seq

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
        _validate_scenario_or_400(scenario)

        scenario_id = str(scenario.get("scenario_id") or "").strip()
        if not scenario_id:
            raise BadRequestException("Scenario must contain scenario_id")

        # Persist under .local-run to avoid dirtying the repo.
        base = _local_state_dir() / "scenarios" / scenario_id
        base.mkdir(parents=True, exist_ok=True)
        path = base / "scenario.json"

        # If exists, treat as conflict to keep behavior explicit.
        if path.exists():
            raise BadRequestException(f"Scenario {scenario_id} already exists", details={"scenario_id": scenario_id})

        path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
        rec = _scenario_to_record(scenario, source_path=path, created_at=_utc_now())
        with self._lock:
            self._scenarios[scenario_id] = rec
        return rec

    def _load_fixture_scenarios(self) -> None:
        if not FIXTURES_DIR.exists():
            # This typically means the runtime is running without repo fixtures mounted
            # (e.g., a Docker image missing COPY fixtures/...). Keep startup resilient,
            # but make the root cause visible in logs.
            logger.warning("simulator.fixtures_missing path=%s", str(FIXTURES_DIR))
            return

        for child in sorted(FIXTURES_DIR.iterdir()):
            if not child.is_dir():
                continue
            scenario_path = child / "scenario.json"
            if not scenario_path.exists():
                continue
            try:
                raw = json.loads(scenario_path.read_text(encoding="utf-8"))
                rec = _scenario_to_record(raw, source_path=scenario_path, created_at=None)
                self._scenarios[rec.scenario_id] = rec
            except Exception:
                # Keep startup resilient; invalid fixture can be diagnosed later.
                logger.exception("simulator.fixture_scenario_load_failed path=%s", str(scenario_path))
                continue

    def _load_uploaded_scenarios(self) -> None:
        base = _local_state_dir() / "scenarios"
        if not base.exists():
            return

        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            scenario_path = child / "scenario.json"
            if not scenario_path.exists():
                continue
            try:
                raw = json.loads(scenario_path.read_text(encoding="utf-8"))
                rec = _scenario_to_record(raw, source_path=scenario_path, created_at=None)
                self._scenarios[rec.scenario_id] = rec
            except Exception:
                continue

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
        try:
            artifacts_dir = _local_state_dir() / "runs" / run_id / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            run.artifacts_dir = artifacts_dir
            (artifacts_dir / "last_tick.json").write_text(
                json.dumps({"tick_index": 0, "sim_time_ms": 0}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (artifacts_dir / "status.json").write_text(
                json.dumps(
                    {
                        "api_version": SIMULATOR_API_VERSION,
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "mode": mode,
                        "created_at": _utc_now().isoformat(),
                        "seed": seed,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # Raw events export (NDJSON). The writer task appends lines.
            (artifacts_dir / "events.ndjson").write_text("", encoding="utf-8")
        except Exception:
            run.artifacts_dir = None

        with self._lock:
            self._runs[run_id] = run
            self._active_run_id = run_id

        # Start artifacts writer (best-effort; no-op if artifacts disabled).
        self._start_artifact_events_writer(run_id)

        # Start heartbeat loop.
        run._heartbeat_task = asyncio.create_task(self._heartbeat_loop(run_id), name=f"simulator-heartbeat:{run_id}")
        # Emit immediate status event (so SSE clients don't wait for first tick).
        self.publish_run_status(run_id)
        await self._db_upsert_run(run)
        await self._db_sync_artifacts(run)
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
        run = self.get_run(run_id)
        _ = self.get_scenario(run.scenario_id)

        if to_ms < from_ms:
            raise BadRequestException("to_ms must be >= from_ms")
        if step_ms <= 0:
            raise BadRequestException("step_ms must be > 0")

        points_count = (to_ms - from_ms) // step_ms + 1
        if points_count > 2000:
            raise BadRequestException("Too many points", details={"max_points": 2000})

        keys: list[tuple[MetricSeriesKey, Optional[str]]] = [
            ("success_rate", "%"),
            ("avg_route_length", "count"),
            ("total_debt", "amount"),
            ("clearing_volume", "amount"),
            ("bottlenecks_score", "%"),
        ]

        # DB-first for real-mode: return persisted points if available.
        if self._db_enabled() and run.mode == "real":
            try:
                async with db_session.AsyncSessionLocal() as session:
                    rows = (
                        await session.execute(
                            select(SimulatorRunMetric)
                            .where(
                                (SimulatorRunMetric.run_id == run_id)
                                & (SimulatorRunMetric.equivalent_code == str(equivalent))
                                & (SimulatorRunMetric.t_ms >= 0)
                                & (SimulatorRunMetric.t_ms <= int(to_ms))
                                & (SimulatorRunMetric.key.in_([k for (k, _u) in keys]))
                            )
                            .order_by(SimulatorRunMetric.key.asc(), SimulatorRunMetric.t_ms.asc())
                        )
                    ).scalars().all()

                by_key: dict[str, list[tuple[int, float]]] = {str(k): [] for (k, _u) in keys}
                for r in rows:
                    if r.value is None:
                        continue
                    by_key.setdefault(str(r.key), []).append((int(r.t_ms), float(r.value)))

                # Resample persisted tick metrics to (from_ms..to_ms, step_ms) using carry-forward.
                # This guarantees MetricPoint.v is always numeric.
                series: list[MetricSeries] = []
                for key, unit in keys:
                    timeline = by_key.get(str(key), [])
                    idx = 0
                    last_val = 0.0
                    pts: list[MetricPoint] = []
                    for i in range(points_count):
                        t = int(from_ms + i * step_ms)
                        while idx < len(timeline) and int(timeline[idx][0]) <= t:
                            last_val = float(timeline[idx][1])
                            idx += 1
                        pts.append(MetricPoint(t_ms=t, v=float(last_val)))
                    series.append(MetricSeries(key=key, unit=unit, points=pts))

                return MetricsResponse(
                    api_version=SIMULATOR_API_VERSION,
                    run_id=run_id,
                    equivalent=equivalent,
                    from_ms=from_ms,
                    to_ms=to_ms,
                    step_ms=step_ms,
                    series=series,
                )
            except Exception:
                # Fall back to synthetic below.
                pass

        def v01(x: float) -> float:
            return max(0.0, min(1.0, x))

        def seed_f(*parts: str) -> float:
            h = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
            # [0,1)
            return int.from_bytes(h[:8], "big") / 2**64

        base = seed_f(run_id, equivalent)
        intensity = max(0.0, min(1.0, float(run.intensity_percent) / 100.0))
        edges_n = len(((run._edges_by_equivalent or {}).get(equivalent) or []))

        series: list[MetricSeries] = []

        for key, unit in keys:
            points: list[MetricPoint] = []
            for i in range(points_count):
                t = from_ms + i * step_ms
                # Deterministic pseudo-dynamics; good enough for UI scaffolding.
                if key == "success_rate":
                    # Slightly lower at high intensity.
                    wobble = 0.08 * (0.5 - abs((seed_f(run_id, equivalent, str(t)) - 0.5)))
                    v = v01(0.93 - 0.18 * intensity + wobble)
                    v = v * 100.0
                elif key == "avg_route_length":
                    # Route length grows with graph size a bit.
                    base_len = 2.0 + min(4.0, max(0.0, edges_n / 50.0))
                    jitter = (seed_f(run_id, "arl", equivalent, str(t)) - 0.5) * 0.6
                    v = max(1.0, base_len + jitter)
                elif key == "total_debt":
                    # Monotonic-ish growth with occasional easing.
                    v = max(0.0, (t / 1000.0) * (0.2 + 0.8 * intensity) * (5.0 + 20.0 * base))
                elif key == "clearing_volume":
                    # Peaks around clearing window.
                    if t < 25_000:
                        v = 0.0
                    else:
                        phase = (t - 25_000) % 45_000
                        spike = 1.0 if 0 <= phase < 3_000 else 0.15
                        v = spike * (50.0 + 150.0 * intensity) * (0.8 + 0.4 * base)
                else:  # bottlenecks_score
                    # Higher score when sparse or at high intensity.
                    sparsity = 1.0 if edges_n == 0 else max(0.0, 1.0 - min(1.0, edges_n / 200.0))
                    wobble = (seed_f(run_id, "bns", equivalent, str(t)) - 0.5) * 0.12
                    v = v01(0.20 + 0.55 * sparsity + 0.20 * intensity + wobble) * 100.0

                points.append(MetricPoint(t_ms=t, v=float(v)))
            series.append(MetricSeries(key=key, unit=unit, points=points))

        return MetricsResponse(
            api_version=SIMULATOR_API_VERSION,
            run_id=run_id,
            equivalent=equivalent,
            from_ms=from_ms,
            to_ms=to_ms,
            step_ms=step_ms,
            series=series,
        )

    async def build_bottlenecks(
        self,
        *,
        run_id: str,
        equivalent: str,
        limit: int,
        min_score: Optional[float],
    ) -> BottlenecksResponse:
        run = self.get_run(run_id)
        scenario = self.get_scenario(run.scenario_id).raw

        # DB-first for real-mode: return persisted bottlenecks if available.
        if self._db_enabled() and run.mode == "real":
            try:
                async with db_session.AsyncSessionLocal() as session:
                    latest = (
                        await session.execute(
                            select(func.max(SimulatorRunBottleneck.computed_at)).where(
                                (SimulatorRunBottleneck.run_id == run_id)
                                & (SimulatorRunBottleneck.equivalent_code == str(equivalent))
                            )
                        )
                    ).scalar_one_or_none()
                    if latest is not None:
                        q = select(SimulatorRunBottleneck).where(
                            (SimulatorRunBottleneck.run_id == run_id)
                            & (SimulatorRunBottleneck.equivalent_code == str(equivalent))
                            & (SimulatorRunBottleneck.computed_at == latest)
                        )
                        rows = (await session.execute(q)).scalars().all()

                items: list[BottleneckItem] = []
                for r in rows:
                    if str(r.target_type) != "edge":
                        continue
                    src = ""
                    dst = ""
                    try:
                        parts = str(r.target_id).split("->", 1)
                        src = parts[0]
                        dst = parts[1]
                    except Exception:
                        continue
                    if not src or not dst:
                        continue

                    score = float(r.score)
                    if min_score is not None and score < float(min_score):
                        continue

                    details = r.details or {}
                    label = details.get("label") if isinstance(details, dict) else None
                    suggested_action = details.get("suggested_action") if isinstance(details, dict) else None

                    items.append(
                        BottleneckItem(
                            target=BottleneckTargetEdge(kind="edge", **{"from": src}, to=dst),
                            score=score,
                            reason_code=str(r.reason_code),
                            label=label,
                            suggested_action=suggested_action,
                        )
                    )

                items.sort(key=lambda x: (x.score, getattr(x.target, "from_", ""), getattr(x.target, "to", "")), reverse=True)
                items = items[: int(limit)]
                return BottlenecksResponse(
                    api_version=SIMULATOR_API_VERSION,
                    run_id=run_id,
                    equivalent=equivalent,
                    items=items,
                )
            except Exception:
                # Fall back to synthetic below.
                pass

        # Read trustlines so we have access to limits.
        tls = [
            tl
            for tl in (scenario.get("trustlines") or [])
            if str(tl.get("equivalent") or "") == str(equivalent)
        ]

        def seed_f(*parts: str) -> float:
            h = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
            return int.from_bytes(h[:8], "big") / 2**64

        # Use current sim time if available.
        t = int(run.sim_time_ms or 0)
        intensity = max(0.0, min(1.0, float(run.intensity_percent) / 100.0))

        items: list[BottleneckItem] = []

        for tl in tls:
            src = str(tl.get("from") or "").strip()
            dst = str(tl.get("to") or "").strip()
            if not src or not dst:
                continue
            limit_raw = tl.get("limit")
            try:
                limit_value = float(limit_raw)
            except Exception:
                limit_value = 0.0

            # Synthetic used fraction: increases with intensity and time.
            wobble = abs(seed_f(run_id, equivalent, src, dst, str(t)) - 0.5) * 2.0
            used_ratio = max(0.0, min(1.0, 0.15 + 0.65 * wobble + 0.15 * intensity))
            available_ratio = 1.0 - used_ratio

            # Score [0..1] where higher means more problematic.
            score = max(0.0, min(1.0, 1.0 - available_ratio))
            if min_score is not None and score < float(min_score):
                continue

            reason: BottleneckReasonCode = "LOW_AVAILABLE" if available_ratio < 0.25 else "HIGH_USED"

            label = None
            suggested_action = None
            if reason == "LOW_AVAILABLE":
                label = "Low available capacity"
                suggested_action = "Increase trust limit or rebalance via clearing"
            else:
                label = "High utilization"
                suggested_action = "Consider clearing or adding alternative routes"

            items.append(
                BottleneckItem(
                    target=BottleneckTargetEdge(kind="edge", **{"from": src}, to=dst),
                    score=float(score),
                    reason_code=reason,
                    label=label,
                    suggested_action=suggested_action,
                )
            )

        items.sort(key=lambda x: x.score, reverse=True)
        items = items[: int(limit)]

        # Keep writing synthetic bottlenecks only for non-real mode (UI scaffolding);
        # real-mode is DB-first and writes from the runner.
        if self._db_enabled() and run.mode != "real":
            try:
                computed_at = _utc_now()
                async with db_session.AsyncSessionLocal() as session:
                    rows: list[SimulatorRunBottleneck] = []
                    for it in items:
                        target_id = f"{it.target.from_}->{it.target.to}"  # type: ignore[attr-defined]
                        rows.append(
                            SimulatorRunBottleneck(
                                run_id=run_id,
                                equivalent_code=str(equivalent),
                                computed_at=computed_at,
                                target_type="edge",
                                target_id=target_id,
                                score=float(it.score),
                                reason_code=str(it.reason_code),
                                details={
                                    "label": it.label,
                                    "suggested_action": it.suggested_action,
                                },
                            )
                        )
                    session.add_all(rows)
                    await session.commit()
            except Exception:
                pass
        return BottlenecksResponse(
            api_version=SIMULATOR_API_VERSION,
            run_id=run_id,
            equivalent=equivalent,
            items=items,
        )

    async def list_artifacts(self, *, run_id: str) -> ArtifactIndex:
        run = self.get_run(run_id)
        base = run.artifacts_dir
        if base is None or not base.exists():
            return ArtifactIndex(
                api_version=SIMULATOR_API_VERSION,
                run_id=run_id,
                artifact_path=None,
                items=[],
                bundle_url=None,
            )

        sha_max_bytes = int(os.getenv("SIMULATOR_ARTIFACT_SHA_MAX_BYTES", "524288") or "524288")

        def _content_type(name: str) -> Optional[str]:
            if name.endswith(".ndjson"):
                return "application/x-ndjson"
            if name.endswith(".json"):
                return "application/json"
            if name.endswith(".zip"):
                return "application/zip"
            return None

        def _sha256_file(path: Path) -> Optional[str]:
            try:
                h = hashlib.sha256()
                with path.open("rb") as f:
                    while True:
                        chunk = f.read(1024 * 128)
                        if not chunk:
                            break
                        h.update(chunk)
                return h.hexdigest()
            except Exception:
                return None

        items: list[ArtifactItem] = []
        for p in sorted(base.iterdir()):
            if not p.is_file():
                continue
            url = f"/api/v1/simulator/runs/{run_id}/artifacts/{p.name}"
            sha = None
            size = None
            try:
                size = int(p.stat().st_size)
                if size <= sha_max_bytes:
                    sha = _sha256_file(p)
            except Exception:
                pass
            items.append(
                ArtifactItem(
                    name=p.name,
                    url=url,
                    content_type=_content_type(p.name),
                    size_bytes=size,
                    sha256=sha,
                )
            )

        if self._db_enabled():
            try:
                async with db_session.AsyncSessionLocal() as session:
                    await session.execute(delete(SimulatorRunArtifact).where(SimulatorRunArtifact.run_id == run_id))
                    rows = [
                        SimulatorRunArtifact(
                            run_id=run_id,
                            name=i.name,
                            content_type=i.content_type,
                            size_bytes=i.size_bytes,
                            sha256=i.sha256,
                            storage_url=str(i.url),
                        )
                        for i in items
                    ]
                    session.add_all(rows)
                    await session.commit()
            except Exception:
                pass

        return ArtifactIndex(
            api_version=SIMULATOR_API_VERSION,
            run_id=run_id,
            artifact_path=str(base),
            items=items,
            bundle_url=(
                f"/api/v1/simulator/runs/{run_id}/artifacts/bundle.zip" if (base / "bundle.zip").exists() else None
            ),
        )

    def get_artifact_path(self, *, run_id: str, name: str) -> Path:
        run = self.get_run(run_id)
        base = run.artifacts_dir
        if base is None:
            raise NotFoundException("Artifact not found")
        p = (base / name).resolve()
        if not str(p).startswith(str(base.resolve())):
            raise NotFoundException("Artifact not found")
        if not p.exists() or not p.is_file():
            raise NotFoundException("Artifact not found")
        return p

    def publish_run_status(self, run_id: str) -> None:
        run = self.get_run(run_id)
        payload = SimulatorRunStatusEvent(
            event_id=self._next_event_id(run),
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
        self._broadcast(run_id, payload)

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
        await self._db_upsert_run(run)
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
        await self._db_upsert_run(run)
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

        await self._db_upsert_run(run)

        # Enforce TTL-based pruning even if no further events are appended.
        with self._lock:
            self._prune_event_buffer_locked(run)

        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if events_task is not None:
            await self._stop_artifact_events_writer(run_id)

        # Finalize artifacts (best-effort): status.json, summary.json, bundle.zip.
        await self._finalize_run_artifacts(run_id)
        return _run_to_status(run)

    async def _finalize_run_artifacts(self, run_id: str) -> None:
        run = self.get_run(run_id)
        base = run.artifacts_dir
        if base is None or not base.exists():
            return

        status_payload = self.get_run_status(run_id).model_dump(mode="json")
        summary_payload: dict[str, Any] = {
            "api_version": SIMULATOR_API_VERSION,
            "generated_at": _utc_now().isoformat(),
            "run_id": run_id,
            "scenario_id": run.scenario_id,
            "mode": run.mode,
            "state": run.state,
            "status": status_payload,
        }

        def _write_json(path: Path, payload: dict[str, Any]) -> None:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        def _build_bundle(artifacts_dir: Path) -> None:
            bundle = artifacts_dir / "bundle.zip"
            with zipfile.ZipFile(bundle, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(artifacts_dir.iterdir()):
                    if not p.is_file():
                        continue
                    if p.name == "bundle.zip":
                        continue
                    zf.write(p, arcname=p.name)

        try:
            await asyncio.to_thread(_write_json, base / "status.json", status_payload)
            await asyncio.to_thread(_write_json, base / "summary.json", summary_payload)
            await asyncio.to_thread(_build_bundle, base)
        except Exception:
            return

        try:
            await self._db_sync_artifacts(run)
        except Exception:
            return

    def _start_artifact_events_writer(self, run_id: str) -> None:
        run = self.get_run(run_id)
        base = run.artifacts_dir
        if base is None:
            return
        path = base / "events.ndjson"
        try:
            # Ensure file exists.
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")
        except Exception:
            return

        with self._lock:
            if run._artifact_events_task is not None and not run._artifact_events_task.done():
                return
            q: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=10_000)
            run._artifact_events_queue = q
            run._artifact_events_task = asyncio.create_task(
                self._artifact_events_writer_loop(run_id=run_id, path=path, queue=q),
                name=f"simulator-artifacts-events:{run_id}",
            )

    async def _stop_artifact_events_writer(self, run_id: str) -> None:
        run = self.get_run(run_id)
        task: Optional[asyncio.Task[None]]
        q: Optional[asyncio.Queue[Optional[str]]]
        with self._lock:
            task = run._artifact_events_task
            q = run._artifact_events_queue
            run._artifact_events_task = None
            run._artifact_events_queue = None

        if task is None:
            return

        if q is not None:
            try:
                q.put_nowait(None)
            except Exception:
                pass

        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except Exception:
                pass
        except Exception:
            return

    async def _artifact_events_writer_loop(
        self,
        *,
        run_id: str,
        path: Path,
        queue: "asyncio.Queue[Optional[str]]",
    ) -> None:
        # Batch writes and offload to a thread to avoid blocking the event loop.
        def _append_text(p: Path, text: str) -> None:
            with p.open("a", encoding="utf-8") as f:
                f.write(text)

        buf: list[str] = []
        while True:
            item = await queue.get()
            if item is None:
                break
            buf.append(item)

            # Drain quickly to batch.
            for _ in range(1000):
                try:
                    nxt = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    # Re-queue sentinel for the outer loop.
                    await queue.put(None)
                    break
                buf.append(nxt)

            text = "".join(buf)
            buf.clear()
            try:
                await asyncio.to_thread(_append_text, path, text)
            except Exception:
                # Best-effort: drop on IO errors.
                continue

    def _enqueue_event_artifact(self, *, run_id: str, payload: dict[str, Any]) -> None:
        run = self.get_run(run_id)
        q = run._artifact_events_queue
        if q is None:
            return
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        except Exception:
            return
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            # Best-effort drop.
            return

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
            self._prune_event_buffer_locked(run)

        self.publish_run_status(run_id)
        await self._db_upsert_run(run)
        return _run_to_status(run)

    async def set_intensity(self, run_id: str, intensity_percent: int) -> RunStatus:
        run = self.get_run(run_id)
        with self._lock:
            run.intensity_percent = int(intensity_percent)

        self.publish_run_status(run_id)
        await self._db_upsert_run(run)
        return _run_to_status(run)

    async def subscribe(self, run_id: str, *, equivalent: str, after_event_id: Optional[str] = None) -> _Subscription:
        run = self.get_run(run_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        sub = _Subscription(equivalent=equivalent, queue=queue)
        with self._lock:
            self._runs[run_id]._subs.append(sub)

        # Best-effort replay for reconnects.
        if after_event_id:
            for evt in self._replay_events(run_id=run_id, equivalent=equivalent, after_event_id=after_event_id):
                try:
                    sub.queue.put_nowait(evt)
                except asyncio.QueueFull:
                    break

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
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            try:
                run._subs.remove(sub)
            except ValueError:
                return

    async def build_graph_snapshot(
        self,
        *,
        run_id: str,
        equivalent: str,
        session=None,
    ) -> SimulatorGraphSnapshot:
        run = self.get_run(run_id)
        scenario = self.get_scenario(run.scenario_id).raw
        snap = _scenario_to_snapshot(scenario, equivalent=equivalent)
        if run.mode != "real":
            return snap
        return await self._enrich_snapshot_from_db(snap, equivalent=equivalent, session=session)

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

    async def _enrich_snapshot_from_db(
        self,
        snap: SimulatorGraphSnapshot,
        *,
        equivalent: str,
        session=None,
    ) -> SimulatorGraphSnapshot:
        """Real-mode only.

        Enrich scenario-topology snapshot with DB-derived state:
        - link.used/available based on Debt amounts
        - node.net_balance_atoms / node.net_sign
        """

        eq_code = str(equivalent or "").strip().upper()
        if not eq_code:
            return snap

        pids = [n.id for n in snap.nodes if str(n.id or "").strip()]
        if not pids:
            return snap

        async def _load_state(session) -> tuple[
            Equivalent,
            dict[str, Participant],
            dict[str, uuid.UUID],
            dict[tuple[uuid.UUID, uuid.UUID], Decimal],
            dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, str | None]],
        ] | None:
            eq = (
                await session.execute(select(Equivalent).where(Equivalent.code == eq_code))
            ).scalar_one_or_none()
            if eq is None:
                return None

            p_rows = (
                await session.execute(select(Participant).where(Participant.pid.in_(pids)))
            ).scalars().all()
            pid_to_rec = {p.pid: p for p in p_rows}
            pid_to_id = {p.pid: p.id for p in p_rows}
            participant_ids = [p.id for p in p_rows]
            if not participant_ids:
                return None

            # Debts: debtor -> creditor in DB
            debt_rows = (
                await session.execute(
                    select(
                        Debt.creditor_id,
                        Debt.debtor_id,
                        func.coalesce(func.sum(Debt.amount), 0).label("amount"),
                    )
                    .where(
                        Debt.equivalent_id == eq.id,
                        Debt.creditor_id.in_(participant_ids),
                        Debt.debtor_id.in_(participant_ids),
                    )
                    .group_by(Debt.creditor_id, Debt.debtor_id)
                )
            ).all()
            debt_by_pair: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {
                (r.creditor_id, r.debtor_id): (r.amount or Decimal("0")) for r in debt_rows
            }

            # Trustlines: from (creditor) -> to (debtor)
            tl_rows = (
                await session.execute(
                    select(
                        TrustLine.from_participant_id,
                        TrustLine.to_participant_id,
                        TrustLine.limit,
                        TrustLine.status,
                    ).where(
                        TrustLine.equivalent_id == eq.id,
                        TrustLine.from_participant_id.in_(participant_ids),
                        TrustLine.to_participant_id.in_(participant_ids),
                    )
                )
            ).all()
            tl_by_pair: dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, str | None]] = {
                (r.from_participant_id, r.to_participant_id): (r.limit or Decimal("0"), r.status)
                for r in tl_rows
            }

            return eq, pid_to_rec, pid_to_id, debt_by_pair, tl_by_pair

        if session is None:
            async with db_session.AsyncSessionLocal() as s:
                loaded = await _load_state(s)
        else:
            loaded = await _load_state(session)

        if loaded is None:
            return snap

        eq, pid_to_rec, pid_to_id, debt_by_pair, tl_by_pair = loaded

        precision = int(getattr(eq, "precision", 2) or 2)
        scale10 = Decimal(10) ** precision
        money_quant = Decimal(1) / scale10

        def _to_money_str(v: Decimal) -> str:
            return format(v.quantize(money_quant, rounding=ROUND_DOWN), "f")

        def _parse_amount(v: object) -> float | None:
            if v is None:
                return None
            if isinstance(v, (int, float)):
                x = float(v)
                return x if x == x else None
            if isinstance(v, Decimal):
                return float(v)
            if isinstance(v, str):
                s = v.strip().replace(",", "")
                if not s:
                    return None
                try:
                    return float(s)
                except ValueError:
                    return None
            return None

        def _quantile(values_sorted: list[float], p: float) -> float:
            if not values_sorted:
                raise ValueError("values_sorted must be non-empty")
            if p <= 0:
                return values_sorted[0]
            if p >= 1:
                return values_sorted[-1]
            n = len(values_sorted)
            i = int(p * (n - 1))
            return values_sorted[i]

        def _link_width_key(limit: float | None, *, q33: float | None, q66: float | None) -> str:
            if limit is None or q33 is None or q66 is None:
                return "hairline"
            if limit <= q33:
                return "thin"
            if limit <= q66:
                return "mid"
            return "thick"

        def _link_alpha_key(status: str | None, used: float | None, limit: float | None) -> str:
            if status and status != "active":
                return "muted"
            if used is None or limit is None or limit <= 0:
                return "bg"
            r = abs(used) / limit
            if r >= 0.75:
                return "hi"
            if r >= 0.40:
                return "active"
            if r >= 0.15:
                return "muted"
            return "bg"

        # Compute per-node totals from debt table.
        credit_sum: dict[uuid.UUID, Decimal] = {}
        debit_sum: dict[uuid.UUID, Decimal] = {}
        for (creditor_id, debtor_id), amt in debt_by_pair.items():
            if amt <= 0:
                continue
            credit_sum[creditor_id] = credit_sum.get(creditor_id, Decimal("0")) + amt
            debit_sum[debtor_id] = debit_sum.get(debtor_id, Decimal("0")) + amt

        # Links
        link_stats: list[tuple[SimulatorGraphLink, float | None, float | None]] = []
        for link in snap.links:
            src_pid = str(link.source or "").strip()
            dst_pid = str(link.target or "").strip()
            if not src_pid or not dst_pid:
                continue

            src_id = pid_to_id.get(src_pid)
            dst_id = pid_to_id.get(dst_pid)
            if src_id is None or dst_id is None:
                continue

            used_amt = debt_by_pair.get((src_id, dst_id), Decimal("0"))
            limit_amt: Decimal
            status: str | None
            if (src_id, dst_id) in tl_by_pair:
                limit_amt, status = tl_by_pair[(src_id, dst_id)]
            else:
                status = link.status
                try:
                    limit_amt = Decimal(str(link.trust_limit or "0"))
                except (InvalidOperation, ValueError):
                    limit_amt = Decimal("0")

            available_amt = limit_amt - used_amt
            if available_amt < 0:
                available_amt = Decimal("0")

            link.trust_limit = _to_money_str(limit_amt)
            link.used = _to_money_str(used_amt)
            link.available = _to_money_str(available_amt)
            if status is not None:
                link.status = str(status)

            link_stats.append((link, _parse_amount(limit_amt), _parse_amount(used_amt)))

        limits = sorted([x for _, x, _ in link_stats if x is not None])
        q33 = _quantile(limits, 0.33) if limits else None
        q66 = _quantile(limits, 0.66) if limits else None

        for link, limit_num, used_num in link_stats:
            status_key: str | None
            if isinstance(link.status, str):
                status_key = link.status.strip().lower() or None
            else:
                status_key = None
            link.viz_width_key = _link_width_key(limit_num, q33=q33, q66=q66)
            link.viz_alpha_key = _link_alpha_key(status_key, used=used_num, limit=limit_num)

        # Nodes: net + viz
        atoms_by_pid: dict[str, int] = {}
        mags: list[int] = []
        debt_mags: list[int] = []

        for node in snap.nodes:
            pid = str(node.id or "").strip()
            rec = pid_to_rec.get(pid)
            if rec is None:
                continue

            credit = credit_sum.get(rec.id, Decimal("0"))
            debit = debit_sum.get(rec.id, Decimal("0"))
            net = credit - debit

            atoms = int((net * scale10).to_integral_value(rounding=ROUND_HALF_UP))
            node.net_balance_atoms = str(atoms)
            if atoms < 0:
                node.net_sign = -1
            elif atoms > 0:
                node.net_sign = 1
            else:
                node.net_sign = 0

            atoms_by_pid[pid] = atoms
            mags.append(abs(atoms))
            if atoms < 0:
                debt_mags.append(abs(atoms))

            # Prefer DB metadata for status/type if scenario snapshot lacks them.
            if not (node.status and str(node.status).strip()):
                if getattr(rec, "status", None) is not None:
                    node.status = str(rec.status)
            if not (node.type and str(node.type).strip()):
                if getattr(rec, "type", None) is not None:
                    node.type = str(rec.type)

        mags_sorted = sorted(mags)
        mn = len(mags_sorted)

        def _percentile_rank(sorted_mags: list[int], mag: int) -> float:
            n = len(sorted_mags)
            if n <= 1:
                return 0.0
            import bisect

            i = bisect.bisect_right(sorted_mags, mag) - 1
            i = max(0, min(i, n - 1))
            return i / (n - 1)

        def _scale_from_pct(pct: float, max_scale: float = 1.90, gamma: float = 0.75) -> float:
            if pct <= 0:
                return 1.0
            if pct >= 1:
                return max_scale
            return 1.0 + (max_scale - 1.0) * (pct**gamma)

        debt_mags_sorted = sorted(debt_mags)
        dn = len(debt_mags_sorted)
        DEBT_BINS = 9

        def _debt_bin(mag: int) -> int:
            if dn <= 1:
                return 0
            import bisect

            i = bisect.bisect_right(debt_mags_sorted, mag) - 1
            i = max(0, min(i, dn - 1))
            pct = i / (dn - 1)
            b = int(round(pct * (DEBT_BINS - 1)))
            return max(0, min(b, DEBT_BINS - 1))

        for node in snap.nodes:
            pid = str(node.id or "").strip()
            if not pid:
                continue

            atoms = atoms_by_pid.get(pid)
            if atoms is None:
                continue

            status_key = str(node.status or "").strip().lower()
            type_key = str(node.type or "").strip().lower()

            if status_key in {"suspended", "frozen"}:
                node.viz_color_key = "suspended"
            elif status_key == "left":
                node.viz_color_key = "left"
            elif status_key in {"deleted", "banned"}:
                node.viz_color_key = "deleted"
            else:
                if atoms < 0:
                    node.viz_color_key = f"debt-{_debt_bin(abs(atoms))}"
                else:
                    node.viz_color_key = "business" if type_key == "business" else "person"

            pct = _percentile_rank(mags_sorted, abs(atoms)) if mn > 0 else 0.0
            s = _scale_from_pct(pct)
            if type_key == "business":
                w0, h0 = 26, 22
            else:
                w0, h0 = 16, 16
            node.viz_size = SimulatorVizSize(w=float(int(round(w0 * s))), h=float(int(round(h0 * s))))

        return snap

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
                await self._db_upsert_run(run)
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
                ) -> tuple[int, str, str, str, str | None, str | None, float, list[tuple[str, str]]]:
                    """Returns (seq, eq, sender_pid, receiver_pid, status, error_code, avg_route_len, route_edges)."""

                    sender_id = sender_id_by_pid.get(action.sender_pid)
                    if sender_id is None:
                        return (
                            action.seq,
                            action.equivalent,
                            action.sender_pid,
                            action.receiver_pid,
                            None,
                            "SENDER_NOT_FOUND",
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
                                float(avg_route_len),
                                route_edges,
                            )
                        except Exception as e:
                            code = "INTERNAL_ERROR"
                            if isinstance(e, RoutingException):
                                code = "PAYMENT_REJECTED"
                            elif isinstance(e, TimeoutException):
                                code = "PAYMENT_TIMEOUT"
                            elif isinstance(e, GeoException):
                                code = "INTERNAL_ERROR"
                            return (
                                action.seq,
                                action.equivalent,
                                action.sender_pid,
                                action.receiver_pid,
                                None,
                                code,
                                0.0,
                                [],
                            )
                        finally:
                            with self._lock:
                                run._real_in_flight = max(0, run._real_in_flight - 1)

                tasks = [asyncio.create_task(_do_one(a)) for a in planned]

                next_seq = 0
                ready: dict[int, tuple[str, str, str, str | None, str | None, float, list[tuple[str, str]]]] = {}

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
                        eq, sender_pid, receiver_pid, status, err_code, avg_route_len, route_edges = item

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
                                run.last_error = {
                                    "code": err_code,
                                    "message": err_code,
                                    "at": _utc_now().isoformat(),
                                }
                                run.last_event_type = "tx.failed"

                            failed_evt = SimulatorTxFailedEvent(
                                event_id=self._next_event_id(run),
                                ts=_utc_now(),
                                type="tx.failed",
                                equivalent=eq,
                                from_=sender_pid,
                                to=receiver_pid,
                                error={"code": err_code, "message": err_code, "at": _utc_now()},
                            ).model_dump(mode="json", by_alias=True)
                            self._broadcast(run_id, failed_evt)
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

                                evt = SimulatorTxUpdatedEvent(
                                    event_id=self._next_event_id(run),
                                    ts=_utc_now(),
                                    type="tx.updated",
                                    equivalent=eq,
                                    ttl_ms=1200,
                                    edges=[{"from": a, "to": b} for a, b in edges_pairs],
                                    node_badges=None,
                                ).model_dump(mode="json")
                                with self._lock:
                                    run.last_event_type = "tx.updated"
                                self._broadcast(run_id, evt)
                            else:
                                rejected += 1
                                _inc(eq, "rejected")
                                for a, b in edges_pairs:
                                    _edge_inc(eq, a, b, "rejected")

                                with self._lock:
                                    # Track last_error for diagnostics even on clean rejections.
                                    run.last_error = {
                                        "code": "PAYMENT_REJECTED",
                                        "message": "PAYMENT_REJECTED",
                                        "at": _utc_now().isoformat(),
                                    }
                                    run.last_event_type = "tx.failed"

                                failed_evt = SimulatorTxFailedEvent(
                                    event_id=self._next_event_id(run),
                                    ts=_utc_now(),
                                    type="tx.failed",
                                    equivalent=eq,
                                    from_=sender_pid,
                                    to=receiver_pid,
                                    error={"code": "PAYMENT_REJECTED", "message": "PAYMENT_REJECTED", "at": _utc_now()},
                                ).model_dump(mode="json", by_alias=True)
                                self._broadcast(run_id, failed_evt)

                        with self._lock:
                            run.queue_depth = max(0, run.queue_depth - 1)

                        next_seq += 1

                try:
                    if tasks:
                        for t in asyncio.as_completed(tasks):
                            seq, eq, sender_pid, receiver_pid, status, err_code, avg_route_len, route_edges = await t

                            if run.state != "running":
                                break
                            ready[seq] = (eq, sender_pid, receiver_pid, status, err_code, avg_route_len, route_edges)
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

                await self._db_write_tick_metrics(
                    run_id=run.run_id,
                    t_ms=int(run.sim_time_ms),
                    per_equivalent=per_eq,
                    metric_values_by_eq=per_eq_metric_values,
                    db_session=session,
                )

                # Persist bottlenecks snapshot derived from actual tick outcomes.
                if self._db_enabled():
                    computed_at = _utc_now()
                    for eq in equivalents:
                        await self._db_write_tick_bottlenecks(
                            run_id=run.run_id,
                            equivalent=str(eq),
                            computed_at=computed_at,
                            edge_stats=per_eq_edge_stats.get(str(eq), {}),
                            db_session=session,
                            limit=50,
                        )

                self._write_real_tick_artifact(
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
                await self._db_sync_artifacts(run)
        except Exception as e:
            with self._lock:
                run.errors_total += 1
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
            run.last_error = {"code": code, "message": message, "at": _utc_now().isoformat()}
            task = run._heartbeat_task

        self.publish_run_status(run_id)
        await self._db_upsert_run(run)
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
        cleared_amount_by_eq: dict[str, float] = {str(eq): 0.0 for eq in equivalents}
        for eq in equivalents:
            try:
                # Plan step: find at least one cycle to visualize.
                cycles = await service.find_cycles(eq, max_depth=6)
                if not cycles:
                    continue

                plan_id = f"plan_{secrets.token_hex(6)}"
                plan_evt = SimulatorClearingPlanEvent(
                    event_id=self._next_event_id(run),
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
                self._broadcast(run_id, plan_evt)

                # Execute with stats (volume = sum of cleared amounts).
                cleared_cycles = 0
                cleared_amount = 0.0
                while True:
                    cycles = await service.find_cycles(eq, max_depth=6)
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
                    event_id=self._next_event_id(run),
                    ts=_utc_now(),
                    type="clearing.done",
                    equivalent=eq,
                ).model_dump(mode="json")
                with self._lock:
                    run.last_event_type = "clearing.done"
                    run.current_phase = None
                self._broadcast(run_id, done_evt)
            except Exception as e:
                with self._lock:
                    run.errors_total += 1
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
        return bool(getattr(settings, "SIMULATOR_DB_ENABLED", False))

    async def _db_upsert_run(self, run: RunRecord) -> None:
        if not self._db_enabled():
            return
        try:
            async with db_session.AsyncSessionLocal() as session:
                row = SimulatorRun(
                    run_id=run.run_id,
                    scenario_id=run.scenario_id,
                    mode=str(run.mode),
                    state=str(run.state),
                    started_at=run.started_at,
                    stopped_at=run.stopped_at,
                    sim_time_ms=int(run.sim_time_ms) if run.sim_time_ms is not None else None,
                    tick_index=int(run.tick_index) if run.tick_index is not None else None,
                    seed=int(run.seed) if run.seed is not None else None,
                    intensity_percent=int(run.intensity_percent) if run.intensity_percent is not None else None,
                    ops_sec=float(run.ops_sec) if run.ops_sec is not None else None,
                    queue_depth=int(run.queue_depth) if run.queue_depth is not None else None,
                    errors_total=int(run.errors_total) if run.errors_total is not None else None,
                    last_event_type=run.last_event_type,
                    current_phase=run.current_phase,
                    last_error=run.last_error,
                )
                await session.merge(row)
                await session.commit()
        except Exception:
            return

    async def _db_sync_artifacts(self, run: RunRecord) -> None:
        if not self._db_enabled():
            return
        base = run.artifacts_dir
        if base is None or not base.exists():
            return
        try:
            sha_max_bytes = int(os.getenv("SIMULATOR_ARTIFACT_SHA_MAX_BYTES", "524288") or "524288")

            def _content_type(name: str) -> Optional[str]:
                if name.endswith(".ndjson"):
                    return "application/x-ndjson"
                if name.endswith(".json"):
                    return "application/json"
                if name.endswith(".zip"):
                    return "application/zip"
                return None

            def _sha256_file(path: Path) -> Optional[str]:
                try:
                    h = hashlib.sha256()
                    with path.open("rb") as f:
                        while True:
                            chunk = f.read(1024 * 128)
                            if not chunk:
                                break
                            h.update(chunk)
                    return h.hexdigest()
                except Exception:
                    return None

            items: list[SimulatorRunArtifact] = []
            for p in sorted(base.iterdir()):
                if not p.is_file():
                    continue
                url = f"/api/v1/simulator/runs/{run.run_id}/artifacts/{p.name}"
                sha = None
                size = None
                try:
                    size = int(p.stat().st_size)
                    if size <= sha_max_bytes:
                        sha = _sha256_file(p)
                except Exception:
                    pass
                items.append(
                    SimulatorRunArtifact(
                        run_id=run.run_id,
                        name=p.name,
                        content_type=_content_type(p.name),
                        size_bytes=size,
                        sha256=sha,
                        storage_url=url,
                    )
                )

            async with db_session.AsyncSessionLocal() as session:
                await session.execute(delete(SimulatorRunArtifact).where(SimulatorRunArtifact.run_id == run.run_id))
                session.add_all(items)
                await session.commit()
        except Exception:
            return

    async def _db_write_tick_metrics(
        self,
        *,
        run_id: str,
        t_ms: int,
        per_equivalent: dict[str, dict[str, int]],
        metric_values_by_eq: Optional[dict[str, dict[str, float]]] = None,
        db_session=None,
    ) -> None:
        if not self._db_enabled():
            return
        try:
            async def _write(session) -> None:
                for eq, counters in per_equivalent.items():
                    committed = int(counters.get("committed", 0))
                    rejected = int(counters.get("rejected", 0))
                    errors = int(counters.get("errors", 0))
                    timeouts = int(counters.get("timeouts", 0))

                    denom = committed + rejected
                    success_rate = (committed / denom) * 100.0 if denom > 0 else 0.0
                    attempts = committed + rejected + errors
                    bottlenecks_score = ((errors + timeouts) / attempts) * 100.0 if attempts > 0 else 0.0

                    mv = (metric_values_by_eq or {}).get(str(eq), {})
                    avg_route_length = float(mv.get("avg_route_length", 0.0) or 0.0)
                    total_debt = float(mv.get("total_debt", 0.0) or 0.0)
                    clearing_volume = float(mv.get("clearing_volume", 0.0) or 0.0)

                    await session.merge(
                        SimulatorRunMetric(
                            run_id=run_id,
                            equivalent_code=str(eq),
                            key="success_rate",
                            t_ms=int(t_ms),
                            value=float(success_rate),
                        )
                    )
                    await session.merge(
                        SimulatorRunMetric(
                            run_id=run_id,
                            equivalent_code=str(eq),
                            key="bottlenecks_score",
                            t_ms=int(t_ms),
                            value=float(bottlenecks_score),
                        )
                    )
                    await session.merge(
                        SimulatorRunMetric(
                            run_id=run_id,
                            equivalent_code=str(eq),
                            key="avg_route_length",
                            t_ms=int(t_ms),
                            value=float(avg_route_length),
                        )
                    )
                    await session.merge(
                        SimulatorRunMetric(
                            run_id=run_id,
                            equivalent_code=str(eq),
                            key="total_debt",
                            t_ms=int(t_ms),
                            value=float(total_debt),
                        )
                    )
                    await session.merge(
                        SimulatorRunMetric(
                            run_id=run_id,
                            equivalent_code=str(eq),
                            key="clearing_volume",
                            t_ms=int(t_ms),
                            value=float(clearing_volume),
                        )
                    )
                await session.commit()

            if db_session is None:
                async with db_session.AsyncSessionLocal() as session:
                    await _write(session)
            else:
                await _write(db_session)
        except Exception:
            return

    async def _db_write_tick_bottlenecks(
        self,
        *,
        run_id: str,
        equivalent: str,
        computed_at: datetime,
        edge_stats: dict[tuple[str, str], dict[str, int]],
        db_session,
        limit: int = 50,
    ) -> None:
        if not self._db_enabled():
            return
        try:
            # Score in [0..1] from per-edge failure ratio.
            items: list[SimulatorRunBottleneck] = []
            for (src, dst), st in edge_stats.items():
                attempts = int(st.get("attempts", 0))
                if attempts <= 0:
                    continue
                errors = int(st.get("errors", 0))
                timeouts = int(st.get("timeouts", 0))
                rejected = int(st.get("rejected", 0))
                committed = int(st.get("committed", 0))

                bad = errors + timeouts + rejected
                score = max(0.0, min(1.0, float(bad) / float(attempts)))
                if score <= 0:
                    continue

                if timeouts > 0 and float(timeouts) / float(attempts) >= 0.2:
                    reason: str = "TOO_MANY_TIMEOUTS"
                    label = "Too many timeouts"
                    suggested = "Reduce load or increase routing timeouts"
                elif rejected > 0 or errors > 0:
                    reason = "FREQUENT_ABORTS"
                    label = "Frequent failures"
                    suggested = "Increase trust limits, add alternative routes, or clear"
                else:
                    reason = "HIGH_USED"
                    label = "High utilization"
                    suggested = "Consider clearing or adding alternative routes"

                items.append(
                    SimulatorRunBottleneck(
                        run_id=run_id,
                        equivalent_code=str(equivalent),
                        computed_at=computed_at,
                        target_type="edge",
                        target_id=f"{src}->{dst}",
                        score=float(score),
                        reason_code=str(reason),
                        details={
                            "attempts": attempts,
                            "committed": committed,
                            "rejected": rejected,
                            "errors": errors,
                            "timeouts": timeouts,
                            "label": label,
                            "suggested_action": suggested,
                        },
                    )
                )

            items.sort(key=lambda r: (float(r.score), str(r.target_id)), reverse=True)
            items = items[: int(limit)]
            if not items:
                return

            # Insert a new snapshot batch for this computed_at.
            db_session.add_all(items)
            await db_session.commit()
        except Exception:
            try:
                await db_session.rollback()
            except Exception:
                pass
            return

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

    def _write_real_tick_artifact(self, run: RunRecord, payload: dict[str, Any]) -> None:
        base = run.artifacts_dir
        if base is None:
            return
        try:
            (base / "last_tick.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return

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
                    event_id=self._next_event_id(run),
                    ts=_utc_now(),
                    type="clearing.done",
                    equivalent=eq,
                ).model_dump(mode="json")
                run.last_event_type = "clearing.done"
                run.current_phase = None
                self._broadcast(run_id, evt)

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
                self._broadcast(run_id, plan)

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
        self._broadcast(run_id, evt)

    def _maybe_make_tx_updated(self, *, run_id: str, equivalent: str) -> Optional[dict[str, Any]]:
        run = self.get_run(run_id)
        if run._rng is None or run._edges_by_equivalent is None:
            return None
        edges = (run._edges_by_equivalent or {}).get(equivalent) or []
        if not edges:
            return None

        (src, dst) = run._rng.choice(edges)
        evt = SimulatorTxUpdatedEvent(
            event_id=self._next_event_id(run),
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
            event_id=self._next_event_id(run),
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

    def _next_event_id(self, run: RunRecord) -> str:
        with self._lock:
            run._event_seq += 1
            return f"evt_{run.run_id}_{run._event_seq:06d}"

    def _event_seq_from_event_id(self, *, run_id: str, event_id: str) -> Optional[int]:
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

    def _append_to_event_buffer(self, *, run_id: str, payload: dict[str, Any]) -> None:
        run = self.get_run(run_id)
        event_id = str(payload.get("event_id") or "")
        if not event_id:
            return

        seq = self._event_seq_from_event_id(run_id=run_id, event_id=event_id)
        # Only buffer standard monotonically-increasing runtime event ids.
        if seq is None:
            return

        now = time.time()
        event_type = str(payload.get("type") or "")
        event_equivalent = str(payload.get("equivalent") or "")

        with self._lock:
            run._event_buffer.append((now, event_id, event_equivalent if event_type != "run_status" else "", payload))
            self._prune_event_buffer_locked(run, now=now)

    def _prune_event_buffer_locked(self, run: RunRecord, *, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()
        ttl = max(0, int(self._event_buffer_ttl_sec))
        if ttl:
            cutoff = now - ttl
            while run._event_buffer and run._event_buffer[0][0] < cutoff:
                run._event_buffer.popleft()

        max_len = max(1, int(self._event_buffer_max))
        while len(run._event_buffer) > max_len:
            run._event_buffer.popleft()

    def _replay_events(self, *, run_id: str, equivalent: str, after_event_id: str) -> list[dict[str, Any]]:
        run = self.get_run(run_id)
        after_seq = self._event_seq_from_event_id(run_id=run_id, event_id=after_event_id)
        if after_seq is None:
            return []

        with self._lock:
            self._prune_event_buffer_locked(run)
            buf = list(run._event_buffer)

        out: list[dict[str, Any]] = []
        for (_ts, event_id, event_equivalent, payload) in buf:
            seq = self._event_seq_from_event_id(run_id=run_id, event_id=event_id)
            if seq is None or seq <= after_seq:
                continue
            event_type = str(payload.get("type") or "")
            if event_type != "run_status" and event_equivalent != equivalent:
                continue
            out.append(payload)
        return out

    def _broadcast(self, run_id: str, payload: dict[str, Any]) -> None:
        run = self.get_run(run_id)

        # Route: run_status goes to all subscribers, others are filtered by equivalent.
        event_type = str(payload.get("type") or "")
        event_equivalent = str(payload.get("equivalent") or "")

        # Record for best-effort replay.
        self._append_to_event_buffer(run_id=run_id, payload=payload)

        # Best-effort raw events export.
        self._enqueue_event_artifact(run_id=run_id, payload=payload)

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
                # Best-effort drop
                continue


def _new_run_id() -> str:
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    return f"run_{ts}_{secrets.token_hex(4)}"


def _scenario_to_record(raw: dict[str, Any], *, source_path: Optional[Path], created_at: Optional[datetime]) -> ScenarioRecord:
    scenario_id = str(raw.get("scenario_id") or raw.get("id") or "").strip()
    if not scenario_id:
        # fallback to directory name
        scenario_id = source_path.parent.name if source_path is not None else "unknown"

    participants = raw.get("participants") or []
    trustlines = raw.get("trustlines") or []
    equivalents = raw.get("equivalents") or []

    name = raw.get("name")
    return ScenarioRecord(
        scenario_id=scenario_id,
        name=str(name) if name is not None else None,
        created_at=created_at,
        participants_count=int(len(participants)),
        trustlines_count=int(len(trustlines)),
        equivalents=[str(x) for x in equivalents],
        raw=raw,
        source_path=source_path,
    )


def _scenario_to_snapshot(raw: dict[str, Any], *, equivalent: str) -> SimulatorGraphSnapshot:
    participants = raw.get("participants") or []
    trustlines = raw.get("trustlines") or []

    # Nodes
    nodes: list[SimulatorGraphNode] = []
    for p in participants:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        nodes.append(
            SimulatorGraphNode(
                id=pid,
                name=p.get("name"),
                type=p.get("type"),
                status=p.get("status"),
                viz_color_key=_node_color_key(p),
            )
        )

    # Links (only those matching equivalent)
    links: list[SimulatorGraphLink] = []
    for tl in trustlines:
        if str(tl.get("equivalent") or "") != str(equivalent):
            continue
        src = str(tl.get("from") or "")
        dst = str(tl.get("to") or "")
        if not src or not dst:
            continue
        limit = tl.get("limit")
        links.append(
            SimulatorGraphLink(
                source=src,
                target=dst,
                trust_limit=limit,
                used=0,
                available=limit,
                status="active",
                viz_width_key="thin",
                viz_alpha_key="active",
            )
        )

    # links_count
    counts: dict[str, int] = {}
    for l in links:
        counts[l.source] = counts.get(l.source, 0) + 1
        counts[l.target] = counts.get(l.target, 0) + 1
    for n in nodes:
        n.links_count = counts.get(n.id)

    return SimulatorGraphSnapshot(
        equivalent=str(equivalent),
        generated_at=_utc_now(),
        nodes=nodes,
        links=links,
        palette=None,
        limits=None,
    )


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


def _node_color_key(p: dict[str, Any]) -> Optional[str]:
    t = str(p.get("type") or "").strip()
    status = str(p.get("status") or "").strip()
    if status in {"suspended", "left", "deleted"}:
        return status
    if t in {"business", "person"}:
        return t
    return None


def _run_to_status(run: RunRecord) -> RunStatus:
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
        errors_last_1m=None,
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
