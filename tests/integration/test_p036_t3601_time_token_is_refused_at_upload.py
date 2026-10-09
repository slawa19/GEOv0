"""036 `T3601`, F-036-1: a string event time (`"day_10"`, allowed by `scenario.schema.json:244-249`) is accepted at upload
and the event then never fires - the runner's `_parse_event_time_ms` returns `None` for it (`real_runner_impl.py:180-187`)
and the due-events loop skips it silently (`:286-288`), so "never" is indistinguishable from "not yet" (AGENTS.md §1, §9).

TARGET (spec 036, "Целевое поведение"): `time` is an integer number of milliseconds only; the schema refuses a token and
the upload answers 400 `Scenario invalid` (`SCENARIO_INVALID`, `scenario_registry.py:60,75`).

CONTROL (green now): the same scenario with an integer time is accepted - the 400 is about the token, not about the
scenario around it - and the rejection leaves nothing stored (the existing contract of the upload validation).
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

SCHEMA = Path(__file__).resolve().parents[2] / "fixtures" / "simulator" / "scenario.schema.json"


def _registry(tmp_path: Path) -> ScenarioRegistry:
    return ScenarioRegistry(
        lock=threading.RLock(),
        scenarios={},
        fixtures_dir=tmp_path / "fixtures",
        schema_path=SCHEMA,
        local_state_dir=tmp_path,
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )


def _scenario(scenario_id: str, event_time) -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": scenario_id,
        "participants": [{"id": "P1", "type": "person"}, {"id": "P2", "type": "person"}],
        "trustlines": [],
        "equivalents": ["USD"],
        "events": [{"time": event_time, "type": "note", "description": "an episode"}],
    }


async def _upload(client, monkeypatch, registry: ScenarioRegistry, scenario: dict):
    monkeypatch.setattr(runtime, "_scenario_registry", registry)
    return await client.post(
        "/api/v1/simulator/scenarios",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json={"scenario": scenario},
    )


@pytest.mark.asyncio
async def test_control_an_integer_event_time_is_accepted(client, monkeypatch, tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    response = await _upload(client, monkeypatch, registry, _scenario("p036-int-time", 10_000))

    assert response.status_code == 200, response.text
    assert "p036-int-time" in registry._scenarios


@pytest.mark.asyncio
async def test_a_string_event_time_is_refused_at_upload(client, monkeypatch, tmp_path: Path) -> None:
    """TARGET (red now): 400 SCENARIO_INVALID naming the event; nothing is stored."""

    registry = _registry(tmp_path)

    response = await _upload(client, monkeypatch, registry, _scenario("p036-day-10", "day_10"))

    assert response.status_code == 400, (
        f"a scenario whose event has time 'day_10' answered {response.status_code}, expected 400: {response.text[:300]}"
    )
    error = response.json()["error"]
    assert error["details"]["simulator_error"] == "SCENARIO_INVALID", error
    assert any(e["path"].startswith("events/0") for e in error["details"]["errors"]), error["details"]["errors"]
    assert "p036-day-10" not in registry._scenarios
    assert not (tmp_path / "scenarios" / "p036-day-10").exists()
