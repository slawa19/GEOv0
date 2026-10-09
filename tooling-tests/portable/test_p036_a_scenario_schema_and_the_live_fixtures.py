"""036 slice A (`T3610`): the scenario schema and the committed live fixtures. No database.

WHAT THIS GUARDS. F-036-5: the live fixtures (`fixtures/simulator/*/scenario.json`, the ones the runtime loads) are
not validated against `fixtures/simulator/scenario.schema.json` when the runtime loads them (`scenario_registry.py`
validates uploads only), so a fixture and the schema could drift apart unseen. Here every live fixture is validated.
F-036-1: the schema no longer admits a string event time; the runner never understood one, so such an event would
silently never fire. The new episode fields of 036 (`caption`, `pause_after`, `focus`, `anchor`, `expected_cycle`,
`settings.playback`, a `{ru, en}` description, `payment` arguments) are accepted when well formed and refused when not.

WHY ONLY THE LIVE SET. `fixtures/simulator/_archive/golden-7_2-like` carries `time: "day_10"` (measured 2026-10-08).
The runtime skips `_` directories and the archive is read-only (AGENTS.md §3), so after F-036-1 that file does not
validate; `test_the_archive_exception_is_exactly_the_string_time` pins that it is the ONLY reason, so the exception
cannot grow unnoticed.

ANTI-VACUUM. A guard that cannot fail proves nothing: the validator is shown to refuse a broken live fixture, every
refusal case is the VALID story scenario with exactly one mutation, and that story scenario is shown to validate.

WHAT IT DOES NOT SEE. Whether a fixture is MEANINGFUL (a caption that explains nothing, an anchor that never
arrives), and anything the runner does with the fields - slice A only describes and serves them.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "simulator"
SCHEMA = json.loads((ROOT / "scenario.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
LIVE = sorted(ROOT.glob("*/scenario.json"))
ARCHIVED = sorted(ROOT.glob("_archive/*/scenario.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _errors(scenario: dict) -> list[tuple[str, str]]:
    return [("/".join(str(p) for p in e.absolute_path), e.message[:90]) for e in VALIDATOR.iter_errors(scenario)]


def test_the_live_set_is_the_five_scenarios_the_runtime_loads() -> None:
    assert [p.parent.name for p in LIVE] == [
        "clearing-demo-10",
        "greenfield-village-100-realistic-v2",
        "minimal",
        "riverside-town-50-realistic-v2",
        "trust-drift-decay-minimal",
    ]
    assert len(ARCHIVED) == 6


@pytest.mark.parametrize("path", LIVE, ids=lambda p: p.parent.name)
def test_every_live_fixture_validates_against_the_schema(path: Path) -> None:
    assert _errors(_load(path)) == []


def test_the_validator_refuses_a_broken_live_fixture() -> None:
    """Anti-vacuum for the guard above: the same validator fails a live fixture with a forbidden field."""

    broken = _load(ROOT / "clearing-demo-10" / "scenario.json")
    broken["trustlines"][0]["not_a_field"] = 1
    broken["events"][0]["time"] = "day_10"
    paths = sorted(path for path, _ in _errors(broken))
    assert paths == ["events/0/time", "trustlines/0"], paths


def test_the_archive_exception_is_exactly_the_string_time() -> None:
    failing = {
        p.parent.name: _errors(_load(p)) for p in ARCHIVED if _errors(_load(p))
    }
    assert list(failing) == ["golden-7_2-like"], failing
    assert [path for path, _ in failing["golden-7_2-like"]] == ["events/0/time"], failing


# ----------------------------------------------------------------------------------------------- the new fields


def _story() -> dict:
    """A small narrative scenario using every field slice A adds."""

    return {
        "schema_version": "scenario/1",
        "scenario_id": "story-schema-probe",
        "name": "Probe",
        "description": {"ru": "Описание", "en": "Description"},
        "equivalents": ["UAH"],
        "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}, {"id": "C", "type": "person"}],
        "trustlines": [{"from": "B", "to": "A", "equivalent": "UAH", "limit": "100"}],
        "settings": {"playback": {"tick_seconds": 2.5, "intensity_percent": 0, "inject_enabled": True}},
        "events": [
            {"time": 0, "type": "note", "caption": {"ru": "Знакомство", "en": "Meeting"}},
            {
                "time": 5000,
                "type": "payment",
                "from": "A",
                "to": "B",
                "amount": "5.00",
                "equivalent": "UAH",
                "caption": {"ru": "Первая покупка", "en": "First purchase"},
                "pause_after": True,
                "focus": {"pids": ["A", "B"], "edges": [{"from": "B", "to": "A"}]},
                "anchor": {"event": "tx.updated", "from": "A", "to": "B", "amount": "5.00", "equivalent": "UAH",
                           "time_ms": 5000},
            },
            {
                "time": 9000,
                "type": "clearing",
                "equivalent": "UAH",
                "caption": {"ru": "Клиринг", "en": "Clearing"},
                "expected_cycle": ["A", "B", "C"],
                "anchor": {"event": "clearing.done"},
            },
        ],
    }


def test_the_story_scenario_validates() -> None:
    assert _errors(_story()) == []


def _set(path: list, value) -> object:
    def mutate(s: dict) -> None:
        node = s
        for key in path[:-1]:
            node = node[key]
        if value is _DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value

    return mutate


_DELETE = object()

REFUSED = [
    ("string time", _set(["events", 0, "time"], "day_10"), "events/0/time"),
    ("negative time", _set(["events", 0, "time"], -1), "events/0/time"),
    ("fractional time", _set(["events", 0, "time"], 1.5), "events/0/time"),
    ("caption without en", _set(["events", 0, "caption"], {"ru": "x"}), "events/0/caption"),
    ("caption as a plain string", _set(["events", 0, "caption"], "x"), "events/0/caption"),
    ("caption with an empty language", _set(["events", 0, "caption"], {"ru": "x", "en": ""}), "events/0/caption/en"),
    ("pause_after not boolean", _set(["events", 1, "pause_after"], "yes"), "events/1/pause_after"),
    ("anchor of an unknown event", _set(["events", 1, "anchor", "event"], "run_status"), "events/1/anchor/event"),
    ("anchor with an unknown key", _set(["events", 1, "anchor", "cleared_amount"], "1"), "events/1/anchor"),
    ("anchor amount as a number", _set(["events", 1, "anchor", "amount"], 5), "events/1/anchor/amount"),
    ("tx.updated anchor without an amount", _set(["events", 1, "anchor", "amount"], _DELETE), "events/1/anchor"),
    ("tx.updated anchor without an equivalent", _set(["events", 1, "anchor", "equivalent"], _DELETE), "events/1/anchor"),
    ("tx.updated anchor without a sender", _set(["events", 1, "anchor", "from"], _DELETE), "events/1/anchor"),
    ("tx.updated anchor with only the event", _set(["events", 1, "anchor"], {"event": "tx.updated"}), "events/1/anchor"),
    ("tx.failed anchor carrying an amount",
     _set(["events", 1, "anchor"], {"event": "tx.failed", "from": "A", "to": "B", "equivalent": "UAH", "amount": "5.00"}),
     "events/1/anchor"),
    ("tx.failed anchor without an equivalent", _set(["events", 1, "anchor"], {"event": "tx.failed", "from": "A", "to": "B"}),
     "events/1/anchor"),
    ("payment amount with 19 fraction digits", _set(["events", 1, "amount"], "0.1234567890123456789"), "events/1/amount"),
    ("payment amount of 51 digits", _set(["events", 1, "amount"], "1" * 51), "events/1/amount"),
    ("payment amount with a terminal newline", _set(["events", 1, "amount"], "5.00\n"), "events/1/amount"),
    ("payment amount with an Arabic-Indic digit", _set(["events", 1, "amount"], "٣"), "events/1/amount"),
    ("payment amount with an exponent", _set(["events", 1, "amount"], "1e3"), "events/1/amount"),
    ("anchor amount with a terminal newline", _set(["events", 1, "anchor", "amount"], "5.00\n"), "events/1/anchor/amount"),
    ("anchor amount with 19 fraction digits", _set(["events", 1, "anchor", "amount"], "0.1234567890123456789"),
     "events/1/anchor/amount"),
    ("focus with an unknown key", _set(["events", 1, "focus", "zoom"], 2), "events/1/focus"),
    ("focus edge without to", _set(["events", 1, "focus", "edges"], [{"from": "A"}]), "events/1/focus/edges/0"),
    ("expected_cycle of one", _set(["events", 2, "expected_cycle"], ["A"]), "events/2/expected_cycle"),
    ("payment without amount", _set(["events", 1, "amount"], _DELETE), "events/1"),
    ("payment amount as a number", _set(["events", 1, "amount"], 5.0), "events/1/amount"),
    ("payment amount negative", _set(["events", 1, "amount"], "-5"), "events/1/amount"),
    ("playback tick below the floor", _set(["settings", "playback", "tick_seconds"], 0.1), "settings/playback/tick_seconds"),
    ("playback tick above the ceiling", _set(["settings", "playback", "tick_seconds"], 6), "settings/playback/tick_seconds"),
    ("playback intensity over 100", _set(["settings", "playback", "intensity_percent"], 101), "settings/playback/intensity_percent"),
    ("playback unknown key", _set(["settings", "playback", "speed"], 2), "settings/playback"),
    ("an event with the removed params block", _set(["events", 0, "params"], {"multiplier": 1.3}), "events/0"),
    ("description without en",_set(["description"], {"ru": "x"}), "description"),
]


@pytest.mark.parametrize(("name", "mutate", "path"), REFUSED, ids=[r[0] for r in REFUSED])
def test_the_schema_refuses_one_broken_field(name: str, mutate, path: str) -> None:
    scenario = deepcopy(_story())
    mutate(scenario)
    paths = [p for p, _ in _errors(scenario)]
    assert paths, f"{name}: the schema accepted it"
    assert any(p == path or p.startswith(path + "/") or path.startswith(p + "/") for p in paths), (name, paths)


@pytest.mark.parametrize(
    "amount", ["0.123456789012345678", "0" * 50, "1" * 32 + "." + "1" * 18, "00005.50", "5"], ids=str
)
def test_the_amount_grammar_keeps_its_boundaries_open(amount: str) -> None:
    """Controls for the bounds above (18 fraction digits, 50 digits in all, leading zeros) - a pattern that refused
    everything would pass every refusal case."""

    scenario = deepcopy(_story())
    scenario["events"][1]["amount"] = amount
    scenario["events"][1]["anchor"]["amount"] = amount
    assert _errors(scenario) == []


def test_a_complete_tx_failed_anchor_validates() -> None:
    scenario = deepcopy(_story())
    scenario["events"][1]["anchor"] = {"event": "tx.failed", "from": "A", "to": "B", "equivalent": "UAH"}
    assert _errors(scenario) == []


def test_a_plain_string_description_and_a_scenario_without_episodes_still_validate() -> None:
    """The compatibility the spec asks for: older scenarios (a string description, no caption anywhere)."""

    old = _story()
    old["description"] = "plain"
    old["events"] = [{"time": 0, "type": "note", "description": "n"}]
    del old["settings"]
    assert _errors(old) == []
