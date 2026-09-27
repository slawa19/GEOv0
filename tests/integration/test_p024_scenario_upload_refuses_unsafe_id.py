"""Programme 024, stage 0, F-024-4a (SIM-01), end to end through HTTP as an anonymous cookie actor.

`POST /api/v1/simulator/scenarios` needs only `require_simulator_actor`, which an anonymous visitor
satisfies with the cookie from `POST /session/ensure`. The unit twin
(`tests/unit/test_p024_scenario_id_is_a_safe_path_segment.py`) covers the registry alone.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import ScenarioRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = {"Origin": "http://localhost:5176"}


def _scenario(scenario_id: str) -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "equivalents": ["UAH"],
        "participants": [{"id": "P1", "type": "person"}],
        "trustlines": [],
    }


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path: Path) -> ScenarioRegistry:
    registry = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios={},
        fixtures_dir=tmp_path / "fixtures",
        schema_path=REPO_ROOT / "fixtures" / "simulator" / "scenario.schema.json",
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", registry)
    # Pin the CSRF allowlist so the anonymous POST passes it regardless of the local .env.
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    return registry


async def _anonymous(client) -> None:
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    assert ensured.status_code == 200, ensured.text
    assert ensured.json()["actor_kind"] == "anon"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id", ["../escape", "..\\escape", "C:\\abs"])
async def test_anonymous_upload_with_a_traversal_id_is_400_and_writes_nothing(
    client, isolated_registry: ScenarioRegistry, tmp_path: Path, scenario_id: str
) -> None:
    await _anonymous(client)

    response = await client.post(
        "/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": _scenario(scenario_id)}
    )

    assert response.status_code == 400, response.text
    assert sorted(tmp_path.rglob("scenario.json")) == []
    assert isolated_registry._scenarios == {}


@pytest.mark.asyncio
async def test_anonymous_upload_with_an_ordinary_id_still_succeeds(
    client, isolated_registry: ScenarioRegistry, tmp_path: Path
) -> None:
    # Counter-check: the same anonymous path keeps working for a well-formed id.
    await _anonymous(client)

    response = await client.post(
        "/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": _scenario("my-upload_1")}
    )

    assert response.status_code == 200, response.text
    assert response.json()["scenario_id"] == "my-upload_1"
    assert (tmp_path / "state" / "scenarios" / "my-upload_1" / "scenario.json").exists()
