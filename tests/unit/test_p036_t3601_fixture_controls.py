"""036 F-036-3: the stress multipliers of the committed fixtures come from `effects[]` and `metadata.duration_ms`.

The `params` block of a `stress` event ({multiplier, duration_ms, label}) was read by nobody and, in two of the four
events, disagreed with `effects[]` (weekend_market 1.3 against 1.5 for households; harvest_festival 1.8 against 2.0 for
producers). Slice A deleted it from the schema, from both `*-realistic-v2` fixtures and from their generator.

CONTROL (green before and after the deletion, measured on 71c66dda and after it): the multipliers the planner computes
are pinned by VALUE at the middle of each of the four windows, and the whole 5 s grid of both scenarios has 42 points
off the neutral multiplier (a non-vacuity count - a pin on an empty answer would pass for any change).

The validation of the live fixtures against the schema lives in the tooling tier
(`tooling-tests/portable/test_p036_a_scenario_schema_and_the_live_fixtures.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.simulator.real_payment_planner import RealPaymentPlanner

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "simulator"
NEUTRAL = (1.0, {}, {})

#: (sim time at the middle of the window) -> (mult_all, mult_by_group, mult_by_profile)
WINDOWS = {
    95_000: (1.0, {"households": 1.5, "retail": 1.3}, {}),  # weekend_market
    160_000: (0.7, {}, {}),  # quiet_period
    220_000: (1.0, {"producers": 2.0, "retail": 1.8, "households": 1.5}, {}),  # harvest_festival
    257_500: (0.5, {}, {}),  # winter_lull
}


def _events(name: str) -> list[dict]:
    return json.loads((ROOT / name / "scenario.json").read_text(encoding="utf-8"))["events"]


@pytest.mark.parametrize("name", ["riverside-town-50-realistic-v2", "greenfield-village-100-realistic-v2"])
def test_stress_multipliers_are_the_effects_of_the_four_windows(name: str) -> None:
    planner = RealPaymentPlanner.__new__(RealPaymentPlanner)  # the multipliers are a pure function of (events, time)
    events = _events(name)

    for sim_time, expected in WINDOWS.items():
        assert planner.compute_stress_multipliers(events=events, sim_time_ms=sim_time) == expected, sim_time

    off_neutral = [
        t for t in range(0, 320_000, 5_000)
        if planner.compute_stress_multipliers(events=events, sim_time_ms=t) != NEUTRAL
    ]
    assert len(off_neutral) == 21, off_neutral  # non-vacuity: 6 + 4 + 8 + 3 grid points of the four windows
    assert planner.compute_stress_multipliers(events=events, sim_time_ms=0) == NEUTRAL  # outside every window


@pytest.mark.parametrize("name", ["riverside-town-50-realistic-v2", "greenfield-village-100-realistic-v2"])
def test_no_stress_event_carries_the_params_block(name: str) -> None:
    events = [e for e in _events(name) if e.get("type") == "stress"]
    assert len(events) == 4  # non-vacuity
    assert all("params" not in e for e in events), [e["label"] for e in events if "params" in e]
