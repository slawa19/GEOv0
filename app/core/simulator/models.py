from __future__ import annotations

import asyncio
import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.schemas.simulator import RunMode, RunState, ScenarioSummary


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
        # Import locally to avoid import-time cycles.
        from app.schemas.simulator import SIMULATOR_API_VERSION

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

    # Best-effort rolling window for errors_last_1m.
    _error_timestamps: "deque[float]" = field(default_factory=deque)

    # Storage persistence throttling state (in-memory only).
    _persist_last_at_ms: int = 0
    _persist_last_state: str = ""
    _persist_last_sig: tuple[Any, ...] | None = None

    _event_seq: int = 0
    _subs: list[_Subscription] = field(default_factory=list)
    _heartbeat_task: Optional[asyncio.Task[None]] = None

    # Best-effort in-memory replay buffer for SSE reconnects.
    # Stores recent emitted events (both run_status and domain events).
    _event_buffer: "deque[tuple[float, str, str, dict[str, Any]]]" = field(
        default_factory=deque
    )

    _rng: random.Random | None = None
    _edges_by_equivalent: dict[str, list[tuple[str, str]]] | None = None
    _next_tx_at_ms: int = 0
    _next_clearing_at_ms: int = 0
    _clearing_pending_done_at_ms: int | None = None
    _clearing_pending_plan_id_by_eq: dict[str, str] = field(default_factory=dict)

    artifacts_dir: Optional[Path] = None

    # Artifacts writer (events.ndjson). Best-effort and async-safe.
    _artifact_events_queue: "asyncio.Queue[Optional[str]]" | None = None
    _artifact_events_task: Optional[asyncio.Task[None]] = None

    # Real-mode artifact IO throttling (in-memory only).
    _artifact_last_tick_written_at_ms: int = 0
    _artifact_last_sync_at_ms: int = 0

    # Real-mode in-process runner state (best-effort MVP).
    _real_seeded: bool = False
    _real_participants: list[tuple[uuid.UUID, str]] | None = None
    _real_equivalents: list[str] | None = None

    _real_max_in_flight: int = 0
    _real_in_flight: int = 0
    _real_consec_tick_failures: int = 0

    # Real-mode best-effort persistence flush (in-memory only).
    _real_last_tick_storage_payload: dict[str, Any] | None = None
    _real_last_tick_storage_flushed_tick: int = -1

    # Real-mode per-equivalent viz quantile cache for SSE node_patch/edge_patch.
    _real_viz_by_eq: dict[str, Any] = field(default_factory=dict)

    # Real-mode logging throttle state (avoid log spam within one tick).
    # Stored on the run to avoid unbounded per-run dictionaries in RealRunner.
    _real_warned_tick: int = -(10**9)
    _real_warned_keys: set[str] = field(default_factory=set)

    # Real-mode scenario timeline events state (best-effort, in-memory).
    _real_fired_scenario_event_indexes: set[int] = field(default_factory=set)
