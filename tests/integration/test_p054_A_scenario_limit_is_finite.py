"""054-A review FD1: a scenario line whose limit is not a finite number is refused when the scenario enters the registry.

Defect (2026-10-30, regression of 054-A-15): `scenario_to_snapshot` renders a line's limit through `to_money_str`, which
RAISES on a non-finite value (`app/utils/money.py`). The schema types `limit` as any string or number and the story
check reads no limit, so `{"limit": "NaN"}` (or a bare JSON `NaN`, which Python's `json` parses) was accepted; the
preview, the run snapshot and `trustlines-list` of that scenario then answered 500. Before 054-A-15 the preview put the
raw `"NaN"` on the wire.

Target: a limit that does not parse as a finite decimal is a `SCENARIO_INVALID` 400 naming `trustlines/<i>/limit`, the
same refusal as the other scenario errors, and the scenario does not enter the registry - on upload and on loading a
stored or shipped scenario from disk (both go through `scenario_to_record`). The seeder's own named refusal (054-A-13)
is untouched.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = {"Origin": "http://localhost:5176"}
UPLOAD_HEADERS = {**ORIGIN, "X-Admin-Token": settings.ADMIN_TOKEN}
PREFIX = "p054-a-fd1-"

#: Every spelling that reaches the registry: the string forms and the bare JSON literals Python's `json` parses.
NOT_FINITE = ["NaN", "Infinity", "-Infinity", "sNaN", "abc", float("nan"), float("inf")]


@pytest.fixture
def registry(monkeypatch, tmp_path: Path):
    reg = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios=runtime._scenarios,
        fixtures_dir=tmp_path / "fixtures",
        schema_path=REPO_ROOT / "fixtures" / "simulator" / "scenario.schema.json",
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", reg)
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", "all")
    try:
        yield reg
    finally:
        for key in [k for k in runtime._scenarios if k.startswith(PREFIX)]:
            runtime._scenarios.pop(key, None)


def _scenario(name: str, limit) -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": PREFIX + name,
        "equivalents": ["UAH"],
        "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}],
        "trustlines": [{"from": "A", "to": "B", "equivalent": "UAH", "limit": "10"},
                       {"from": "B", "to": "A", "equivalent": "UAH", "limit": limit}],
    }


async def _upload(client, scenario: dict):
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    assert ensured.status_code == 200, ensured.text
    # `allow_nan` (the default) spells a float NaN/Infinity as the bare JSON literal the server's `json` parses.
    body = json.dumps({"scenario": scenario})
    return await client.post(
        "/api/v1/simulator/scenarios",
        headers={**UPLOAD_HEADERS, "Content-Type": "application/json"},
        content=body,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", NOT_FINITE, ids=[repr(v) for v in NOT_FINITE])
async def test_a_non_finite_limit_is_refused_at_upload_by_name(client, registry, limit) -> None:
    name = f"bad-{NOT_FINITE.index(limit)}"
    response = await _upload(client, _scenario(name, limit))

    assert response.status_code == 400, response.text
    details = response.json()["error"]["details"]
    assert details["simulator_error"] == "SCENARIO_INVALID", details
    assert [e["path"] for e in details["errors"]] == ["trustlines/1/limit"], details
    assert PREFIX + name not in runtime._scenarios
    preview = await client.get(f"/api/v1/simulator/scenarios/{PREFIX}{name}/graph/preview", params={"equivalent": "UAH"})
    assert preview.status_code == 404, preview.text


@pytest.mark.asyncio
# MM2-MM-B (2026-10-31): a JSON-number limit is refused now (`test_mm_m2_scenario_load_refusals_postgres.py`); the
# controls are its decimal-string spellings.
@pytest.mark.parametrize("limit", ["7", "7.00", "7.5", "0"])
async def test_control_a_finite_limit_is_accepted_and_previewed(client, registry, limit) -> None:
    name = f"ok-{str(limit).replace('.', '-')}-{type(limit).__name__}"
    response = await _upload(client, _scenario(name, limit))

    assert response.status_code == 200, response.text
    preview = await client.get(f"/api/v1/simulator/scenarios/{PREFIX}{name}/graph/preview", params={"equivalent": "UAH"})
    assert preview.status_code == 200, preview.text
    assert sorted(link["trust_limit"] for link in preview.json()["links"]) == sorted(["10.00", f"{float(limit):.2f}"])


@pytest.mark.parametrize("store", ["uploaded", "fixture"])
def test_a_stored_scenario_with_a_non_finite_limit_is_not_loaded(registry, store, caplog) -> None:
    root = registry._local_state_dir / "scenarios" if store == "uploaded" else registry._fixtures_dir
    for i, limit in enumerate(NOT_FINITE + ["8"]):
        name = f"disk-{store}-{i}"
        directory = root / (PREFIX + name)
        directory.mkdir(parents=True)
        (directory / "scenario.json").write_text(json.dumps(_scenario(name, limit)), encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        registry.load_uploaded_scenarios() if store == "uploaded" else registry.load_fixture_scenarios()

    loaded = sorted(k for k in runtime._scenarios if k.startswith(PREFIX + f"disk-{store}-"))
    # Counter-check: the finite control is loaded, so the refusal is not a refusal of everything.
    assert loaded == [PREFIX + f"disk-{store}-{len(NOT_FINITE)}"], loaded
