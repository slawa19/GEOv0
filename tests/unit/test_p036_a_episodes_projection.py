"""036 slice A: the projection of a scenario's raw JSON into the REST story (`ScenarioRecord.detail`).

Uploaded scenarios are schema-validated; the fixtures the runtime loads are not (F-034-14 / F-036-5, guarded in the
tooling tier), and a scenario stored on disk by an older build is not re-validated either. The projection therefore
leaves out what it cannot serve instead of failing the whole response - and these cases pin that it leaves out ONLY
that: the well-formed episodes next to a malformed one are still served (anti-vacuum for the skip).
"""

from __future__ import annotations

from app.core.simulator.models import ScenarioRecord, scenario_description, scenario_episodes, scenario_playback

GOOD = {"time": 1000, "type": "note", "caption": {"ru": "р", "en": "e"}}


def test_a_malformed_episode_is_left_out_and_its_neighbours_are_served() -> None:
    events = [
        {"time": 0, "type": "note", "caption": {"ru": "only ru"}},  # no en
        {"time": 0, "type": "note", "caption": "a plain string"},  # a caption is always the pair
        GOOD,
        {"time": "day_10", "type": "note", "caption": {"ru": "р", "en": "e"}},  # a string time
        {"time": True, "type": "note", "caption": {"ru": "р", "en": "e"}},  # a bool is not a time
        {"time": -5, "type": "note", "caption": {"ru": "р", "en": "e"}},
        {"time": 2000, "type": "payment", "caption": {"ru": "р", "en": "e"}, "anchor": {"event": "run_status"}},
        {"time": 3000, "type": "teleport", "caption": {"ru": "р", "en": "e"}},  # not an event type
        "not an event",
        {"time": 4000, "type": "note"},  # no caption: not an episode
        {"time": 5000, "type": "stress", "caption": {"ru": "р2", "en": "e2"}, "pause_after": "yes"},
    ]

    episodes = scenario_episodes({"events": events})

    assert [(e.index, e.time_ms, e.kind, e.pause_after) for e in episodes] == [
        (2, 1000, "note", False),
        (10, 5000, "stress", False),  # a non-boolean pause_after is not a pause
    ]


def test_no_events_or_a_non_list_is_no_episodes() -> None:
    assert scenario_episodes({}) == []
    assert scenario_episodes({"events": "x"}) == []


def test_description_forms() -> None:
    assert scenario_description({"description": "plain"}).model_dump() == {"ru": "plain", "en": "plain"}
    assert scenario_description({"description": {"ru": "р", "en": "e"}}).model_dump() == {"ru": "р", "en": "e"}
    assert scenario_description({"description": "  "}) is None
    assert scenario_description({"description": {"ru": "р"}}) is None
    assert scenario_description({"description": 5}) is None
    assert scenario_description({}) is None


def test_playback_forms() -> None:
    assert scenario_playback({"settings": {"playback": {"tick_seconds": 2}}}).model_dump() == {
        "tick_seconds": 2.0,
        "intensity_percent": None,
        "inject_enabled": None,
    }
    assert scenario_playback({"settings": {}}) is None
    assert scenario_playback({}) is None
    assert scenario_playback({"settings": {"playback": {"speed": 2}}}) is None  # an unknown key is not served


def test_detail_is_the_summary_plus_the_story() -> None:
    raw = {"description": "d", "events": [GOOD], "settings": {"playback": {"inject_enabled": True}}}
    record = ScenarioRecord(
        scenario_id="s", name="n", created_at=None, participants_count=2, trustlines_count=1,
        equivalents=["UAH"], raw=raw, source_path=None,
    )

    detail = record.detail()

    assert detail.model_dump(exclude={"episodes", "playback"}) == record.summary().model_dump()
    assert [e.index for e in detail.episodes] == [0]
    assert detail.playback.inject_enabled is True


def test_an_anchor_dumps_with_the_wire_key_from() -> None:
    """The wire key is `from`, not the Python name `from_` (AGENTS.md section 8): a wire dump uses `by_alias=True`."""

    [episode] = scenario_episodes(
        {"events": [{"time": 0, "type": "payment", "caption": {"ru": "р", "en": "e"},
                     "anchor": {"event": "tx.updated", "from": "A", "to": "B"}}]}
    )

    dumped = episode.anchor.model_dump(mode="json", by_alias=True)

    assert dumped["from"] == "A" and "from_" not in dumped, dumped
