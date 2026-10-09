"""036 B2 fix-delta: `pause_after` is a boolean on EVERY event, an event without a caption included.

The scenario schema (`fixtures/simulator/scenario.schema.json`, `$defs/timelineEvent/properties/pause_after`) types it
`boolean` on any event; the engine obeys it on any event (`RealRunnerImpl._pause_after_spent_episodes`), a captioned one
(an episode) or not. `build_story` read it only on a captioned event, so a stored or shipped scenario with
`{'time': 0, 'type': 'note', 'pause_after': 'yes'}` was accepted, had no episode, and the bad value silently paused nothing.
Now: the same 409 / `E008` as every other bad field of the story, with the path.

TARGET = RED on `7c11ee0f`; the controls are the good spellings.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.simulator.scenario_story import ScenarioStoryInvalid, build_story, story_errors
from tests.p021_support import require_target

CAPTION = {"ru": "р", "en": "e"}
UNCAPTIONED_BAD = {"time": 0, "type": "note", "pause_after": "yes"}


def _raw(*events) -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": "p036-b2-fixdelta",
        "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}],
        "trustlines": [{"from": "A", "to": "B", "equivalent": "UAH", "limit": "10"}],
        "equivalents": ["UAH"],
        "events": [deepcopy(e) for e in events],
    }


def test_a_pause_after_that_is_not_a_boolean_is_refused_on_an_event_without_a_caption() -> None:
    errors = story_errors(_raw(UNCAPTIONED_BAD))

    require_target([e["path"] for e in errors] == ["events/0/pause_after"], f"errors {errors}")
    with pytest.raises(ScenarioStoryInvalid) as raised:
        build_story(_raw(UNCAPTIONED_BAD))
    assert [e["path"] for e in raised.value.errors] == ["events/0/pause_after"]


@pytest.mark.parametrize("value", [None, "true", 1, 0, [], {}], ids=["null", "string", "one", "zero", "list", "object"])
def test_no_other_spelling_of_a_boolean_passes(value) -> None:
    errors = story_errors(_raw({"time": 0, "type": "note", "pause_after": value}))

    require_target([e["path"] for e in errors] == ["events/0/pause_after"], f"{value!r}: errors {errors}")


@pytest.mark.parametrize("event", [
    {"time": 0, "type": "note", "pause_after": True},
    {"time": 0, "type": "note", "pause_after": False},
    {"time": 0, "type": "note"},
    {"time": 0, "type": "note", "caption": CAPTION, "pause_after": True},
], ids=["true-uncaptioned", "false-uncaptioned", "absent", "true-captioned"])
def test_control_a_boolean_or_no_pause_after_is_accepted(event) -> None:
    assert story_errors(_raw(event)) == []
