"""036 slice A: the projection of a scenario's raw JSON into the REST story (`ScenarioRecord.detail`).

Uploaded scenarios are schema-validated; the fixtures the runtime loads, and a scenario stored on disk by an older build,
are not. The projection is therefore an INTEGRITY BOUNDARY (AGENTS.md section 9), decided in the fix-delta of the review
of `1afe0b09` (finding 2, decision F3): a captioned event is an episode, and an episode that cannot be served as written -
a caption that is not a non-empty `{ru, en}` pair, a `pause_after` that is not a boolean, a time that is not an integer,
an anchor the spec does not allow - makes the detail REFUSE (409, `SCENARIO_INVALID`, the path of every bad field), never
drop the episode and never alter its meaning. An event with NO caption is not an episode, and that is not an error.
`episodes: []` therefore means exactly "no captioned events".

The refusal names paths in the existing `/`-separated spelling of `SCENARIO_INVALID` (`events/1/caption/en`).
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.simulator.models import ScenarioRecord
from app.utils.exceptions import ConflictException

GOOD = {"time": 1000, "type": "note", "caption": {"ru": "р", "en": "e"}}
FIRST = {"time": 0, "type": "note", "caption": {"ru": "р0", "en": "e0"}}


def _record(raw: dict) -> ScenarioRecord:
    return ScenarioRecord(
        scenario_id="s", name="n", created_at=None, participants_count=2, trustlines_count=1,
        equivalents=["UAH"], raw=raw, source_path=None,
    )


def _story(*events: dict, **top) -> dict:
    return {"participants": [{"id": "A"}, {"id": "B"}, {"id": "C"}], "events": [FIRST, *events], **top}


def _with(event: dict, **patch) -> dict:
    out = deepcopy(event)
    out.update(patch)
    return out


BAD_EPISODES = [
    ("caption without en", _with(GOOD, caption={"ru": "x"}), "events/1/caption/en"),
    ("caption as a plain string", _with(GOOD, caption="a plain string"), "events/1/caption"),
    ("caption with an empty language", _with(GOOD, caption={"ru": "x", "en": ""}), "events/1/caption/en"),
    ("caption with a third language", _with(GOOD, caption={"ru": "x", "en": "y", "fr": "z"}), "events/1/caption/fr"),
    ("pause_after as a string", _with(GOOD, pause_after="yes"), "events/1/pause_after"),
    ("pause_after as a number", _with(GOOD, pause_after=1), "events/1/pause_after"),
    ("a string time", _with(GOOD, time="day_10"), "events/1/time"),
    ("a boolean time", _with(GOOD, time=True), "events/1/time"),
    ("a negative time", _with(GOOD, time=-5), "events/1/time"),
    ("a fractional time", _with(GOOD, time=1000.5), "events/1/time"),
    ("an unknown event type", _with(GOOD, type="teleport"), "events/1/type"),
    ("an anchor of an unknown event", _with(GOOD, anchor={"event": "run_status"}), "events/1/anchor/event"),
    ("a bare tx.updated anchor", _with(GOOD, anchor={"event": "tx.updated"}), "events/1/anchor"),
    ("a tx.failed anchor with an amount",
     _with(GOOD, anchor={"event": "tx.failed", "from": "A", "to": "B", "equivalent": "UAH", "amount": "1"}),
     "events/1/anchor"),
    ("an anchor amount with a terminal newline",
     _with(GOOD, anchor={"event": "tx.updated", "from": "A", "to": "B", "equivalent": "UAH", "amount": "1\n"}),
     "events/1/anchor/amount"),
    ("an anchor amount with 19 fraction digits",
     _with(GOOD, anchor={"event": "tx.updated", "from": "A", "to": "B", "equivalent": "UAH",
                         "amount": "0.1234567890123456789"}), "events/1/anchor/amount"),
    ("an anchor amount with an Arabic-Indic digit",
     _with(GOOD, anchor={"event": "tx.updated", "from": "A", "to": "B", "equivalent": "UAH", "amount": "٣"}),
     "events/1/anchor/amount"),
    ("a focus with an unknown key", _with(GOOD, focus={"zoom": 2}), "events/1/focus/zoom"),
    ("an expected cycle of one", _with(GOOD, expected_cycle=["A"]), "events/1/expected_cycle"),
    ("a focus naming nobody", _with(GOOD, focus={"pids": ["NOBODY"]}), "events/1/focus/pids/0"),
]


@pytest.mark.parametrize(("name", "event", "path"), BAD_EPISODES, ids=[b[0] for b in BAD_EPISODES])
def test_a_bad_captioned_episode_refuses_the_detail_and_names_the_path(name: str, event: dict, path: str) -> None:
    record = _record(_story(event))

    with pytest.raises(ConflictException) as refused:
        record.detail()

    assert refused.value.status_code == 409
    assert refused.value.details["simulator_error"] == "SCENARIO_INVALID"
    assert [e["path"] for e in refused.value.details["errors"]] == [path], refused.value.details


BAD_PLAYBACK = [
    ("tick below the floor", {"tick_seconds": 0.1}, "settings/playback/tick_seconds"),
    ("tick above the ceiling", {"tick_seconds": 6}, "settings/playback/tick_seconds"),
    ("tick as a string", {"tick_seconds": "2"}, "settings/playback/tick_seconds"),
    ("intensity over 100", {"intensity_percent": 101}, "settings/playback/intensity_percent"),
    ("intensity fractional", {"intensity_percent": 50.5}, "settings/playback/intensity_percent"),
    ("inject_enabled as a string", {"inject_enabled": "yes"}, "settings/playback/inject_enabled"),
    ("an unknown key", {"speed": 2}, "settings/playback/speed"),
]


@pytest.mark.parametrize(("name", "playback", "path"), BAD_PLAYBACK, ids=[b[0] for b in BAD_PLAYBACK])
def test_an_invalid_playback_refuses_the_detail(name: str, playback: dict, path: str) -> None:
    record = _record(_story(GOOD, settings={"playback": playback}))

    with pytest.raises(ConflictException) as refused:
        record.detail()

    assert [e["path"] for e in refused.value.details["errors"]] == [path], refused.value.details


@pytest.mark.parametrize(
    ("description", "path"),
    [({"ru": "x"}, "description/en"), ({"ru": "", "en": "y"}, "description/ru"), (5, "description"),
     ({"ru": "x", "en": "y", "fr": "z"}, "description/fr")],
    ids=["no-en", "empty-ru", "a-number", "third-language"],
)
def test_an_invalid_description_refuses_the_detail_but_not_the_summary(description, path: str) -> None:
    record = _record(_story(GOOD, description=description))

    with pytest.raises(ConflictException) as refused:
        record.detail()

    assert [e["path"] for e in refused.value.details["errors"]] == [path], refused.value.details
    # the LIST must not fail for one stored scenario: the summary serves no description rather than a wrong one
    assert record.summary().description is None


def test_every_error_of_the_story_is_reported_not_only_the_first() -> None:
    record = _record(_story(_with(GOOD, caption={"ru": "x"}), _with(GOOD, pause_after="yes"),
                            settings={"playback": {"tick_seconds": 9}}))

    with pytest.raises(ConflictException) as refused:
        record.detail()

    assert [e["path"] for e in refused.value.details["errors"]] == [
        "events/1/caption/en", "events/2/pause_after", "settings/playback/tick_seconds"]


def test_an_event_without_a_caption_is_not_an_episode_whatever_else_it_holds() -> None:
    """Control: a legacy stored scenario (a string time, a `params` block, no caption anywhere) is still served."""

    legacy = {"participants": [{"id": "A"}], "events": [
        {"time": "day_10", "type": "stress", "params": {"multiplier": 1.3}},
        {"time": 5, "type": "note", "description": "n"},
    ]}

    detail = _record(legacy).detail()

    assert detail.episodes == [] and detail.playback is None


def test_no_events_or_a_non_list_is_no_episodes() -> None:
    assert _record({}).detail().episodes == []
    assert _record({"events": "x"}).detail().episodes == []


def test_good_episodes_are_served_with_their_index_in_events() -> None:
    detail = _record(_story(GOOD, {"time": 2, "type": "note"}, _with(GOOD, time=3000, pause_after=True))).detail()

    assert [(e.index, e.time_ms, e.pause_after) for e in detail.episodes] == [(0, 0, False), (1, 1000, False),
                                                                             (3, 3000, True)]


def test_description_forms() -> None:
    assert _record({"description": "plain"}).detail().description.model_dump() == {"ru": "plain", "en": "plain"}
    assert _record({"description": {"ru": "р", "en": "e"}}).detail().description.model_dump() == {"ru": "р", "en": "e"}
    assert _record({"description": "  "}).detail().description is None  # blank = none, not a wrong text
    assert _record({}).detail().description is None


def test_playback_forms() -> None:
    assert _record({"settings": {"playback": {"tick_seconds": 2}}}).detail().playback.model_dump() == {
        "tick_seconds": 2.0, "intensity_percent": None, "inject_enabled": None}
    assert _record({"settings": {"playback": {"tick_seconds": 0.25, "intensity_percent": 100, "inject_enabled": False}}}
                   ).detail().playback.model_dump() == {
        "tick_seconds": 0.25, "intensity_percent": 100, "inject_enabled": False}
    assert _record({"settings": {}}).detail().playback is None
    assert _record({}).detail().playback is None


def test_detail_is_the_summary_plus_the_story() -> None:
    record = _record({"description": "d", "events": [GOOD], "settings": {"playback": {"inject_enabled": True}}})

    detail = record.detail()

    assert detail.model_dump(exclude={"episodes", "playback"}) == record.summary().model_dump()
    assert [e.index for e in detail.episodes] == [0]
    assert detail.playback.inject_enabled is True


def test_an_anchor_dumps_with_the_wire_key_from() -> None:
    """The wire key is `from`, not the Python name `from_` (AGENTS.md section 8): a wire dump uses `by_alias=True`."""

    anchor = {"event": "tx.updated", "from": "A", "to": "B", "amount": "1", "equivalent": "UAH"}
    [episode] = _record(_story(_with(GOOD, anchor=anchor))).detail().episodes[1:]

    dumped = episode.anchor.model_dump(mode="json", by_alias=True)

    assert dumped["from"] == "A" and "from_" not in dumped, dumped
