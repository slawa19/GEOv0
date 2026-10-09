"""036 B2: what `POST /simulator/runs` does with the story and the playback of the scenario, and what `GET /runs/{id}` reports.

* A scenario whose story cannot be served as written is REFUSED at run creation, by the same rule and with the same answer as
  the detail read (`build_story`, 409 `E008`, `details.simulator_error = SCENARIO_INVALID`, the path of every bad field), and
  NO run is created. A scenario with no captions, or a good story, creates a run (controls).
* `intensity_percent` of the request is optional (decision MAKE-OPTIONAL): the request's value, `0` included, wins; else the
  scenario's `settings.playback.intensity_percent`; else 30. An out-of-range request value is still refused (422).
  The Simulator UI (`useSimulatorRealMode.ts`) always sends a number, so the scenario's default is not reached from it yet.
* `GET /runs/{id}` carries `episode_progress` (optional, typed): see `RunStatus` in `api/openapi.yaml`.

Runs are created in `fixtures` mode and stopped in a `finally`: nothing here depends on the creation mode.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import scenario_to_record
from tests.contract.openapi_response_conformance import load_canon, validate_body

BASE = {
    "schema_version": "scenario/1",
    "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}],
    "trustlines": [{"from": "A", "to": "B", "equivalent": "UAH", "limit": "10"}],
    "equivalents": ["UAH"],
}


def _register(monkeypatch, scenario_id: str, **top) -> str:
    raw = {**deepcopy(BASE), "scenario_id": scenario_id, **top}
    monkeypatch.setitem(runtime._scenarios, scenario_id, scenario_to_record(raw, source_path=None, created_at=None))
    return scenario_id


async def _start(client, auth_headers, scenario_id: str, **body):
    return await client.post(
        "/api/v1/simulator/runs", headers=auth_headers, json={"scenario_id": scenario_id, "mode": "fixtures", **body}
    )


async def _stop(client, auth_headers, response) -> None:
    if response.status_code == 200:
        await client.post(f"/api/v1/simulator/runs/{response.json()['run_id']}/stop", headers=auth_headers)


DAMAGED = [
    {"time": 0, "type": "note", "caption": {"ru": "р", "en": "e"}},
    {"time": 1000, "type": "note", "caption": {"ru": "only ru"}, "pause_after": "yes"},
]


@pytest.mark.asyncio
async def test_a_damaged_story_is_refused_at_run_creation_and_no_run_is_made(client, auth_headers, monkeypatch) -> None:
    """TARGET (red on 7df35fcf: the run is created, 200)."""

    scenario_id = _register(monkeypatch, "p036-b2-damaged", events=DAMAGED)
    runs_before = set(runtime._runs)

    response = await _start(client, auth_headers, scenario_id, intensity_percent=10)
    try:
        assert response.status_code == 409, f"{response.status_code}: {response.text[:300]}"
        error = response.json()["error"]
        assert error["code"] == "E008" and error["details"]["simulator_error"] == "SCENARIO_INVALID"
        assert [e["path"] for e in error["details"]["errors"]] == ["events/1/caption/en", "events/1/pause_after"]
        assert set(runtime._runs) == runs_before  # nothing was created
    finally:
        await _stop(client, auth_headers, response)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fixtures", "real"])
async def test_a_damaged_story_is_refused_in_either_mode(client, auth_headers, monkeypatch, mode) -> None:
    """Fix-delta: the refusal is of the story, not of a mode. `real` is the mode the story is played in; the product answers
    409 / E008 there too and creates nothing (a guard against `build_story` being called for `fixtures` only)."""

    scenario_id = _register(monkeypatch, f"p036-b2-damaged-{mode}", events=DAMAGED)
    runs_before = set(runtime._runs)

    response = await _start(client, auth_headers, scenario_id, mode=mode, intensity_percent=10)
    try:
        assert response.status_code == 409, f"{mode}: {response.status_code}: {response.text[:300]}"
        assert response.json()["error"]["details"]["simulator_error"] == "SCENARIO_INVALID"
        assert set(runtime._runs) == runs_before
    finally:
        await _stop(client, auth_headers, response)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fixtures", "real"])
async def test_a_pause_after_that_is_not_a_boolean_on_an_event_without_a_caption_is_refused_at_run_creation(
    client, auth_headers, monkeypatch, mode
) -> None:
    """TARGET (red on 7c11ee0f: 200, the bad value silently paused nothing)."""

    scenario_id = _register(monkeypatch, f"p036-b2-pause-{mode}", events=[{"time": 0, "type": "note", "pause_after": "yes"}])
    runs_before = set(runtime._runs)

    response = await _start(client, auth_headers, scenario_id, mode=mode, intensity_percent=10)
    try:
        assert response.status_code == 409, f"{mode}: {response.status_code}: {response.text[:300]}"
        error = response.json()["error"]
        assert error["code"] == "E008" and [e["path"] for e in error["details"]["errors"]] == ["events/0/pause_after"]
        assert set(runtime._runs) == runs_before
    finally:
        await _stop(client, auth_headers, response)


@pytest.mark.asyncio
async def test_control_a_good_story_and_a_scenario_without_captions_create_runs(client, auth_headers, monkeypatch) -> None:
    good = _register(monkeypatch, "p036-b2-good", events=[{"time": 0, "type": "note", "caption": {"ru": "р", "en": "e"}}])
    plain = _register(monkeypatch, "p036-b2-plain", events=[{"time": "day_10", "type": "note"}])  # a legacy stored shape

    first = await _start(client, auth_headers, good, intensity_percent=10)
    await _stop(client, auth_headers, first)
    second = await _start(client, auth_headers, plain, intensity_percent=10)
    await _stop(client, auth_headers, second)

    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("playback", "request_body", "expected"),
    [
        (None, {}, 30),  # nothing says: 30
        ({"intensity_percent": 70}, {}, 70),  # the scenario's default
        ({"intensity_percent": 70}, {"intensity_percent": 10}, 10),  # the request wins
        ({"intensity_percent": 70}, {"intensity_percent": 0}, 0),  # ... zero included
        (None, {"intensity_percent": 0}, 0),
        ({"tick_seconds": 2}, {}, 30),  # a playback without an intensity says nothing about it
    ],
    ids=["none", "scenario-default", "request-wins", "request-zero-wins", "request-zero", "playback-without-intensity"],
)
async def test_the_intensity_of_a_run_is_the_request_then_the_scenario_then_30(
    client, auth_headers, monkeypatch, playback, request_body, expected
) -> None:
    top = {"settings": {"playback": playback}} if playback is not None else {}
    scenario_id = _register(monkeypatch, "p036-b2-intensity", **top)

    response = await _start(client, auth_headers, scenario_id, **request_body)
    try:
        assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
        assert runtime.get_run(response.json()["run_id"]).intensity_percent == expected
    finally:
        await _stop(client, auth_headers, response)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [101, -1, "x", 5.5])
async def test_control_an_intensity_outside_0_100_or_not_an_integer_is_still_refused(client, auth_headers, monkeypatch, value) -> None:
    scenario_id = _register(monkeypatch, "p036-b2-bad-intensity")

    response = await _start(client, auth_headers, scenario_id, intensity_percent=value)

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_the_status_of_a_run_carries_its_episode_progress_on_the_wire(client, auth_headers, monkeypatch) -> None:
    scenario_id = _register(monkeypatch, "p036-b2-progress")
    response = await _start(client, auth_headers, scenario_id, intensity_percent=10)
    try:
        assert response.status_code == 200, response.text
        run = runtime.get_run(response.json()["run_id"])
        run._real_story_progress.update({
            0: {"kind": "payment", "status": "done", "epoch": 0, "equivalent": "UAH",
                "payment": {"from": "A", "to": "B", "amount": "5.00", "equivalent": "UAH"}},
            2: {"kind": "clearing", "status": "done", "epoch": 0, "equivalent": "UAH", "attempts": 1, "cleared_cycles": 1,
                "cycles": [{"cleared_amount": "10.00", "edges": [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}]}]},
            1: {"kind": "inject", "status": "refused", "reason": "inject_disabled_by_process", "epoch": 0},
        })

        status = await client.get(f"/api/v1/simulator/runs/{run.run_id}", headers=auth_headers)

        assert status.status_code == 200, status.text
        body = status.json()
        items = body.get("episode_progress")
        assert [i["index"] for i in items] == [0, 1, 2], items  # ordered by the event index
        assert items[0]["payment"] == {"from": "A", "to": "B", "amount": "5.00", "equivalent": "UAH"}
        assert items[1]["reason"] == "inject_disabled_by_process" and items[1]["status"] == "refused"
        assert items[2]["cycles"] == [{"cleared_amount": "10.00", "edges": [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}]}]
        assert validate_body(load_canon(), "/components/schemas/RunStatus", body) == []
    finally:
        await _stop(client, auth_headers, response)


@pytest.mark.asyncio
async def test_the_status_of_a_run_with_no_tracked_event_has_null_progress(client, auth_headers, monkeypatch) -> None:
    scenario_id = _register(monkeypatch, "p036-b2-no-progress")
    response = await _start(client, auth_headers, scenario_id, intensity_percent=10)
    try:
        status = await client.get(f"/api/v1/simulator/runs/{response.json()['run_id']}", headers=auth_headers)

        assert "episode_progress" in status.json() and status.json()["episode_progress"] is None, status.json()
    finally:
        await _stop(client, auth_headers, response)
