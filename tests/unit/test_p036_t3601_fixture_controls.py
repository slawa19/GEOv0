"""036 `T3601`: controls over the committed scenario fixtures (green now, must stay green through slices A-C).

NOT reproducers: they pin what the slices must NOT move, and give F-036-1/-3/-5 their measured "before":

* F-036-1: no LIVE fixture (`fixtures/simulator/*/scenario.json`, the ones the runtime loads) carries a string event time.
  The spec says "фикстуры со строками не существуют"; that is TRUE of the live set and FALSE of the archive: the
  archived `_archive/golden-7_2-like` has one `stress` event with `time: "day_10"` (measured 2026-10-08). The runtime
  skips `_`-directories (`scenario_registry.py:287`) and the archive is read-only (AGENTS.md §3), so slice A, which
  drops the token from the schema, makes that file fail the schema; any validation guard must therefore scan the live
  set only. The archive's string time is pinned below as data, so the exception is a recorded fact, not a surprise.
* F-036-5: every committed fixture validates against `scenario.schema.json` today (nothing validates them at load -
  `scenario_registry.py:277-297`), and the validator DOES refuse a broken field (the anti-vacuum control: a guard that
  cannot fail proves nothing). The spec puts this guard in the tooling tier; adding a case there moves
  `EXPECTED_CASES` in `tooling-tests/conftest.py`, which belongs to slice A, so it lives here until then.
* F-036-3: removing `params` from a `stress` event does not move the multipliers the planner computes (it reads
  `effects[]` and `metadata.duration_ms` only) - asserted on VALUES at a time inside each window, with a non-vacuity
  assertion that a multiplier other than 1.0 really came out.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.core.simulator.real_payment_planner import RealPaymentPlanner

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "simulator"
SCHEMA = json.loads((ROOT / "scenario.schema.json").read_text(encoding="utf-8"))
FIXTURES = sorted(ROOT.glob("*/scenario.json"))  # live: the runtime loads these
ARCHIVED = sorted(ROOT.glob("_archive/*/scenario.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_fixture_set_is_not_empty_and_includes_the_stress_scenarios() -> None:
    names = {p.parent.name for p in FIXTURES}
    assert len(FIXTURES) == 5, [p.parent.name for p in FIXTURES]  # 5 live on 75dafc82 (+ 6 archived, below)
    assert len(ARCHIVED) == 6, [p.parent.name for p in ARCHIVED]
    assert {"riverside-town-50-realistic-v2", "greenfield-village-100-realistic-v2"} <= names


def test_the_only_string_event_time_in_the_tree_is_in_the_archive() -> None:
    """Pins the measured exception to the spec's "фикстуры со строками не существуют" (F-036-1)."""

    with_tokens = {
        p.parent.name: [e["time"] for e in _load(p).get("events", []) if not isinstance(e.get("time"), int)]
        for p in ARCHIVED
    }
    assert {k: v for k, v in with_tokens.items() if v} == {"golden-7_2-like": ["day_10"]}, with_tokens


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.parent.name)
def test_no_fixture_event_has_a_string_time(path: Path) -> None:
    times = [e.get("time") for e in _load(path).get("events", [])]
    assert all(isinstance(t, int) and not isinstance(t, bool) for t in times), times


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.parent.name)
def test_every_committed_fixture_validates_against_the_schema(path: Path) -> None:
    errors = [(list(e.path), e.message[:100]) for e in Draft202012Validator(SCHEMA).iter_errors(_load(path))]
    assert errors == [], errors


def test_the_schema_validator_refuses_a_broken_field() -> None:
    """Anti-vacuum for the guard above: the same validator fails a fixture with a field the schema forbids."""

    broken = _load(ROOT / "clearing-demo-10" / "scenario.json")
    broken["trustlines"][0]["not_a_field"] = 1
    errors = list(Draft202012Validator(SCHEMA).iter_errors(broken))
    assert errors, "the validator accepted a trust line with an unknown field"


def _stress_events(path: Path) -> list[dict]:
    return [e for e in _load(path)["events"] if e.get("type") == "stress"]


@pytest.mark.parametrize("name", ["riverside-town-50-realistic-v2", "greenfield-village-100-realistic-v2"])
def test_stress_multipliers_do_not_depend_on_the_params_block(name: str) -> None:
    planner = RealPaymentPlanner.__new__(RealPaymentPlanner)  # the multipliers are a pure function of (events, time)
    events = _load(ROOT / name / "scenario.json")["events"]
    stripped = deepcopy(events)
    for evt in stripped:
        evt.pop("params", None)
    assert any("params" in e for e in events), "non-vacuity: the fixture has params to strip"

    moved = 0
    for evt in _stress_events(ROOT / name / "scenario.json"):
        t = int(evt["time"]) + int(evt["metadata"]["duration_ms"]) // 2  # inside the window
        with_params = planner.compute_stress_multipliers(events=events, sim_time_ms=t)
        without = planner.compute_stress_multipliers(events=stripped, sim_time_ms=t)
        assert with_params == without, (evt.get("label"), with_params, without)
        if with_params != (1.0, {}, {}):
            moved += 1
    assert moved == 4, moved  # non-vacuity: all four windows produce a multiplier other than 1.0
