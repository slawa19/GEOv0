from __future__ import annotations

import asyncio
import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
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
class EdgeClearingHistory:
    """Per-edge clearing history for trust drift calculation."""

    original_limit: Decimal  # лимит при создании trustline
    clearing_count: int = 0  # кол-во клирингов через это ребро
    last_clearing_tick: int = -1  # тик последнего клиринга
    cleared_volume: Decimal = Decimal("0")  # суммарный объём клиринга


@dataclass
class TrustDriftConfig:
    """Trust drift parameters from scenario settings."""

    enabled: bool = False
    growth_rate: float = 0.05
    decay_rate: float = 0.02
    max_growth: float = 2.0
    min_limit_ratio: float = 0.3
    overload_threshold: float = 0.8

    @classmethod
    def from_scenario(cls, scenario: dict) -> "TrustDriftConfig":
        """Parse trust_drift from scenario settings."""
        settings = scenario.get("settings", {})
        td = settings.get("trust_drift", {})
        if not td or not td.get("enabled", False):
            return cls(enabled=False)
        return cls(
            enabled=True,
            growth_rate=td.get("growth_rate", 0.05),
            decay_rate=td.get("decay_rate", 0.02),
            max_growth=td.get("max_growth", 2.0),
            min_limit_ratio=td.get("min_limit_ratio", 0.3),
            overload_threshold=td.get("overload_threshold", 0.8),
        )


@dataclass
class RunRecord:
    run_id: str
    scenario_id: str
    mode: RunMode
    state: RunState

    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None

    # Best-effort stop context for diagnostics/UI.
    stop_requested_at: Optional[datetime] = None
    stop_source: Optional[str] = None
    stop_reason: Optional[str] = None
    stop_client: Optional[str] = None

    sim_time_ms: int = 0
    tick_index: int = 0
    seed: int = 0
    intensity_percent: int = 0
    ops_sec: float = 0.0
    queue_depth: int = 0

    errors_total: int = 0
    committed_total: int = 0
    rejected_total: int = 0
    attempts_total: int = 0
    timeouts_total: int = 0
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

    # Per-run scenario raw dict (deep-copied from ScenarioRecord.raw on run creation).
    # Used to avoid cross-run conflicts when the runner mutates scenario topology in-memory.
    _scenario_raw: dict[str, Any] | None = None

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

    # Consecutive ticks where all planned payments were rejected (capacity stall).
    _real_consec_all_rejected_ticks: int = 0

    # Real-mode best-effort persistence flush (in-memory only).
    _real_last_tick_storage_payload: dict[str, Any] | None = None
    _real_last_tick_storage_flushed_tick: int = -1

    # Real-mode per-equivalent viz quantile cache for SSE node_patch/edge_patch.
    _real_viz_by_eq: dict[str, Any] = field(default_factory=dict)

    # Real-mode cached total debt snapshot (throttled aggregate query).
    _real_total_debt_by_eq: dict[str, float] = field(default_factory=dict)
    _real_total_debt_tick: int = -1

    # Real-mode logging throttle state (avoid log spam within one tick).
    # Stored on the run to avoid unbounded per-run dictionaries in RealRunner.
    _real_warned_tick: int = -(10**9)
    _real_warned_keys: set[str] = field(default_factory=set)

    # Real-mode scenario timeline events state (best-effort, in-memory).
    _real_fired_scenario_event_indexes: set[int] = field(default_factory=set)

    # Real-mode clearing execution can be slow and may be guarded by timeouts.
    # Keep track of an in-flight clearing task to prevent overlapping clearing runs.
    _real_clearing_task: Optional[asyncio.Task[dict[str, float]]] = None

    # Trust drift: per-edge clearing history and config.
    # Key format: "{creditor_pid}:{debtor_pid}:{equivalent_code}"
    _edge_clearing_history: dict[str, EdgeClearingHistory] = field(
        default_factory=dict
    )
    _trust_drift_config: TrustDriftConfig | None = None
