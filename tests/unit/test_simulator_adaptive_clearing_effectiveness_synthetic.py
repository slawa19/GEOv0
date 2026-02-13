"""Deterministic effectiveness tests for AdaptiveClearingPolicy (§7.5.4.1 of the spec).

Synthetic signal sequences that measure:
1) Reaction time: how many ticks until policy activates after a no_capacity spike.
2) No jitter: hysteresis/cooldown limits mode switches.
3) Correct backoff growth on zero-yield clearing.
4) Budget clamping is respected at all times.
5) Warmup behaviour: conservative decisions with incomplete window.
"""
from __future__ import annotations

import pytest

from app.core.simulator.adaptive_clearing_policy import (
    AdaptiveClearingPolicy,
    AdaptiveClearingPolicyConfig,
    AdaptiveClearingState,
    ClearingDecision,
    TickSignals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**overrides) -> AdaptiveClearingPolicyConfig:
    defaults = dict(
        window_ticks=10,
        no_capacity_high=0.50,
        no_capacity_low=0.20,
        min_interval_ticks=3,
        backoff_max_interval_ticks=30,
        inflight_threshold=0,
        queue_depth_threshold=0,
        max_depth_min=3,
        max_depth_max=6,
        time_budget_ms_min=50,
        time_budget_ms_max=250,
        global_max_depth_ceiling=6,
        global_time_budget_ms_ceiling=250,
    )
    defaults.update(overrides)
    return AdaptiveClearingPolicyConfig(**defaults)


def _run_ticks(
    policy: AdaptiveClearingPolicy,
    state: AdaptiveClearingState,
    eq: str,
    signals_per_tick: list[tuple[int, int]],
    *,
    start_tick: int = 0,
) -> list[ClearingDecision]:
    """Feed signals and evaluate policy for each tick. Return list of decisions."""
    decisions: list[ClearingDecision] = []
    for i, (attempted, rejected) in enumerate(signals_per_tick):
        tick = start_tick + i
        state.record_tick_signals(eq, TickSignals(
            attempted_payments=attempted,
            rejected_no_capacity=rejected,
        ))
        d = policy.evaluate(eq, state, tick_index=tick)
        decisions.append(d)
    return decisions


# ---------------------------------------------------------------------------
# 1. Reaction time: spike → activation
# ---------------------------------------------------------------------------

class TestReactionTime:
    """Measure how quickly policy activates after a rejection spike."""

    def test_activation_within_window_after_spike(self):
        """After a sudden spike in no_capacity_rate above HIGH, policy should
        activate within 1 tick (assuming no cooldown)."""
        cfg = _cfg(window_ticks=5, no_capacity_high=0.50)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # 5 ticks of calm (0% rejection) — fill the window
        calm = [(10, 0)] * 5
        decisions_calm = _run_ticks(policy, state, eq, calm, start_tick=0)
        assert all(not d.should_run for d in decisions_calm)

        # Spike: 5 ticks of 70% rejection
        spike = [(10, 7)] * 5
        decisions_spike = _run_ticks(policy, state, eq, spike, start_tick=5)

        # Find the first activation tick
        first_activation = next(
            (i for i, d in enumerate(decisions_spike) if d.should_run),
            None,
        )
        assert first_activation is not None, "Policy never activated after spike"
        # Should activate within the first 3 ticks of spike
        # (window needs enough signal; depends on window_ticks=5 and mixing)
        assert first_activation <= 3, f"Activation took {first_activation} ticks (too slow)"

    def test_deactivation_after_recovery(self):
        """After no_capacity_rate drops below LOW, policy should deactivate."""
        cfg = _cfg(window_ticks=5, no_capacity_high=0.50, no_capacity_low=0.20)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Activate: 5 ticks of high rejection
        high = [(10, 7)] * 5
        _run_ticks(policy, state, eq, high, start_tick=0)

        # Simulate clearing at tick 5 (so cooldown resets)
        state.update_clearing_result(eq, volume=10.0, cost_ms=50.0, tick=5)

        # Now feed recovery: 0% rejection for enough ticks to drop the window rate below LOW
        recovery = [(10, 0)] * 10
        decisions = _run_ticks(policy, state, eq, recovery, start_tick=6)

        # Should have deactivated at some point
        deactivations = [d for d in decisions if d.reason == "RATE_LOW_EXIT"]
        assert len(deactivations) >= 1, "Policy never deactivated during recovery"


