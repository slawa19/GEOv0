"""036 `T3601` / F-036-3: the stress multipliers of the committed fixtures come from `effects[]` and
`metadata.duration_ms` alone - the `params` block of a `stress` event is read by nobody.

CONTROL (green now, must stay green through the cleanup of `params`): removing `params` from a `stress` event does not
move the multipliers the planner computes - asserted on VALUES at a time inside each window, with a non-vacuity
assertion that a multiplier other than 1.0 really came out.

The validation of the live fixtures against the schema and the string-time facts live in the tooling tier
(`tooling-tests/portable/test_p036_a_scenario_schema_and_the_live_fixtures.py`, 036 slice A), where the spec puts them.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.core.simulator.real_payment_planner import RealPaymentPlanner

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "simulator"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["riverside-town-50-realistic-v2", "greenfield-village-100-realistic-v2"])
def test_stress_multipliers_do_not_depend_on_the_params_block(name: str) -> None:
    planner = RealPaymentPlanner.__new__(RealPaymentPlanner)  # the multipliers are a pure function of (events, time)
    scenario = _load(ROOT / name / "scenario.json")
    events = scenario["events"]
    stripped = deepcopy(events)
    for evt in stripped:
        evt.pop("params", None)

    moved = 0
    for evt in [e for e in events if e.get("type") == "stress"]:
        t = int(evt["time"]) + int(evt["metadata"]["duration_ms"]) // 2  # inside the window
        with_params = planner.compute_stress_multipliers(events=events, sim_time_ms=t)
        without = planner.compute_stress_multipliers(events=stripped, sim_time_ms=t)
        assert with_params == without, (evt.get("label"), with_params, without)
        if with_params != (1.0, {}, {}):
            moved += 1
    assert moved == 4, moved  # non-vacuity: all four windows produce a multiplier other than 1.0
