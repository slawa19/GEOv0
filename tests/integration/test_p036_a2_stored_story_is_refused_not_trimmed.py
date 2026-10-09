"""036 slice A, fix-delta (review finding 2, decision F3): a stored or shipped scenario whose story cannot be served as
written is REFUSED by `GET /simulator/scenarios/{id}` - 409 `E008`, `details.simulator_error = SCENARIO_INVALID`, the path
of every bad field - and its source is left as it is. The scenario list keeps working (one bad stored scenario must not
take the index down), and a scenario with no captioned event still answers `episodes: []`.

Red on `1afe0b09`: the detail answered 200 with the damaged episode simply absent.

Does a RUN of such a scenario refuse too? Not in slice A: nothing that creates or ticks a run reads `caption`,
`pause_after`, `focus`, `anchor`, `expected_cycle` or `settings.playback` yet (grep over `app/` at the fix-delta; slice B
adds those reads, and the refusal belongs at the point of use).
"""

from __future__ import annotations

import pytest

from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import scenario_to_record
from tests.contract.openapi_response_conformance import load_canon, validate_body

ORIGIN = {"Origin": "http://localhost:5176"}
ID = "p036-a2-stored-damaged"


def _damaged() -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": ID,
        "equivalents": ["UAH"],
        "participants": [{"id": "A", "type": "person"}],
        "trustlines": [],
        "events": [
            {"time": 0, "type": "note", "caption": {"ru": "р", "en": "e"}},
            {"time": 1000, "type": "note", "caption": {"ru": "only ru"}, "pause_after": "yes"},
        ],
    }


@pytest.fixture
def stored(monkeypatch):
    record = scenario_to_record(_damaged(), source_path=None, created_at=None)
    monkeypatch.setitem(runtime._scenarios, ID, record)
    return record


async def _anonymous(client) -> None:
    client.cookies.clear()
    assert (await client.post("/api/v1/simulator/session/ensure")).status_code == 200


@pytest.mark.asyncio
async def test_the_detail_of_a_damaged_stored_story_is_refused_with_the_paths(client, stored) -> None:
    await _anonymous(client)

    response = await client.get(f"/api/v1/simulator/scenarios/{ID}")

    assert response.status_code == 409, f"{response.status_code}: {response.text[:300]}"
    error = response.json()["error"]
    assert error["code"] == "E008"
    assert error["details"]["simulator_error"] == "SCENARIO_INVALID"
    assert [e["path"] for e in error["details"]["errors"]] == ["events/1/caption/en", "events/1/pause_after"]
    assert validate_body(load_canon(), "/components/schemas/ErrorEnvelope", response.json()) == []


@pytest.mark.asyncio
async def test_the_source_of_the_refused_scenario_is_untouched(client, stored) -> None:
    await _anonymous(client)
    before = _damaged()

    await client.get(f"/api/v1/simulator/scenarios/{ID}")

    assert runtime._scenarios[ID].raw == before


@pytest.mark.asyncio
async def test_the_list_still_serves_the_damaged_scenario(client, stored, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", "all")
    await _anonymous(client)

    response = await client.get("/api/v1/simulator/scenarios")

    assert response.status_code == 200, response.text
    assert ID in [item["scenario_id"] for item in response.json()["items"]]


@pytest.mark.asyncio
async def test_a_scenario_without_captions_is_not_refused(client) -> None:
    """Control: `episodes: []` is the answer for a scenario that simply has no captioned event."""

    await _anonymous(client)

    response = await client.get("/api/v1/simulator/scenarios/clearing-demo-10")

    assert response.status_code == 200 and response.json()["episodes"] == []