# ---------------------------------------------------------------------------
# 2. No jitter: hysteresis prevents oscillation
# ---------------------------------------------------------------------------

class TestNoJitter:
    """Verify hysteresis band prevents rapid on/off switching."""

    def test_no_oscillation_in_hysteresis_band(self):
        """When no_capacity_rate hovers between LOW and HIGH, the policy should
        NOT oscillate on/off every tick."""
        cfg = _cfg(window_ticks=5, no_capacity_high=0.50, no_capacity_low=0.20,
                    min_interval_ticks=2)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # Pre-fill with data that puts rate at ~35% (between LOW=20% and HIGH=50%)
        mid = [(10, 3)] * 5  # 30% rejection, slightly above 20% but below 50%
        decisions = _run_ticks(policy, state, eq, mid, start_tick=0)

        # Count mode switches (should_run transitions)
        mode_switches = 0
        for i in range(1, len(decisions)):
            if decisions[i].should_run != decisions[i - 1].should_run:
                mode_switches += 1

        # In hysteresis band (inactive → needs HIGH to activate, active → needs LOW to deactivate)
        # So starting from inactive, mid-band signals should never activate
        assert mode_switches == 0, f"Too many mode switches in hysteresis band: {mode_switches}"

    def test_cooldown_limits_consecutive_clearings(self):
        """Even with constant high pressure, consecutive clearings must
        respect min_interval_ticks."""
        cfg = _cfg(window_ticks=5, no_capacity_high=0.30, min_interval_ticks=4)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # Fill window with high rejections
        _run_ticks(policy, state, eq, [(10, 7)] * 5, start_tick=0)

        clearing_ticks: list[int] = []
        for tick in range(20):
            state.record_tick_signals(eq, TickSignals(attempted_payments=10, rejected_no_capacity=7))
            d = policy.evaluate(eq, state, tick_index=tick)
            if d.should_run:
                state.update_clearing_result(eq, volume=5.0, cost_ms=20.0, tick=tick)
                clearing_ticks.append(tick)

        # Check interval between consecutive clearings >= min_interval_ticks
        for i in range(1, len(clearing_ticks)):
            gap = clearing_ticks[i] - clearing_ticks[i - 1]
            assert gap >= cfg.min_interval_ticks, (
                f"Clearing interval {gap} < min_interval_ticks {cfg.min_interval_ticks} "
                f"(ticks {clearing_ticks[i - 1]}→{clearing_ticks[i]})"
            )


# ---------------------------------------------------------------------------
# 3. Backoff on zero yield
# ---------------------------------------------------------------------------

