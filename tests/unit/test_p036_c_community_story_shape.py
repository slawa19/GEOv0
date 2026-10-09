"""036 slice C (T3630): the SHAPE of `community-story-10` and where the product exposes it. No database.

What this guards, each from the spec (`specs/036-narrative-demo-scenarios/spec.md`, "Фикстура"): 10-12 participants, 8-12
episodes, one equivalent, 2-4 minutes at a `tick_seconds` of 2-3; every event is an episode (a caption in both languages); the
story is served by `build_story`; it is in the default allowlist and in the registry.

THE DESIGN RULE THE EXECUTION TEST DEPENDS ON (B1 remainder 036-2 (б), declared in the scenario document): the PERIODIC
clearing of a run (every `SIMULATOR_CLEARING_EVERY_N_TICKS`-th tick, a process setting a scenario cannot switch off) may take a
cycle that belongs to an episode. So between the last debt-moving episode before a scripted `clearing` and that clearing there
is no periodic-clearing tick. `_stolen` states the rule over any scenario; the story satisfies it, and a copy with the clearing
moved across a periodic tick does not (anti-vacuum: the rule is shown to fire).

NOT SEEN HERE. That the story runs (`tests/integration/test_p036_c_community_story_postgres.py`) and that it is valid against
the JSON schema (`tooling-tests/portable/test_p036_a_scenario_schema_and_the_live_fixtures.py`).
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.runtime_impl import _scenario_allowlist
from app.core.simulator.scenario_story import build_story, story_errors

SCENARIO_ID = "community-story-10"
STORY = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "simulator" / SCENARIO_ID / "scenario.json").read_text(encoding="utf-8"))
PERIOD = 25  # the process default of `SIMULATOR_CLEARING_EVERY_N_TICKS`; the execution test asserts the runtime's is the same
DEBT_MOVING = {"payment", "inject"}


def _tick(event: dict) -> int:
    return event["time"] // 1000  # one tick of sim time is `SIMULATOR_TICK_MS_BASE` = 1000 ms


def _stolen(scenario: dict, period: int = PERIOD) -> list[tuple[int, int]]:
    """(clearing index, periodic tick) for every scripted clearing that a periodic clearing could pre-empt."""

    events = scenario["events"]
    found = []
    for i, e in enumerate(events):
        if e["type"] != "clearing":
            continue
        earlier = [_tick(x) for x in events[:i] if x["type"] in DEBT_MOVING]
        if not earlier:
            continue
        first, clearing_tick = max(earlier), _tick(e)
        found.extend((i, t) for t in range(first, clearing_tick) if t > 0 and t % period == 0)
    return found


def test_the_story_has_the_size_the_spec_asks_for() -> None:
    events, injected = STORY["events"], [
        eff["participant"]["id"] for e in STORY["events"] for eff in e.get("effects", []) if eff.get("op") == "add_participant"
    ]
    minutes = _tick(events[-1]) * STORY["settings"]["playback"]["tick_seconds"] / 60

    assert 10 <= len(STORY["participants"]) + len(injected) <= 12, (len(STORY["participants"]), injected)
    assert 8 <= len(events) <= 12, len(events)
    assert STORY["equivalents"] == ["UAH"] and {tl["equivalent"] for tl in STORY["trustlines"]} == {"UAH"}
    assert 2 <= STORY["settings"]["playback"]["tick_seconds"] <= 3
    assert 2 <= minutes <= 4, minutes  # at the story's own pace, pauses not counted
    assert STORY["settings"]["playback"]["intensity_percent"] == 0  # no background payment moves a debt of the story
    assert STORY["settings"]["playback"]["inject_enabled"] is True
    assert not STORY["settings"].get("trust_drift", {}).get("enabled")  # no limit drifts under the story


def test_every_event_is_an_episode_served_by_build_story() -> None:
    assert story_errors(STORY) == []
    story = build_story(STORY)

    assert len(story.episodes) == len(STORY["events"])  # a caption on every event, in both languages
    assert [e.index for e in story.episodes] == list(range(len(STORY["events"])))
    assert story.playback is not None and story.playback.tick_seconds == 2.5


def test_a_periodic_clearing_cannot_take_the_cycle_of_an_episode() -> None:
    assert _stolen(STORY) == [], _stolen(STORY)

    # anti-vacuum: the same rule fires on the story with its clearing moved across the periodic tick 50
    moved = deepcopy(STORY)
    clearing = next(e for e in moved["events"] if e["type"] == "clearing")
    clearing["time"] = 51_000
    assert _stolen(moved), "the rule no longer sees a clearing that waits across a periodic tick"
    # ... and on the periodic tick sitting between the cycle's last payment (38) and the clearing (43)
    assert _stolen(STORY, period=40) == [(8, 40)]


def test_the_story_is_not_in_the_default_allowlist_and_is_selectable_by_the_override(monkeypatch) -> None:
    """R1 (fix-delta 2026-10-09): the story needs three things a person picking it from a list is not given - the process flag for
    injects, intensity 0 and a base that has not run it - and the Simulator UI cannot supply the second (it sends 30). So it
    is NOT in the default list; the existing override (`SIMULATOR_SCENARIO_ALLOWLIST`) lists it, and the registry has it either
    way. The session that adds the launch flow (S9) adds it to the default list."""

    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", "")
    assert SCENARIO_ID not in (_scenario_allowlist() or set())
    assert SCENARIO_ID not in [s.scenario_id for s in runtime.list_scenarios()]
    assert runtime.get_scenario(SCENARIO_ID).raw["scenario_id"] == SCENARIO_ID  # in the registry all the same

    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", SCENARIO_ID)
    assert [s.scenario_id for s in runtime.list_scenarios()] == [SCENARIO_ID]  # the override makes it the one listed
    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", "*")
    assert SCENARIO_ID in [s.scenario_id for s in runtime.list_scenarios()]
