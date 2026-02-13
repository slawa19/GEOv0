"""Unit tests for AdaptiveClearingPolicy (§7.1 of the spec).

Covers:
1) no_capacity_rate rises above HIGH → policy activates clearing (respecting cooldown)
2) no_capacity_rate drops below LOW → policy deactivates (hysteresis)
3) clearing_volume ≈ 0 repeatedly → backoff grows
4) in_flight / queue_depth above threshold → guardrail skips clearing
5) budget scaling by pressure
6) cold start / empty window behaviour
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

def _default_config(**overrides) -> AdaptiveClearingPolicyConfig:
    defaults = dict(
        # Use minimal window by default to avoid warmup gating unrelated tests.
        window_ticks=1,
        no_capacity_high=0.60,
        no_capacity_low=0.30,
        min_interval_ticks=3,
        backoff_max_interval_ticks=20,
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


def _feed_ticks(
    state: AdaptiveClearingState,
    eq: str,
    ticks: list[tuple[int, int]],
) -> None:
    """Feed (attempted, rejected_no_capacity) per tick into state."""
    for attempted, rejected in ticks:
        state.record_tick_signals(eq, TickSignals(
            attempted_payments=attempted,
            rejected_no_capacity=rejected,
        ))


# ---------------------------------------------------------------------------
# 1. Activation above HIGH threshold
# ---------------------------------------------------------------------------

class TestActivation:
    def test_activates_when_no_capacity_rate_above_high(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Feed ticks with 70% no_capacity (above 60% HIGH)
        _feed_ticks(state, eq, [(10, 7)] * 5)

        decision = policy.evaluate(eq, state, tick_index=5)
        assert decision.should_run is True
        assert decision.reason == "RATE_HIGH_ENTER"
        assert decision.time_budget_ms is not None
        assert decision.max_depth is not None

    def test_does_not_activate_below_high(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Feed ticks with 40% no_capacity (below 60% HIGH)
        _feed_ticks(state, eq, [(10, 4)] * 5)

        decision = policy.evaluate(eq, state, tick_index=5)
        assert decision.should_run is False
        assert decision.reason == "SKIP_NOT_ACTIVE"

    def test_activates_respecting_cooldown(self):
        cfg = _default_config(min_interval_ticks=5)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # High pressure
        _feed_ticks(state, eq, [(10, 8)] * 5)

        # First evaluation: activates
        d1 = policy.evaluate(eq, state, tick_index=10)
        assert d1.should_run is True

        # Record clearing at tick 10
        state.update_clearing_result(eq, volume=100.0, cost_ms=50.0, tick=10)

        # Tick 12 — within cooldown (< 5 ticks since last clearing)
        _feed_ticks(state, eq, [(10, 8)] * 2)
        d2 = policy.evaluate(eq, state, tick_index=12)
        assert d2.should_run is False
        assert d2.reason == "SKIP_MIN_INTERVAL"

        # Tick 15 — cooldown expired
        _feed_ticks(state, eq, [(10, 8)] * 3)
        d3 = policy.evaluate(eq, state, tick_index=15)
        assert d3.should_run is True


# ---------------------------------------------------------------------------
# 2. Hysteresis: deactivation below LOW
# ---------------------------------------------------------------------------

class TestHysteresis:
    def test_stays_active_between_low_and_high(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "EUR"

        # Activate: rate above HIGH (70%)
        _feed_ticks(state, eq, [(10, 7)] * 5)
        d1 = policy.evaluate(eq, state, tick_index=5)
        assert d1.should_run is True
        state.update_clearing_result(eq, volume=50.0, cost_ms=20.0, tick=5)

        # Now rate drops to 40% (between LOW=30% and HIGH=60%) — should STAY active
        _feed_ticks(state, eq, [(10, 4)] * 10)  # overwrite window
        d2 = policy.evaluate(eq, state, tick_index=15)
        assert d2.should_run is True
        assert d2.reason == "RUN_ACTIVE"

    def test_deactivates_below_low(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "EUR"

        # Activate
        _feed_ticks(state, eq, [(10, 7)] * 10)
        d1 = policy.evaluate(eq, state, tick_index=10)
        assert d1.should_run is True
        state.update_clearing_result(eq, volume=50.0, cost_ms=20.0, tick=10)

        # Drop rate below LOW (20% < 30%)
        _feed_ticks(state, eq, [(10, 2)] * 10)
        d2 = policy.evaluate(eq, state, tick_index=20)
        assert d2.should_run is False
        assert d2.reason == "RATE_LOW_EXIT"


# ---------------------------------------------------------------------------
# 3. Backoff on zero yield
# ---------------------------------------------------------------------------

class TestBackoff:
    def test_backoff_grows_on_zero_volume(self):
        cfg = _default_config(min_interval_ticks=2, backoff_max_interval_ticks=20)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Keep high pressure
        _feed_ticks(state, eq, [(10, 8)] * 10)

        # Initial activation
        d = policy.evaluate(eq, state, tick_index=0)
        assert d.should_run is True

        # First zero-yield clearing at tick 0
        state.update_clearing_result(eq, volume=0.0, cost_ms=50.0, tick=0)
        s = state.get_per_eq_state(eq)
        assert s.consecutive_zero_yield == 1
        backoff = s.backoff_interval
        assert backoff >= cfg.min_interval_ticks

        # Tick within backoff window — should be blocked
        _feed_ticks(state, eq, [(10, 8)] * (backoff - 1))
        d2 = policy.evaluate(eq, state, tick_index=backoff - 1)
        assert d2.should_run is False
        # Could be min-interval or backoff depending on which fires first — both are correct
        assert d2.reason in ("SKIP_MIN_INTERVAL", "SKIP_BACKOFF")

        # After backoff expires — should be allowed again
        _feed_ticks(state, eq, [(10, 8)])
        d3 = policy.evaluate(eq, state, tick_index=backoff + 1)
        assert d3.should_run is True

    def test_backoff_resets_on_positive_volume(self):
        cfg = _default_config(min_interval_ticks=2)
        state = AdaptiveClearingState(cfg)
        eq = "USD"

        # Simulate two zero-yield clearings
        state.update_clearing_result(eq, volume=0.0, cost_ms=50.0, tick=0)
        state.update_clearing_result(eq, volume=0.0, cost_ms=50.0, tick=5)
        s = state.get_per_eq_state(eq)
        assert s.consecutive_zero_yield == 2
        assert s.backoff_interval > 0

        # Positive yield resets
        state.update_clearing_result(eq, volume=100.0, cost_ms=50.0, tick=10)
        assert s.consecutive_zero_yield == 0
        assert s.backoff_interval == 0

    def test_backoff_capped_at_max(self):
        cfg = _default_config(min_interval_ticks=2, backoff_max_interval_ticks=16)
        state = AdaptiveClearingState(cfg)
        eq = "USD"

        for i in range(20):
            state.update_clearing_result(eq, volume=0.0, cost_ms=50.0, tick=i * 50)

        s = state.get_per_eq_state(eq)
        assert s.backoff_interval <= cfg.backoff_max_interval_ticks

    def test_backoff_formula_exact_intervals_with_cap(self):
        cfg = _default_config(min_interval_ticks=3, backoff_max_interval_ticks=20)
        state = AdaptiveClearingState(cfg)
        eq = "USD"

        # streak -> expected interval
        expected = [3, 6, 12, 20, 20]
        for i, want in enumerate(expected, start=1):
            state.update_clearing_result(eq, volume=0.0, cost_ms=50.0, tick=i * 10)
            s = state.get_per_eq_state(eq)
            assert s.consecutive_zero_yield == i
            assert s.backoff_interval == want


class TestMissingData:
    def test_record_tick_signals_treats_none_and_negative_as_zero(self):
        cfg = _default_config(window_ticks=3)
        state = AdaptiveClearingState(cfg)
        eq = "USD"

        state.record_tick_signals(eq, TickSignals(attempted_payments=None, rejected_no_capacity=None))  # type: ignore[arg-type]
        state.record_tick_signals(eq, TickSignals(attempted_payments=-10, rejected_no_capacity=-5))

        s = state.get_per_eq_state(eq)
        assert list(s.window) == [(0, 0), (0, 0)]


# ---------------------------------------------------------------------------
# 4. Guardrails (in_flight / queue_depth) — tested at coordinator level,
#    but we test the config thresholds exist
# ---------------------------------------------------------------------------

class TestGuardrailConfig:
    def test_default_guardrails_disabled(self):
        cfg = _default_config()
        assert cfg.inflight_threshold == 0
        assert cfg.queue_depth_threshold == 0

    def test_guardrails_configurable(self):
        cfg = _default_config(inflight_threshold=50, queue_depth_threshold=100)
        assert cfg.inflight_threshold == 50
        assert cfg.queue_depth_threshold == 100


# ---------------------------------------------------------------------------
# 5. Budget scaling
# ---------------------------------------------------------------------------

class TestBudgetScaling:
    def test_max_pressure_gives_max_budget(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # 100% rejection rate → max pressure
        _feed_ticks(state, eq, [(10, 10)] * 10)
        d = policy.evaluate(eq, state, tick_index=10)
        assert d.should_run is True
        assert d.max_depth == cfg.max_depth_max
        assert d.time_budget_ms == cfg.time_budget_ms_max

    def test_pressure_at_high_gives_max_budget(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Exactly at HIGH threshold (60%) → pressure = 1 (upper boundary)
        _feed_ticks(state, eq, [(100, 60)] * 10)
        d = policy.evaluate(eq, state, tick_index=10)
        assert d.should_run is True
        assert d.max_depth == cfg.max_depth_max
        assert d.time_budget_ms == cfg.time_budget_ms_max

    def test_pressure_at_low_gives_min_budget_when_active(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Force active state so the policy will produce a run decision even at LOW.
        # (Inactive mode only runs when rate >= HIGH.)
        s = state.get_per_eq_state(eq)
        s.active = True
        s.last_clearing_tick = -10_000

        # Exactly at LOW (30%) → pressure = 0
        _feed_ticks(state, eq, [(100, 30)] * 10)
        d = policy.evaluate(eq, state, tick_index=10)
        assert d.should_run is True
        assert d.max_depth == cfg.max_depth_min
        assert d.time_budget_ms == cfg.time_budget_ms_min

    def test_pressure_at_one_gives_max_budget_when_active(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Force active state to test scaling independent from activation threshold.
        s = state.get_per_eq_state(eq)
        s.active = True
        s.last_clearing_tick = -10_000

        # 100% rejection rate → no_capacity_rate=1.0 → pressure clamps to 1
        _feed_ticks(state, eq, [(10, 10)] * 10)
        d = policy.evaluate(eq, state, tick_index=10)
        assert d.should_run is True
        assert d.max_depth == cfg.max_depth_max
        assert d.time_budget_ms == cfg.time_budget_ms_max

    def test_budget_respects_global_ceiling(self):
        cfg = _default_config(
            max_depth_max=10,
            global_max_depth_ceiling=5,
            time_budget_ms_max=500,
            global_time_budget_ms_ceiling=200,
        )
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        _feed_ticks(state, eq, [(10, 10)] * 10)
        d = policy.evaluate(eq, state, tick_index=10)
        assert d.should_run is True
        assert d.max_depth <= cfg.global_max_depth_ceiling
        assert d.time_budget_ms <= cfg.global_time_budget_ms_ceiling


# ---------------------------------------------------------------------------
# 6. Cold start / empty window
# ---------------------------------------------------------------------------

class TestColdStart:
    def test_empty_window_no_clearing(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # No ticks fed → warmup_fallback_cadence=0 → warmup disabled until window fills
        d = policy.evaluate(eq, state, tick_index=0)
        assert d.should_run is False
        assert d.reason == "WARMUP_DISABLED"

    def test_zero_attempted_payments(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Feed ticks with 0 attempted
        _feed_ticks(state, eq, [(0, 0)] * 5)
        d = policy.evaluate(eq, state, tick_index=5)
        assert d.should_run is False

    def test_window_rolling_eviction(self):
        cfg = _default_config(window_ticks=5)
        state = AdaptiveClearingState(cfg)
        eq = "USD"

        # Feed 5 high-rejection ticks
        _feed_ticks(state, eq, [(10, 8)] * 5)
        rate_high = state.get_no_capacity_rate(eq)
        assert rate_high == pytest.approx(0.8)

        # Feed 5 low-rejection ticks — old high ticks should be evicted
        _feed_ticks(state, eq, [(10, 1)] * 5)
        rate_low = state.get_no_capacity_rate(eq)
        assert rate_low == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# 7. Per-eq independence
# ---------------------------------------------------------------------------

class TestPerEqIndependence:
    def test_separate_eq_states(self):
        cfg = _default_config()
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)

        # USD has high pressure, EUR has low
        _feed_ticks(state, "USD", [(10, 8)] * 10)
        _feed_ticks(state, "EUR", [(10, 1)] * 10)

        d_usd = policy.evaluate("USD", state, tick_index=10)
        d_eur = policy.evaluate("EUR", state, tick_index=10)

        assert d_usd.should_run is True
        assert d_eur.should_run is False


# ---------------------------------------------------------------------------
# 8. Cold-start / warmup fallback (§1.2 fix)
# ---------------------------------------------------------------------------

class TestWarmupFallback:
    """When warmup_fallback_cadence > 0 and window is underfilled,
    policy uses static cadence instead of refusing to clear."""

    def test_warmup_fallback_fires_at_cadence(self):
        cfg = _default_config(window_ticks=10, warmup_fallback_cadence=5)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # 3 ticks of HIGH pressure data (window < 10)
        # Warmup fallback must still use MIN budget (no scaling).
        _feed_ticks(state, eq, [(10, 10)] * 3)

        # tick=5 — should fire (5 % 5 == 0)
        d5 = policy.evaluate(eq, state, tick_index=5)
        assert d5.should_run is True
        assert d5.reason == "WARMUP_FALLBACK_RUN"
        assert d5.time_budget_ms == cfg.time_budget_ms_min
        assert d5.max_depth == cfg.max_depth_min

    def test_warmup_fallback_skips_non_cadence_tick(self):
        cfg = _default_config(window_ticks=10, warmup_fallback_cadence=5)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        _feed_ticks(state, eq, [(10, 3)] * 3)

        # tick=3 — not on cadence
        d3 = policy.evaluate(eq, state, tick_index=3)
        assert d3.should_run is False
        assert d3.reason == "WARMUP_FALLBACK_SKIP"

    def test_warmup_fallback_respects_cooldown(self):
        # Use cadence=1 so the fallback would attempt to run every tick,
        # and we can assert that min_interval (cooldown) still blocks it.
        cfg = _default_config(window_ticks=10, warmup_fallback_cadence=1, min_interval_ticks=3)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        _feed_ticks(state, eq, [(10, 3)] * 3)

        # Clearing at tick 0
        state.update_clearing_result(eq, volume=10.0, cost_ms=20.0, tick=0)

        # tick=1 — in cooldown (min_interval=3)
        d1 = policy.evaluate(eq, state, tick_index=1)
        assert d1.should_run is False
        assert d1.reason == "SKIP_MIN_INTERVAL"

    def test_warmup_disabled_when_cadence_zero(self):
        cfg = _default_config(window_ticks=10, warmup_fallback_cadence=0)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Empty window, tick on typical cadence
        d = policy.evaluate(eq, state, tick_index=0)
        assert d.should_run is False
        assert d.reason == "WARMUP_DISABLED"

    def test_warmup_fallback_transitions_to_adaptive(self):
        """After window fills up, fallback stops and adaptive logic takes over."""
        cfg = _default_config(window_ticks=5, warmup_fallback_cadence=5, no_capacity_high=0.50)
        state = AdaptiveClearingState(cfg)
        policy = AdaptiveClearingPolicy(cfg)
        eq = "USD"

        # Fill window to exactly window_ticks
        _feed_ticks(state, eq, [(10, 2)] * 5)

        # tick=10 (on cadence) but window is full → should use adaptive logic
        # 20% rate < 50% HIGH → below_threshold (not warmup_fallback)
        d = policy.evaluate(eq, state, tick_index=10)
        assert d.should_run is False
        assert d.reason == "SKIP_NOT_ACTIVE"


# ---------------------------------------------------------------------------
# 9. Config validation (§3.5 fix)
# ---------------------------------------------------------------------------

class TestConfigValidation:
    def test_valid_config_no_warnings(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _default_config()
        validation_msgs = [r for r in caplog.records if "AdaptiveClearingPolicyConfig validation" in r.message]
        assert len(validation_msgs) == 0

    def test_low_gte_high_warns(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _default_config(no_capacity_low=0.70, no_capacity_high=0.50)
        validation_msgs = [r for r in caplog.records if "no_capacity_low" in r.message and "no_capacity_high" in r.message]
        assert len(validation_msgs) >= 1

    def test_window_ticks_zero_warns(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            cfg = _default_config(window_ticks=0)
        validation_msgs = [r for r in caplog.records if "window_ticks" in r.message]
        assert len(validation_msgs) >= 1
        assert cfg.window_ticks == 1

    def test_budget_min_gt_max_warns(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _default_config(time_budget_ms_min=300, time_budget_ms_max=100)
        validation_msgs = [r for r in caplog.records if "time_budget_ms_min" in r.message]
        assert len(validation_msgs) >= 1