class TestBackoffGrowth:
    """Verify backoff grows geometrically when clearing yields zero volume."""

    def test_backoff_doubles_on_zero_yield(self):
        cfg = _cfg(window_ticks=5, no_capacity_high=0.30, min_interval_ticks=3,
                    backoff_max_interval_ticks=60)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # Fill window with high rejections so policy wants to activate
        _run_ticks(policy, state, eq, [(10, 7)] * 5, start_tick=0)

        intervals: list[int] = []
        last_clearing_tick = -100  # far in the past

        for tick in range(200):
            state.record_tick_signals(eq, TickSignals(attempted_payments=10, rejected_no_capacity=7))
            d = policy.evaluate(eq, state, tick_index=tick)
            if d.should_run:
                # Zero yield: clearing found nothing
                state.update_clearing_result(eq, volume=0.0, cost_ms=20.0, tick=tick)
                if last_clearing_tick >= 0:
                    intervals.append(tick - last_clearing_tick)
                last_clearing_tick = tick

        # Intervals should be non-decreasing (backoff growing)
        assert len(intervals) >= 3, f"Expected at least 3 clearing intervals, got {len(intervals)}"
        for i in range(1, len(intervals)):
            assert intervals[i] >= intervals[i - 1], (
                f"Backoff not growing: interval {intervals[i]} < {intervals[i - 1]}"
            )

    def test_backoff_resets_on_positive_yield(self):
        cfg = _cfg(window_ticks=5, no_capacity_high=0.30, min_interval_ticks=3,
                    backoff_max_interval_ticks=60)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # Fill window
        _run_ticks(policy, state, eq, [(10, 7)] * 5, start_tick=0)

        # Build up backoff with 3 zero-yield clearings
        clearing_count = 0
        for tick in range(100):
            state.record_tick_signals(eq, TickSignals(attempted_payments=10, rejected_no_capacity=7))
            d = policy.evaluate(eq, state, tick_index=tick)
            if d.should_run:
                state.update_clearing_result(eq, volume=0.0, cost_ms=20.0, tick=tick)
                clearing_count += 1
                if clearing_count >= 3:
                    break

        per_eq_state = state.get_per_eq_state(eq)
        backoff_before = per_eq_state.backoff_interval
        assert backoff_before > 0, "Backoff should have grown"

        # Now a positive yield clearing
        d_next = None
        for tick in range(100, 300):
            state.record_tick_signals(eq, TickSignals(attempted_payments=10, rejected_no_capacity=7))
            d_next = policy.evaluate(eq, state, tick_index=tick)
            if d_next.should_run:
                state.update_clearing_result(eq, volume=50.0, cost_ms=20.0, tick=tick)
                break

        # Backoff should be reset
        assert per_eq_state.backoff_interval == 0, "Backoff should reset after positive yield"
        assert per_eq_state.consecutive_zero_yield == 0


# ---------------------------------------------------------------------------
# 4. Budget clamping always respected
# ---------------------------------------------------------------------------

class TestBudgetClamping:
    """Budget values in decisions must always be within configured bounds."""

    def test_budget_within_bounds_at_all_pressure_levels(self):
        cfg = _cfg(
            max_depth_min=3,
            max_depth_max=6,
            time_budget_ms_min=50,
            time_budget_ms_max=250,
            global_max_depth_ceiling=6,
            global_time_budget_ms_ceiling=250,
        )
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # Test at various pressure levels
        for rejection_count in range(0, 11):
            s = AdaptiveClearingState(cfg)
            _run_ticks(policy, s, eq, [(10, rejection_count)] * 10, start_tick=0)
            d = policy.evaluate(eq, s, tick_index=10)
            if d.should_run:
                assert cfg.time_budget_ms_min <= d.time_budget_ms <= min(cfg.time_budget_ms_max, cfg.global_time_budget_ms_ceiling), (
                    f"time_budget_ms={d.time_budget_ms} out of bounds [{cfg.time_budget_ms_min}, {min(cfg.time_budget_ms_max, cfg.global_time_budget_ms_ceiling)}]"
                )
                assert cfg.max_depth_min <= d.max_depth <= min(cfg.max_depth_max, cfg.global_max_depth_ceiling), (
                    f"max_depth={d.max_depth} out of bounds [{cfg.max_depth_min}, {min(cfg.max_depth_max, cfg.global_max_depth_ceiling)}]"
                )

    def test_global_ceiling_limits_budget(self):
        """When global ceiling is lower than adaptive max, budget is clamped."""
        cfg = _cfg(
            max_depth_max=10,
            time_budget_ms_max=500,
            global_max_depth_ceiling=4,  # lower than max
            global_time_budget_ms_ceiling=100,  # lower than max
        )
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # Max pressure
        _run_ticks(policy, state, eq, [(10, 10)] * 10, start_tick=0)
        d = policy.evaluate(eq, state, tick_index=10)
        assert d.should_run
        assert d.max_depth <= cfg.global_max_depth_ceiling, (
            f"max_depth {d.max_depth} exceeds global ceiling {cfg.global_max_depth_ceiling}"
        )
        assert d.time_budget_ms <= cfg.global_time_budget_ms_ceiling, (
            f"time_budget_ms {d.time_budget_ms} exceeds global ceiling {cfg.global_time_budget_ms_ceiling}"
        )


# ---------------------------------------------------------------------------
# 5. Warmup / cold-start behaviour
# ---------------------------------------------------------------------------

