"""036 slice A (`T3610`): `GET /simulator/scenarios/{scenario_id}` serves the story of the scenario.

TARGET (spec 036, "REST наружу"): the EXISTING detail route returns the summary plus `description` as a `{ru, en}` pair,
`episodes[]` (the events that carry a caption) and `playback`; the LIST carries `description` only. A scenario whose
description is a plain string is served as both languages. No SSE event or field is touched (П7); the story travels by
REST.

On 71c66dda (before slice A) the detail answered 200 with none of these fields and the list had no `description`.

Responses are checked against the canon (`api/openapi.yaml`) with the p011 engine; a body with a key the canon does not
declare is shown to be refused (anti-vacuum).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import ScenarioRegistry
from tests.contract.openapi_response_conformance import load_canon, validate_body

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = {"Origin": "http://localhost:5176"}
STORY_ID = "p036-a-story"


def _story() -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": STORY_ID,
        "name": "Story",
        "description": {"ru": "Описание", "en": "Description"},
        "equivalents": ["UAH"],
        "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}, {"id": "C", "type": "person"}],
        "trustlines": [{"from": "B", "to": "A", "equivalent": "UAH", "limit": "100"}],
        "settings": {"playback": {"tick_seconds": 2.5, "intensity_percent": 0, "inject_enabled": True}},
        "events": [
            {"time": 0, "type": "note", "description": "no caption: not an episode"},
            {"time": 1000, "type": "note", "caption": {"ru": "Знакомство", "en": "Meeting"}},
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
                "anchor": {"event": "tx.updated", "from": "A", "to": "B", "amount": "5.00", "equivalent": "UAH"},
            },
            {
                "time": 9000,
                "type": "clearing",
                "equivalent": "UAH",
                "caption": {"ru": "Клиринг", "en": "Clearing"},
                "expected_cycle": ["A", "B", "C"],
                "anchor": {"event": "clearing.done", "time_ms": 9000},
            },
        ],
    }


EXPECTED_EPISODES = [
    {"index": 1, "time_ms": 1000, "caption": {"ru": "Знакомство", "en": "Meeting"}, "pause_after": False,
     "kind": "note", "focus": None, "anchor": None, "expected_cycle": None},
    {"index": 2, "time_ms": 5000, "caption": {"ru": "Первая покупка", "en": "First purchase"}, "pause_after": True,
     "kind": "payment", "focus": {"pids": ["A", "B"], "edges": [{"from": "B", "to": "A"}]},
     "anchor": {"event": "tx.updated", "from": "A", "to": "B", "amount": "5.00", "equivalent": "UAH", "time_ms": None},
     "expected_cycle": None},
    {"index": 3, "time_ms": 9000, "caption": {"ru": "Клиринг", "en": "Clearing"}, "pause_after": False,
     "kind": "clearing", "focus": None,
     "anchor": {"event": "clearing.done", "from": None, "to": None, "amount": None, "equivalent": None, "time_ms": 9000},
     "expected_cycle": ["A", "B", "C"]},
]


@pytest.fixture
def story_registry(monkeypatch, tmp_path: Path):
    """An isolated store on disk, registering into the runtime's own scenario map so the routes can find it."""

    registry = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios=runtime._scenarios,
        fixtures_dir=tmp_path / "fixtures",
        schema_path=REPO_ROOT / "fixtures" / "simulator" / "scenario.schema.json",
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", registry)
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", "all")
    try:
        yield registry
    finally:
        runtime._scenarios.pop(STORY_ID, None)


async def _anonymous(client) -> None:
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    assert ensured.status_code == 200, ensured.text


def _conforms(schema: str, body) -> list:
    return validate_body(load_canon(), f"/components/schemas/{schema}", body)


@pytest.mark.asyncio
async def test_the_detail_serves_description_episodes_and_playback(client, story_registry) -> None:
    await _anonymous(client)
    uploaded = await client.post("/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": _story()})
    assert uploaded.status_code == 200, uploaded.text  # control: the story scenario is a valid upload

    response = await client.get(f"/api/v1/simulator/scenarios/{STORY_ID}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == {"ru": "Описание", "en": "Description"}
    assert body["episodes"] == EXPECTED_EPISODES  # the captioned events only, the wire key of an edge is `from`
    assert body["playback"] == {"tick_seconds": 2.5, "intensity_percent": 0, "inject_enabled": True}
    assert (body["participants_count"], body["trustlines_count"], body["equivalents"]) == (3, 1, ["UAH"])
    assert _conforms("ScenarioDetail", body) == []


@pytest.mark.asyncio
async def test_the_list_carries_the_description_and_no_story(client, story_registry) -> None:
    await _anonymous(client)
    await client.post("/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": _story()})

    response = await client.get("/api/v1/simulator/scenarios")

    assert response.status_code == 200, response.text
    body = response.json()
    [item] = [i for i in body["items"] if i["scenario_id"] == STORY_ID]
    assert item["description"] == {"ru": "Описание", "en": "Description"}
    assert "episodes" not in item and "playback" not in item, sorted(item)
    assert _conforms("ScenariosListResponse", body) == []


@pytest.mark.asyncio
async def test_the_upload_response_is_the_summary_with_the_description(client, story_registry) -> None:
    await _anonymous(client)

    response = await client.post("/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": _story()})

    body = response.json()
    assert body["description"] == {"ru": "Описание", "en": "Description"}
    assert "episodes" not in body and "playback" not in body, sorted(body)
    assert _conforms("ScenarioSummary", body) == []


@pytest.mark.asyncio
async def test_a_plain_string_description_is_served_as_both_languages(client) -> None:
    """The shipped fixture `clearing-demo-10` has a string description and no captions: compatibility."""

    raw = json.loads((REPO_ROOT / "fixtures" / "simulator" / "clearing-demo-10" / "scenario.json").read_text(encoding="utf-8"))
    assert isinstance(raw["description"], str) and raw["description"]  # control: the fixture is what the test says
    await _anonymous(client)

    response = await client.get("/api/v1/simulator/scenarios/clearing-demo-10")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == {"ru": raw["description"], "en": raw["description"]}
    assert body["episodes"] == [] and body["playback"] is None
    assert _conforms("ScenarioDetail", body) == []


@pytest.mark.asyncio
async def test_a_scenario_without_a_description_serves_null(client) -> None:
    await _anonymous(client)

    response = await client.get("/api/v1/simulator/scenarios/minimal")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] is None and body["episodes"] == [] and body["playback"] is None
    assert _conforms("ScenarioDetail", body) == []


def test_the_canon_check_refuses_an_undeclared_key() -> None:
    """Anti-vacuum for `_conforms`: the same engine refuses a detail body with a key the canon does not declare."""

    good = {"api_version": "simulator-api/1", "scenario_id": "s", "participants_count": 0, "trustlines_count": 0,
            "equivalents": [], "episodes": [], "playback": None, "description": None}
    assert _conforms("ScenarioDetail", good) == []
    assert _conforms("ScenarioDetail", {**good, "narration": "x"}) != []
    assert _conforms("ScenarioDetail", {**good, "episodes": [{"index": 0}]}) != []
