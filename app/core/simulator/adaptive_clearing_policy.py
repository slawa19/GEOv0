"""Adaptive clearing policy: feedback-control for Real Mode clearing cadence.

Pure logic, no DB/IO. Determines per-equivalent whether clearing should run
based on rolling-window signals (no_capacity_rate, clearing yield, backoff).

See: docs/ru/simulator/backend/adaptive-clearing-policy-spec.md
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# Volume below this threshold is considered "zero yield" for backoff purposes.
ZERO_VOLUME_EPS: float = 1e-9


# ---------------------------------------------------------------------------
# Reason codes (human-readable, stable strings for logs/tests)
# ---------------------------------------------------------------------------

REASON_WARMUP_RUN = "WARMUP_FALLBACK_RUN"
REASON_WARMUP_SKIP = "WARMUP_FALLBACK_SKIP"
REASON_WARMUP_DISABLED = "WARMUP_DISABLED"

REASON_RATE_HIGH_ENTER = "RATE_HIGH_ENTER"
REASON_RATE_LOW_EXIT = "RATE_LOW_EXIT"
REASON_RUN_ACTIVE = "RUN_ACTIVE"

REASON_SKIP_NOT_ACTIVE = "SKIP_NOT_ACTIVE"
REASON_SKIP_MIN_INTERVAL = "SKIP_MIN_INTERVAL"
REASON_SKIP_BACKOFF = "SKIP_BACKOFF"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdaptiveClearingPolicyConfig:
    """All tunables for the adaptive clearing policy."""

    window_ticks: int = 30
    no_capacity_high: float = 0.60
    no_capacity_low: float = 0.30
    min_interval_ticks: int = 5
    backoff_max_interval_ticks: int = 60

    # Guardrails (0 = disabled)
    inflight_threshold: int = 0
    queue_depth_threshold: int = 0

    # Budget clamp bounds (policy output is clamped to these)
    max_depth_min: int = 3
    max_depth_max: int = 6
    time_budget_ms_min: int = 50
    time_budget_ms_max: int = 250

    # Hard ceilings from global env (set by caller from
    # SIMULATOR_CLEARING_MAX_DEPTH / SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS)
    global_max_depth_ceiling: int = 6
    global_time_budget_ms_ceiling: int = 250

    # Cold-start: use static cadence fallback while window is underfilled.
    # 0 = disabled (no fallback; policy decides purely on available data).
    warmup_fallback_cadence: int = 0

    def __post_init__(self) -> None:
        """Validate config invariants and log warnings for dangerous values."""
        warnings: list[str] = []

        # Normalize window size to a safe minimum.
        # NOTE: dataclass is frozen; use object.__setattr__ for normalization.
        if self.window_ticks < 1:
            warnings.append(f"window_ticks ({self.window_ticks}) < 1; will be treated as 1")
            object.__setattr__(self, "window_ticks", 1)

        # Thresholds must satisfy the full invariant: 0 <= low < high <= 1.
        low = float(self.no_capacity_low)
        high = float(self.no_capacity_high)
        if not (0.0 <= low < high <= 1.0):
            warnings.append(
                "invalid thresholds: require 0 <= no_capacity_low < no_capacity_high <= 1; "
                f"got low={low}, high={high}"
            )
        if low >= high:
            warnings.append(
                f"no_capacity_low ({low}) >= no_capacity_high ({high}); "
                "hysteresis band is collapsed — policy will always see max pressure"
            )
        if self.min_interval_ticks < 1:
            warnings.append(f"min_interval_ticks ({self.min_interval_ticks}) < 1; cooldown disabled")

        if self.time_budget_ms_min > self.time_budget_ms_max:
            warnings.append(
                f"time_budget_ms_min ({self.time_budget_ms_min}) > time_budget_ms_max ({self.time_budget_ms_max})"
            )
        if self.max_depth_min > self.max_depth_max:
            warnings.append(
                f"max_depth_min ({self.max_depth_min}) > max_depth_max ({self.max_depth_max})"
            )
        for w in warnings:
            logger.warning("AdaptiveClearingPolicyConfig validation: %s", w)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClearingDecision:
    """Per-equivalent decision returned by the policy."""

    should_run: bool
    reason: str
    time_budget_ms: int | None = None
    max_depth: int | None = None


# ---------------------------------------------------------------------------
# Per-eq tick signals (input to policy.evaluate)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TickSignals:
    """Signals collected for one equivalent during one tick."""

    attempted_payments: int = 0
    rejected_no_capacity: int = 0
    total_debt: float = 0.0


# ---------------------------------------------------------------------------
# Internal per-eq state
# ---------------------------------------------------------------------------

@dataclass
class _PerEqState:
    """Rolling-window state for a single equivalent."""

    # Rolling window of (attempted, rejected_no_capacity) per tick
    window: deque[tuple[int, int]] = field(default_factory=deque)

    # Clearing history
    last_clearing_volume: float = 0.0
    last_clearing_cost_ms: float = 0.0
    last_clearing_tick: int = -1

    # Backoff: current interval grows on consecutive zero-yield clearings
    backoff_interval: int = 0
    consecutive_zero_yield: int = 0

    # Hysteresis: whether we are in "active" (clearing-on) state
    active: bool = False


# ---------------------------------------------------------------------------
# Main state container
# ---------------------------------------------------------------------------

class AdaptiveClearingState:
    """Mutable state for the adaptive policy, lives on the coordinator."""

    def __init__(self, config: AdaptiveClearingPolicyConfig) -> None:
        self._config = config
        self._per_eq: dict[str, _PerEqState] = {}

    def _get(self, eq: str) -> _PerEqState:
        s = self._per_eq.get(eq)
        if s is None:
            s = _PerEqState()
            self._per_eq[eq] = s
        return s

    # -- Called by coordinator to feed tick signals --

    def record_tick_signals(self, eq: str, signals: TickSignals) -> None:
        s = self._get(eq)
        # Be defensive against missing/None values.
        attempted = int(getattr(signals, "attempted_payments", 0) or 0)
        rejected = int(getattr(signals, "rejected_no_capacity", 0) or 0)
        if attempted < 0:
            attempted = 0
        if rejected < 0:
            rejected = 0

        s.window.append((attempted, rejected))
        while len(s.window) > self._config.window_ticks:
            s.window.popleft()

    def update_clearing_result(
        self, eq: str, *, volume: float, cost_ms: float, tick: int
    ) -> None:
        s = self._get(eq)
        s.last_clearing_volume = volume
        s.last_clearing_cost_ms = cost_ms
        s.last_clearing_tick = tick

        if volume < ZERO_VOLUME_EPS:
            s.consecutive_zero_yield += 1
            # Exponential backoff (deterministic):
            # interval = min(max_interval, min_interval * 2**max(0, streak-1))
            # so the first zero-yield does not become more conservative than
            # the base min-interval (cooldown).
            min_interval = max(1, int(self._config.min_interval_ticks))
            max_interval = max(min_interval, int(self._config.backoff_max_interval_ticks))

            exp = max(0, int(s.consecutive_zero_yield) - 1)
            interval = min(max_interval, int(min_interval * (2**exp)))
            s.backoff_interval = max(min_interval, int(interval))
        else:
            s.consecutive_zero_yield = 0
            s.backoff_interval = 0

    # -- Accessors for policy --

    def get_no_capacity_rate(self, eq: str) -> float:
        s = self._get(eq)
        if not s.window:
            return 0.0
        total_attempted = sum(a for a, _ in s.window)
        total_rejected = sum(r for _, r in s.window)
        if total_attempted == 0:
            return 0.0
        return total_rejected / total_attempted

    def get_per_eq_state(self, eq: str) -> _PerEqState:
        return self._get(eq)

    def get_window_fill(self, eq: str) -> int:
        """Number of ticks currently in the rolling window for *eq*."""
        return len(self._get(eq).window)


# ---------------------------------------------------------------------------
# Policy (stateless evaluator — reads state, returns decision)
# ---------------------------------------------------------------------------

class AdaptiveClearingPolicy:
    """Evaluates whether clearing should run for a given equivalent."""

    def __init__(self, config: AdaptiveClearingPolicyConfig) -> None:
        self._config = config

    def evaluate(
        self,
        eq: str,
        state: AdaptiveClearingState,
        tick_index: int,
    ) -> ClearingDecision:
        """Return a ClearingDecision for the given equivalent at the current tick."""

        cfg = self._config
        s = state.get_per_eq_state(eq)
        no_capacity_rate = state.get_no_capacity_rate(eq)

        window_fill = state.get_window_fill(eq)

        # -- Cold-start / warmup fallback --
        # While the window is underfilled, we do NOT run the adaptive hysteresis logic.
        # Instead, we either:
        # - run on a static cadence (when warmup_fallback_cadence > 0), or
        # - skip clearing entirely until the window fills (when warmup_fallback_cadence == 0).
        if window_fill < cfg.window_ticks:
            if cfg.warmup_fallback_cadence <= 0:
                return ClearingDecision(
                    should_run=False,
                    reason=REASON_WARMUP_DISABLED,
                )

            if tick_index % cfg.warmup_fallback_cadence != 0:
                return ClearingDecision(
                    should_run=False,
                    reason=REASON_WARMUP_SKIP,
                )

            # Even in warmup fallback, respect min interval / backoff to prevent
            # hammering clearing.
            blocked = self._check_interval_limits(s, tick_index)
            if blocked is not None:
                return blocked

            # During warmup fallback we MUST use the minimal budget (no scaling
            # by no_capacity_rate) to match the spec.
            return self._make_min_budget_decision(REASON_WARMUP_RUN)

        # -- Hysteresis logic (determines active/inactive state) --
        was_active = bool(s.active)
        if no_capacity_rate >= cfg.no_capacity_high:
            s.active = True
        # Deactivate only when we drop strictly below LOW.
        # (At exactly LOW we keep the previous state to avoid boundary churn
        # and to allow the "pressure=0" budget case while active.)
        elif no_capacity_rate < cfg.no_capacity_low:
            s.active = False

        # If we just exited active state, we skip for this tick.
        if was_active and not s.active:
            return ClearingDecision(
                should_run=False,
                reason=REASON_RATE_LOW_EXIT,
            )

        if not s.active:
            return ClearingDecision(
                should_run=False,
                reason=REASON_SKIP_NOT_ACTIVE,
            )

        # -- Cooldown / backoff check (limits frequency even when active) --
        blocked = self._check_interval_limits(s, tick_index)
        if blocked is not None:
            return blocked

        # Choose a reason depending on whether we just entered active state.
        reason = REASON_RATE_HIGH_ENTER if (not was_active and s.active) else REASON_RUN_ACTIVE
        return self._make_run_decision(no_capacity_rate, reason)

    def _check_interval_limits(
        self,
        s: _PerEqState,
        tick_index: int,
    ) -> ClearingDecision | None:
        """Return a negative decision when min-interval/backoff blocks clearing."""

        cfg = self._config

        if s.last_clearing_tick < 0:
            return None

        ticks_since = tick_index - s.last_clearing_tick

        if ticks_since < cfg.min_interval_ticks:
            return ClearingDecision(
                should_run=False,
                reason=REASON_SKIP_MIN_INTERVAL,
            )

        if s.backoff_interval > 0 and ticks_since < s.backoff_interval:
            return ClearingDecision(
                should_run=False,
                reason=REASON_SKIP_BACKOFF,
            )

        return None

    def _make_run_decision(
        self, no_capacity_rate: float, reason: str
    ) -> ClearingDecision:
        """Build a positive ClearingDecision with budget scaled by pressure."""

        cfg = self._config

        # Scale budgets linearly between low..high thresholds
        # Higher pressure → more budget (depth + time)
        pressure = _normalize(no_capacity_rate, cfg.no_capacity_low, cfg.no_capacity_high)

        raw_depth = cfg.max_depth_min + pressure * (cfg.max_depth_max - cfg.max_depth_min)
        raw_budget = cfg.time_budget_ms_min + pressure * (
            cfg.time_budget_ms_max - cfg.time_budget_ms_min
        )

        # Clamp to configured bounds, then to global ceilings
        effective_depth = _clamp(
            int(raw_depth),
            cfg.max_depth_min,
            min(cfg.max_depth_max, cfg.global_max_depth_ceiling),
        )
        effective_budget = _clamp(
            int(raw_budget),
            cfg.time_budget_ms_min,
            min(cfg.time_budget_ms_max, cfg.global_time_budget_ms_ceiling),
        )

        return ClearingDecision(
            should_run=True,
            reason=reason,
            time_budget_ms=effective_budget,
            max_depth=effective_depth,
        )

    def _make_min_budget_decision(self, reason: str) -> ClearingDecision:
        """Build a positive decision with *minimal* budget (no scaling).

        Used for warmup fallback.
        """

        cfg = self._config

        # Respect both configured min/max and global hard ceilings.
        effective_depth = min(
            int(cfg.max_depth_min),
            int(cfg.max_depth_max),
            int(cfg.global_max_depth_ceiling),
        )
        effective_budget = min(
            int(cfg.time_budget_ms_min),
            int(cfg.time_budget_ms_max),
            int(cfg.global_time_budget_ms_ceiling),
        )

        # Avoid producing invalid non-positive budgets even with misconfig.
        effective_depth = max(1, int(effective_depth))
        effective_budget = max(1, int(effective_budget))

        return ClearingDecision(
            should_run=True,
            reason=reason,
            time_budget_ms=effective_budget,
            max_depth=effective_depth,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _normalize(v: float, lo: float, hi: float) -> float:
    """Normalize v into [0, 1] range between lo and hi."""
    if hi <= lo:
        return 1.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))