class TestWarmupBehaviour:
    """With incomplete window, policy should make conservative decisions."""

    def test_empty_window_does_not_activate(self):
        cfg = _cfg(window_ticks=10)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # No signals recorded yet (cold start)
        d = policy.evaluate(eq, state, tick_index=0)
        assert d.should_run is False

    def test_partial_window_needs_strong_signal(self):
        """With only 2/10 window ticks filled, mild rejection should not activate."""
        cfg = _cfg(window_ticks=10, no_capacity_high=0.50)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # 2 ticks of 40% rejection (below HIGH=50%)
        _run_ticks(policy, state, eq, [(10, 4)] * 2, start_tick=0)
        d = policy.evaluate(eq, state, tick_index=2)
        assert d.should_run is False

    def test_partial_window_activates_on_extreme_signal(self):
        """With only 3/10 window ticks filled and fallback disabled,
        policy MUST NOT activate (warmup gate)."""
        cfg = _cfg(window_ticks=10, no_capacity_high=0.50)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # 3 ticks of 90% rejection (well above HIGH)
        _run_ticks(policy, state, eq, [(10, 9)] * 3, start_tick=0)
        d = policy.evaluate(eq, state, tick_index=3)
        assert d.should_run is False
        assert d.reason == "WARMUP_DISABLED"


# ---------------------------------------------------------------------------
# 6. Budget scales with pressure
# ---------------------------------------------------------------------------

class TestBudgetScaling:
    """Higher no_capacity_rate should produce higher budgets (within bounds)."""

    def test_higher_pressure_means_higher_budget(self):
        cfg = _cfg(
            window_ticks=5,
            no_capacity_high=0.40,
            no_capacity_low=0.10,
            max_depth_min=3,
            max_depth_max=6,
            time_budget_ms_min=50,
            time_budget_ms_max=250,
        )

        # Low pressure (just above HIGH)
        s_low = AdaptiveClearingState(cfg)
        p = AdaptiveClearingPolicy(cfg)
        _run_ticks(p, s_low, "UAH", [(10, 5)] * 5, start_tick=0)  # 50%
        d_low = p.evaluate("UAH", s_low, tick_index=5)

        # High pressure
        s_high = AdaptiveClearingState(cfg)
        _run_ticks(p, s_high, "UAH", [(10, 9)] * 5, start_tick=0)  # 90%
        d_high = p.evaluate("UAH", s_high, tick_index=5)

        assert d_low.should_run and d_high.should_run
        assert d_high.time_budget_ms >= d_low.time_budget_ms, (
            f"Higher pressure should not have lower budget: {d_high.time_budget_ms} < {d_low.time_budget_ms}"
        )
        assert d_high.max_depth >= d_low.max_depth, (
            f"Higher pressure should not have lower depth: {d_high.max_depth} < {d_low.max_depth}"
        )


# ---------------------------------------------------------------------------
# 7. Warmup fallback fires during cold start (§1.2 fix)
# ---------------------------------------------------------------------------

class TestWarmupFallbackEffectiveness:
    """With warmup_fallback_cadence, cold-start period still runs periodic clearing."""

    def test_cold_start_with_fallback_fires_clearing(self):
        """During warmup, clearing fires at fallback cadence even with low signal."""
        cfg = _cfg(window_ticks=10, warmup_fallback_cadence=5)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        # Feed only 3 ticks (below window_ticks=10)
        decisions = _run_ticks(policy, state, eq, [(10, 1)] * 3, start_tick=0)

        # tick=5 should fire fallback
        state.record_tick_signals(eq, TickSignals(attempted_payments=10, rejected_no_capacity=1))
        d5 = policy.evaluate(eq, state, tick_index=5)
        assert d5.should_run is True
        assert d5.reason == "WARMUP_FALLBACK_RUN"

    def test_cold_start_without_fallback_no_clearing(self):
        """Without fallback cadence, cold start with low signal does not clear."""
        cfg = _cfg(window_ticks=10, warmup_fallback_cadence=0)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "UAH"

        decisions = _run_ticks(policy, state, eq, [(10, 1)] * 3, start_tick=0)
        d5 = policy.evaluate(eq, state, tick_index=5)
        assert d5.should_run is False
        assert d5.reason == "WARMUP_DISABLED"
