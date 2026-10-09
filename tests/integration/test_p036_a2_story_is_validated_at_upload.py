"""036 slice A, fix-delta of the external review of `1afe0b09` (findings 1, 3, 4, 5): what the upload accepts and what
the story of an accepted scenario means must be ONE rule.

Each test states the target and is red on `1afe0b09`; the controls (the same input in its valid spelling) are green
there and must stay green.

* finding 1 - `"time": 1000.0` is a valid JSON integer (draft 2020-12), yet the projection and the runner read only a
  Python `int`: the episode vanished. Integral numbers are normalised once, at ingestion.
* finding 3 - the money grammar of `amount` (event and anchor) is the product's money door: plain decimal, at most 18
  fraction digits and 50 digits in all, no sign or exponent or space, nothing after the last digit (a terminal newline
  included), storable in `Numeric(20, 8)` and positive. The accounting step of the equivalent is checked at execution
  (slice B), not here: upload reads no database.
* finding 4 - a `tx.updated` anchor names from, to, amount and equivalent; a `tx.failed` anchor names from, to and
  equivalent and NO amount (the SSE event has none); the other two events need only the event name (spec 036, "Якорь").
* finding 5 - a participant named by the story (focus, expected cycle, scripted payment, anchor) is one of the
  scenario's participants or one an `add_participant` inject introduces; the order in time is slice B's.
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
PREFIX = "p036-a2-"
CAPTION = {"ru": "р", "en": "e"}


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
    try:
        yield reg
    finally:
        for key in [k for k in runtime._scenarios if k.startswith(PREFIX)]:
            runtime._scenarios.pop(key, None)


def _scenario(name: str, events: list[dict], **top) -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": PREFIX + name,
        "equivalents": ["UAH"],
        "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}, {"id": "C", "type": "person"}],
        "trustlines": [],
        "events": events,
        **top,
    }


def _payment(amount, **extra) -> dict:
    return {"time": 1000, "type": "payment", "from": "A", "to": "B", "amount": amount, "equivalent": "UAH", **extra}


async def _upload(client, scenario: dict):
    client.cookies.clear()
    ensured = await client.post("/api/v1/simulator/session/ensure")
    assert ensured.status_code == 200, ensured.text
    return await client.post("/api/v1/simulator/scenarios", headers=ORIGIN, json={"scenario": scenario})


def _paths(response) -> list[str]:
    return [e["path"] for e in response.json()["error"]["details"]["errors"]]


# ------------------------------------------------------------------------------------------- finding 1: integral time


@pytest.mark.asyncio
async def test_an_integral_float_time_is_normalised_and_its_episode_survives(client, registry) -> None:
    scenario = _scenario("float-time", [{"time": 1000.0, "type": "note", "caption": CAPTION,
                                        "anchor": {"event": "clearing.done", "time_ms": 1000.0}}],
                         settings={"playback": {"intensity_percent": 0.0}})

    uploaded = await _upload(client, scenario)

    assert uploaded.status_code == 200, uploaded.text  # control: draft 2020-12 accepts an integral float as an integer
    raw = runtime._scenarios[PREFIX + "float-time"].raw
    assert [type(raw["events"][0]["time"]).__name__, type(raw["events"][0]["anchor"]["time_ms"]).__name__,
            type(raw["settings"]["playback"]["intensity_percent"]).__name__] == ["int", "int", "int"], raw
    detail = (await client.get(f"/api/v1/simulator/scenarios/{PREFIX}float-time")).json()
    assert [(e["index"], e["time_ms"]) for e in detail["episodes"]] == [(0, 1000)], detail["episodes"]
    assert detail["playback"]["intensity_percent"] == 0


@pytest.mark.asyncio
async def test_a_fractional_time_is_still_refused(client, registry) -> None:
    """Control: only a MATHEMATICALLY integral number is normalised."""

    response = await _upload(client, _scenario("frac-time", [{"time": 1000.5, "type": "note", "caption": CAPTION}]))

    assert response.status_code == 400, response.text
    assert _paths(response) == ["events/0/time"]


# --------------------------------------------------------------------------------------------- finding 3: the money


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["0.12345678", "00005.50", "1.500", "999999999999", "0." + "1" * 8])
async def test_control_a_well_formed_amount_is_accepted_everywhere_it_is_written(client, registry, amount) -> None:
    anchor = {"event": "tx.updated", "from": "A", "to": "B", "amount": amount, "equivalent": "UAH"}
    scenario = _scenario("ok-" + amount.replace(".", "-"),
                         [_payment(amount), {"time": 2000, "type": "note", "caption": CAPTION, "anchor": anchor}])

    response = await _upload(client, scenario)

    assert response.status_code == 200, response.text


BAD_AMOUNTS = {
    "19 fraction digits": "0.1234567890123456789",
    "9 fraction digits (not storable at scale 8)": "0.123456789",
    "a terminal newline": "5\n",
    "exponent": "1e3",
    "a sign": "-5",
    "a leading space": " 5",
    "zero": "0",
    "1e12 (capacity)": "1000000000000",
    "51 digits": "1" * 51,
    "a comma": "5,5",
    # the money door itself accepts Arabic-Indic digits (Decimal reads them); the scenario grammar is ASCII
    "an Arabic-Indic digit": "٣",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", list(BAD_AMOUNTS.values()), ids=list(BAD_AMOUNTS))
async def test_a_payment_amount_outside_the_money_grammar_is_refused(client, registry, amount) -> None:
    response = await _upload(client, _scenario("bad-pay", [_payment(amount)]))

    assert response.status_code == 400, f"payment amount {amount!r} answered {response.status_code}: {response.text[:200]}"
    assert _paths(response) == ["events/0/amount"], response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", list(BAD_AMOUNTS.values()), ids=list(BAD_AMOUNTS))
async def test_an_anchor_amount_outside_the_money_grammar_is_refused(client, registry, amount) -> None:
    anchor = {"event": "tx.updated", "from": "A", "to": "B", "amount": amount, "equivalent": "UAH"}

    response = await _upload(client, _scenario("bad-anchor", [{"time": 0, "type": "note", "caption": CAPTION,
                                                                "anchor": anchor}]))

    assert response.status_code == 400, f"anchor amount {amount!r} answered {response.status_code}: {response.text[:200]}"
    assert _paths(response) == ["events/0/anchor/amount"], response.text


# ----------------------------------------------------------------------------------------------- finding 4: anchors

FULL_UPDATED = {"event": "tx.updated", "from": "A", "to": "B", "amount": "5.00", "equivalent": "UAH"}


def _episode(anchor: dict) -> dict:
    return {"time": 0, "type": "note", "caption": CAPTION, "anchor": anchor}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "anchor",
    [
        {"event": "tx.updated"},
        {k: v for k, v in FULL_UPDATED.items() if k != "from"},
        {k: v for k, v in FULL_UPDATED.items() if k != "to"},
        {k: v for k, v in FULL_UPDATED.items() if k != "amount"},
        {k: v for k, v in FULL_UPDATED.items() if k != "equivalent"},
        {"event": "tx.failed", "from": "A", "to": "B"},
        {"event": "tx.failed", "from": "A", "equivalent": "UAH"},
        {"event": "tx.failed", "from": "A", "to": "B", "equivalent": "UAH", "amount": "5.00"},
    ],
    ids=["updated-bare", "updated-no-from", "updated-no-to", "updated-no-amount", "updated-no-equivalent",
         "failed-no-equivalent", "failed-no-to", "failed-with-amount"],
)
async def test_a_transaction_anchor_names_the_fields_the_spec_matches_on(client, registry, anchor) -> None:
    response = await _upload(client, _scenario("anchor", [_episode(anchor)]))

    assert response.status_code == 400, f"{anchor} answered {response.status_code}: {response.text[:200]}"
    assert all(p.startswith("events/0/anchor") for p in _paths(response)), response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "anchor",
    [FULL_UPDATED, {"event": "tx.failed", "from": "A", "to": "B", "equivalent": "UAH"}, {"event": "clearing.done"},
     {"event": "topology.changed", "time_ms": 3000}],
    ids=["updated", "failed", "clearing", "topology"],
)
async def test_control_the_complete_anchors_of_every_event_are_accepted(client, registry, anchor) -> None:
    response = await _upload(client, _scenario("anchor-ok", [_episode(anchor)]))

    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------------------- finding 5: participant refs

UNKNOWN = "NOBODY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "path"),
    [
        ({"time": 0, "type": "note", "caption": CAPTION, "focus": {"pids": ["A", UNKNOWN]}}, "events/0/focus/pids/1"),
        ({"time": 0, "type": "note", "caption": CAPTION, "focus": {"edges": [{"from": UNKNOWN, "to": "A"}]}},
         "events/0/focus/edges/0/from"),
        ({"time": 0, "type": "clearing", "caption": CAPTION, "expected_cycle": ["A", "B", UNKNOWN]},
         "events/0/expected_cycle/2"),
        ({"time": 0, "type": "payment", "from": UNKNOWN, "to": "B", "amount": "1"}, "events/0/from"),
        ({"time": 0, "type": "payment", "from": "A", "to": UNKNOWN, "amount": "1"}, "events/0/to"),
        ({"time": 0, "type": "note", "caption": CAPTION,
          "anchor": {"event": "tx.failed", "from": "A", "to": UNKNOWN, "equivalent": "UAH"}}, "events/0/anchor/to"),
    ],
    ids=["focus-pid", "focus-edge", "cycle", "payment-from", "payment-to", "anchor-to"],
)
async def test_a_story_may_only_name_participants_of_the_scenario(client, registry, event, path) -> None:
    response = await _upload(client, _scenario("refs", [event]))

    assert response.status_code == 400, response.text
    assert _paths(response) == [path], response.text


@pytest.mark.asyncio
async def test_control_a_participant_introduced_by_an_inject_may_be_named(client, registry) -> None:
    inject = {"time": 0, "type": "inject", "effects": [
        {"op": "add_participant", "participant": {"id": "NEWCOMER", "type": "person"}}]}
    story = {"time": 5000, "type": "note", "caption": CAPTION, "focus": {"pids": ["NEWCOMER"]}}

    response = await _upload(client, _scenario("refs-ok", [inject, story]))

    assert response.status_code == 200, response.text
