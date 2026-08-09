from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import ScenarioRegistry


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "source_payload", "error_path"),
    [
        ("top", {"equivalents": ["MY-TOKEN"]}, "equivalents/0"),
        ("default", {"baseEquivalent": "MY-TOKEN"}, "baseEquivalent"),
        (
            "trustline",
            {
                "equivalents": ["USD"],
                "trustlines": [
                    {
                        "from": "P1",
                        "to": "P2",
                        "equivalent": "MY-TOKEN",
                        "limit": "10",
                    }
                ],
            },
            "trustlines/0/equivalent",
        ),
        (
            "inject-effect",
            {
                "equivalents": ["USD"],
                "events": [
                    {
                        "time": 0,
                        "type": "inject",
                        "effects": [
                            {
                                "op": "create_trustline",
                                "from": "P1",
                                "to": "P2",
                                "equivalent": "MY-TOKEN",
                                "limit": "10",
                            }
                        ],
                    }
                ],
            },
            "events/0/effects/0/equivalent",
        ),
        (
            "initial-trustline",
            {
                "equivalents": ["USD"],
                "events": [
                    {
                        "time": 0,
                        "type": "inject",
                        "effects": [
                            {
                                "op": "add_participant",
                                "participant": {"id": "P3", "type": "person"},
                                "initial_trustlines": [
                                    {
                                        "sponsor": "P1",
                                        "equivalent": "MY-TOKEN",
                                        "limit": "10",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            "events/0/effects/0/initial_trustlines/0/equivalent",
        ),
    ],
)
async def test_scenario_upload_rejects_noncanonical_equivalent_before_storage(
    client,
    monkeypatch,
    tmp_path: Path,
    source: str,
    source_payload: dict,
    error_path: str,
) -> None:
    scenario_id = f"invalid-equivalent-{source}"
    scenario = {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "participants": [
            {"id": "P1", "type": "person"},
            {"id": "P2", "type": "person"},
        ],
        "trustlines": [],
        **source_payload,
    }
    registry = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios={},
        fixtures_dir=tmp_path / "fixtures",
        schema_path=(
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "simulator"
            / "scenario.schema.json"
        ),
        local_state_dir=tmp_path,
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", registry)

    response = await client.post(
        "/api/v1/simulator/scenarios",
        headers=_admin_headers(),
        json={"scenario": scenario},
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "E009"
    assert error["details"]["simulator_error"] == "SCENARIO_INVALID"
    assert error["details"]["errors"] == [
        {
            "path": error_path,
            "message": "Noncanonical equivalent code: MY-TOKEN",
        }
    ]
    assert scenario_id not in registry._scenarios
    assert not (tmp_path / "scenarios" / scenario_id).exists()


@pytest.mark.asyncio
async def test_scenario_upload_bounds_noncanonical_equivalent_errors(
    client,
    monkeypatch,
    tmp_path: Path,
) -> None:
    scenario_id = "many-invalid-equivalents"
    scenario = {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "participants": [{"id": "P1", "type": "person"}],
        "trustlines": [],
        "equivalents": [f"INVALID-{index}" for index in range(60)],
    }
    registry = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios={},
        fixtures_dir=tmp_path / "fixtures",
        schema_path=(
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "simulator"
            / "scenario.schema.json"
        ),
        local_state_dir=tmp_path,
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", registry)

    response = await client.post(
        "/api/v1/simulator/scenarios",
        headers=_admin_headers(),
        json={"scenario": scenario},
    )

    assert response.status_code == 400, response.text
    errors = response.json()["error"]["details"]["errors"]
    assert len(errors) == 50
    assert errors[0]["path"] == "equivalents/0"
    assert errors[-1]["path"] == "equivalents/49"
    assert scenario_id not in registry._scenarios
    assert not (tmp_path / "scenarios" / scenario_id).exists()
