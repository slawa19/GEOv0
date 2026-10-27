"""050 frozen product targets, measured from real PostgreSQL money and HTTP SSE replay.

Counters/progress are sampled only after the runtime's running post-tick heartbeat;
GET tick_index alone can expose an in-flight money phase and is not completion.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import copy
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.core.simulator.runtime import runtime
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from scripts.measure_scenario_economy import measure_live
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_simulator_super_smoke import _read_sse_events

pytestmark = [MODE_B, pytest.mark.slow]
SCENARIOS = ("greenfield-village-100-realistic-v2", "riverside-town-50-realistic-v2")
# Frozen by spec 050 Numeric freeze T5002, 2026-10-27 BEFORE regeneration/live.
MIN_CLEARED_TURNOVER_SHARE = Decimal("0.04430853798956837468")
MIN_OPENING_CLEARED_SHARE = Decimal("0.5")
MAX_REJECT_SHARE = Decimal("0.19")
MIN_ROLE_SHARE = Decimal("0.0027")
MIN_ASSIGNED_PAIR_SHARE = Decimal("0.75")
ROLES = {"anchors", "producers", "retail", "services", "households", "agents"}


@pytest_asyncio.fixture
async def stopped_runs(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    # ASGITransport buffers until terminal status, so this test replays a full
    # retained launch at once. The ordinary 500-event subscriber queue cannot
    # hold the observed >700-ID prefix. Reserve the whole configured buffer plus
    # the stream bootstrap; this changes transport capture capacity only.
    monkeypatch.setattr(runtime, "_sse_sub_queue_max", runtime._event_buffer_max + 1)
    started = []
    try:
        yield started
    finally:
        for run_id in started:
            try:
                await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)
            finally:
                await runtime.stop(run_id)


async def _post(client, auth, run_id, action):
    response = await client.post(f"/api/v1/simulator/runs/{run_id}/{action}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


async def _get(client, auth, run_id, suffix="", **params):
    response = await client.get(f"/api/v1/simulator/runs/{run_id}{suffix}", headers=auth, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def _start(client, auth, stopped_runs, monkeypatch, scenario_id, *, intensity):
    # A test-local playback copy changes only wall-clock pacing, never economy data.
    record = runtime.get_scenario(scenario_id)
    scenario = copy.deepcopy(record.raw)
    scenario.setdefault("settings", {}).setdefault("playback", {})["tick_seconds"] = .25
    monkeypatch.setitem(runtime._scenarios, scenario_id, replace(record, raw=scenario))
    response = await client.post("/api/v1/simulator/runs", headers=auth,
                                 json={"scenario_id": scenario_id, "mode": "real", "intensity_percent": intensity})
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    stopped_runs.append(run_id)
    await _post(client, auth, run_id, "pause")
    run = runtime.get_run(run_id)
    assert run.tick_index == 0, "initial money began before the initial snapshot"
    run.seed = 42  # the product's current HTTP API derives a seed from run_id; freeze this test's planner input.
    await _get(client, auth, run_id, "/actions/trustlines-list", equivalent="UAH")
    return run_id, copy.deepcopy(run._scenario_raw)


async def _capture(client, auth, run_id, *, ticks):
    run = runtime.get_run(run_id)
    assert run.seed == 42 and runtime._tick_seconds_of(run) == .25
    assert runtime._real_runner._tick._clearing_every_n_ticks == 25, "frozen live cadence differs from runtime"
    clearing_budget_ms = runtime._real_runner._tick._real_clearing_time_budget_ms
    clearing_hard_timeout_sec = runtime._real_runner._tick.clearing_hard_timeout_sec()
    assert clearing_budget_ms == 5000, "approved live clearing budget differs from captured runtime"
    assert clearing_hard_timeout_sec == 8.0, "approved live hard-timeout cap differs from runtime"
    initial_status = await _get(client, auth, run_id)
    assert initial_status["sim_time_ms"] == 0
    initial_snapshot = await _get(client, auth, run_id, "/graph/snapshot", equivalent="UAH")
    cursor = runtime.publish_run_status(run_id)
    launch = {"epoch": run._launch_epoch, "run_id": run_id, "initial_snapshot": initial_snapshot,
              "initial_status": initial_status, "start_event_id": cursor, "events": [],
              "completed_ticks": [], "expected_ticks": ticks, "clearing_every": 25,
              "parameters": {"seed": 42, "intensity_percent": run.intensity_percent, "tick_seconds": .25,
                             "clearing_budget_ms": clearing_budget_ms,
                             "clearing_hard_timeout_sec": clearing_hard_timeout_sec,
                             "subscriber_queue_capacity": runtime._sse_sub_queue_max}}
    observer_events = []
    observer = await runtime.subscribe(run_id, equivalent="UAH", after_event_id=cursor)
    try:
        for tick in range(1, ticks + 1):
            await _post(client, auth, run_id, "resume")
            async with asyncio.timeout(180):
                while True:
                    event = await observer.queue.get()
                    observer_events.append(event)
                    # Resume/pause publish statuses too. Only a new running time
                    # is the heartbeat emitted AFTER await tick_real_mode.
                    if event.get("type") == "run_status" and event.get("state") == "running" and event.get("sim_time_ms") == tick * 1000:
                        break
            await _post(client, auth, run_id, "pause")
            status = await _get(client, auth, run_id)
            assert status["sim_time_ms"] == tick * 1000, "collector overshot completed tick"
            launch["completed_ticks"].append({"tick": tick, "event_id": event["event_id"], "status": status})
            assert status["state"] == "paused" and status["errors_total"] == 0, status
        await _post(client, auth, run_id, "stop")
    finally:
        await runtime.unsubscribe(run_id, observer)
        # Persist completion/progress evidence even if transport replay fails.
        # Observer events are explicitly diagnostic; acceptance requires events
        # populated by the HTTP endpoint below.
        launch["observer_events_debug_only"] = observer_events
        _dump(run._scenario_raw, {"launches": [launch]})
    # ASGITransport buffers streaming responses: terminate through the real stop
    # path first, then exercise the actual HTTP Last-Event-ID replay endpoint.
    raw = await _read_sse_events(client, run_id=run_id, eq="UAH", auth_headers=auth,
                                 last_event_id=cursor, timeout_sec=30)
    launch["events"] = [{"id": e["event_id"], "type": e["type"], "payload": e} for e in raw]
    _dump(run._scenario_raw, {"launches": [launch]})
    return launch


async def _db_debts(db_session, scenario):
    own = {p["id"] for p in scenario["participants"]}
    debtor, creditor = aliased(Participant), aliased(Participant)
    async with sessionmaker_of(db_session)() as read:
        rows = (await read.execute(select(debtor.pid, creditor.pid, Equivalent.code, Debt.amount)
                .join(debtor, debtor.id == Debt.debtor_id).join(creditor, creditor.id == Debt.creditor_id)
                .join(Equivalent, Equivalent.id == Debt.equivalent_id)
                .where(Debt.amount > 0, debtor.pid.in_(own) | creditor.pid.in_(own)))).all()
    return {(d, c, eq): amount for d, c, eq, amount in rows}


def _dump(scenario, recording, report=None):
    if not recording["launches"]:
        return  # Do not mask the original capture failure with artifact naming.
    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT", ".local-run/test-runs/direct-pytest/artifacts")) / "p050-live"
    root.mkdir(parents=True, exist_ok=True)
    stem = f"{scenario['scenario_id']}-{recording['launches'][0]['run_id']}-epoch{recording['launches'][0]['epoch']}"
    for suffix, data in (("scenario", scenario), ("recording", recording), ("report", report)):
        if data is not None:
            (root / f"{stem}-{suffix}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _at_least(stat, threshold, context):
    assert stat["denominator"] > 0 and stat["share"] is not None, (context, stat)
    actual = Decimal(stat["share"])
    assert actual >= threshold, f"{context}: numerator={stat['numerator']} denominator={stat['denominator']} actual={actual} threshold={threshold}"



def _nominal_opening(scenario):
    payments = [e for e in scenario["events"] if e["type"] == "payment"]
    assert payments and all(e["equivalent"] == "UAH" for e in payments), "expected positive single-equivalent opening"
    nominal = sum((Decimal(e["amount"]) for e in payments), Decimal(0))
    assert nominal > 0
    return nominal


def _assert_positive_opening_basis(clearings, nominal):
    opening = clearings[25]
    assert opening["denominator_basis"] == "nominal_opening_gross", opening
    assert Decimal(opening["denominator"]) == nominal > 0, opening
    for tick in (50, 75, 100):
        clearing = clearings[tick]
        assert clearing["denominator_basis"] == "normal_successful_turnover", clearing
        assert Decimal(clearing["denominator"]) == Decimal(clearing["normal_successful_turnover"]), clearing


def _accept(report, scenario):
    scenario_id = scenario["scenario_id"]
    nominal = _nominal_opening(scenario)  # Independent input from the bound opening events, never from the measurer.
    launch = report["launches"][0]
    assert Decimal(launch["scripted"]["nominal_gross"]) == nominal
    clearings = {c["tick"]: c for c in launch["clearings"]}
    _assert_positive_opening_basis(clearings, nominal)
    normal = launch["normal"]
    rejected = normal["rejection"]
    assert rejected["denominator"] > 0 and rejected["share"] is not None, rejected
    assert Decimal(rejected["share"]) <= MAX_REJECT_SHARE, f"{scenario_id} rejection actual={rejected} threshold={MAX_REJECT_SHARE}"
    _at_least(normal["assigned"], MIN_ASSIGNED_PAIR_SHARE, f"{scenario_id} assigned")
    for role in sorted(ROLES):
        for direction in ("role_send", "role_receive"):
            _at_least(normal[direction][role], MIN_ROLE_SHARE, f"{scenario_id} {role} {direction}")
    assert not launch["stall_ticks"], f"{scenario_id} stall at completed ticks {launch['stall_ticks']}"
    for tick in (25, 50, 75, 100):
        clearing = clearings[tick]
        numerator = Decimal(clearing["cleared_edge_debt"])
        denominator = nominal if tick == 25 else Decimal(clearing["normal_successful_turnover"])
        threshold = MIN_OPENING_CLEARED_SHARE if tick == 25 else MIN_CLEARED_TURNOVER_SHARE
        assert denominator > 0, f"{scenario_id} tick={tick} missing/zero turnover {clearing}"
        assert numerator >= threshold * denominator, f"{scenario_id} tick={tick} D_w={numerator} T_w={denominator} actual={numerator/denominator} threshold={threshold}"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id", SCENARIOS)
async def test_realistic_economy_survives_100_completed_ticks_and_restart(client, auth_headers, db_session, stopped_runs, monkeypatch, scenario_id):
    run_id, scenario = await _start(client, auth_headers, stopped_runs, monkeypatch, scenario_id, intensity=15)
    recording = {"launches": []}
    try:
        recording["launches"].append(await _capture(client, auth_headers, run_id, ticks=100))
        report = measure_live(scenario, recording)
        _dump(scenario, recording, report)
        _accept(report, scenario)
        before = await _db_debts(db_session, scenario)
        restarted = await _post(client, auth_headers, run_id, "restart")
        await _post(client, auth_headers, run_id, "pause")
        assert restarted["sim_time_ms"] == 0 and not restarted.get("episode_progress"), restarted
        assert runtime.get_run(run_id)._launch_epoch == 1
        assert await _db_debts(db_session, scenario) == before, "restart reset the committed debt"
        recording["launches"].append(await _capture(client, auth_headers, run_id, ticks=25))
        report = measure_live(scenario, recording)
        _dump(scenario, recording, report)
        second = report["launches"][1]
        assert not second["stall_ticks"], second
        assert Decimal(second["clearings"][0]["cleared_edge_debt"]) > 0, second["clearings"]
    finally:
        _dump(scenario, recording)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id", SCENARIOS)
async def test_opening_at_tempo_zero_is_exact_direct_hop_debt_before_first_clearing(client, auth_headers, db_session, stopped_runs, monkeypatch, scenario_id):
    run_id, scenario = await _start(client, auth_headers, stopped_runs, monkeypatch, scenario_id, intensity=0)
    recording = {"launches": [await _capture(client, auth_headers, run_id, ticks=20)]}
    report = measure_live(scenario, recording)
    _dump(scenario, recording, report)
    expected = Counter()
    events = [e for e in scenario["events"] if e["type"] == "payment"]
    for event in events:
        expected[event["from"], event["to"], event["equivalent"]] += Decimal(event["amount"])
    assert await _db_debts(db_session, scenario) == dict(expected), "opening debt map (including unexpected intermediate edges) differs"
    statuses = recording["launches"][0]["completed_ticks"][-1]["status"]["episode_progress"]
    assert len(statuses) == len(events) and all(p["status"] == "done" and p["attempts"] == 1 for p in statuses), statuses
    observed = Counter()
    for envelope in recording["launches"][0]["events"]:
        event = envelope["payload"]
        if event["type"] == "tx.updated":
            assert len(event["edges"]) == 1, event
            assert (event["edges"][0]["from"], event["edges"][0]["to"]) == (event["from"], event["to"]), event
            observed[event["from"], event["to"], event["equivalent"], Decimal(event["amount"])] += 1
    assert observed == Counter((e["from"], e["to"], e["equivalent"], Decimal(e["amount"])) for e in events)


def _synthetic(monkeypatch, *, opening=False):
    """Supported indirect route S→M→D; a separate X→D line leaves receiver headroom."""
    scenario_id = SCENARIOS[0]
    record = runtime.get_scenario(scenario_id)
    scenario = {
        "scenario_id": scenario_id, "name": "050 runtime counterproof", "equivalents": ["UAH"],
        "participants": [{"id": pid, "name": pid, "type": "business", "status": "active", "groupId": pid,
                          "behaviorProfileId": pid} for pid in ("S", "M", "D", "X")],
        "trustlines": [{"from": receiver, "to": sender, "equivalent": "UAH", "limit": "100.00",
                        "policy": {"can_be_intermediate": True}} for sender, receiver in (("S", "M"), ("M", "D"), ("X", "D"))],
        "behaviorProfiles": [{"id": pid, "props": {"tx_rate": 1 if pid == "S" else 0,
                              "recipient_group_weights": {"D": 1}, "amount_model": {"UAH": {"min": 1, "max": 1}}}}
                             for pid in ("S", "M", "D", "X")],
        "settings": {"playback": {"tick_seconds": .25}, "trust_drift": {"enabled": False}},
        "events": ([{"time": 1000, "type": "payment", "from": "S", "to": "D", "equivalent": "UAH", "amount": "5.00"}]
                   if opening else []),
    }
    monkeypatch.setitem(runtime._scenarios, scenario_id, replace(record, raw=scenario))
    return scenario_id


@pytest.mark.asyncio
async def test_runtime_capacity_refusals_cannot_pass_a_zero_or_missing_reject_metric(client, auth_headers, db_session, stopped_runs, monkeypatch):
    scenario_id = _synthetic(monkeypatch)
    run_id, scenario = await _start(client, auth_headers, stopped_runs, monkeypatch, scenario_id, intensity=100)
    people = {p["name"]: p["id"] for p in scenario["participants"]}
    # Execute real money through the ordinary action; no fixture debt injection.
    response = await client.post(f"/api/v1/simulator/runs/{run_id}/actions/payment-real", headers=auth_headers,
                                 json={"from_pid": people["M"], "to_pid": people["D"], "equivalent": "UAH", "amount": "100.00"})
    assert response.status_code == 200, response.text
    assert await _db_debts(db_session, scenario) == {(people["M"], people["D"], "UAH"): Decimal("100.00")}
    recording = {"launches": [await _capture(client, auth_headers, run_id, ticks=3)]}
    report = measure_live(scenario, recording)
    _dump(scenario, recording, report)
    launch = report["launches"][0]
    reject = launch["normal"]["rejection"]
    assert reject["denominator"] > 0 and reject["share"] is not None, reject
    assert launch["normal"]["errors"].get("ROUTING_NO_CAPACITY", 0) > 0, launch
    assert Decimal(reject["share"]) > MAX_REJECT_SHARE, f"runtime badcase actual={reject} threshold={MAX_REJECT_SHARE}"
    assert launch["stall_ticks"] == [3], launch
    assert launch["clearings"] == [] and launch["windows"]["steady"] is None, "unobserved windows must remain unmeasured"


@pytest.mark.asyncio
async def test_an_opening_pair_without_a_direct_line_creates_intermediate_debt_and_fails_exact_map(client, auth_headers, db_session, stopped_runs, monkeypatch):
    scenario_id = _synthetic(monkeypatch, opening=True)
    run_id, scenario = await _start(client, auth_headers, stopped_runs, monkeypatch, scenario_id, intensity=0)
    recording = {"launches": [await _capture(client, auth_headers, run_id, ticks=1)]}
    report = measure_live(scenario, recording)
    _dump(scenario, recording, report)
    event = scenario["events"][0]
    debts = await _db_debts(db_session, scenario)
    expected = {(event["from"], event["to"], "UAH"): Decimal("5.00")}
    with pytest.raises(AssertionError, match="opening debt map"):
        assert debts == expected, "opening debt map differs"
    people = {p["name"]: p["id"] for p in scenario["participants"]}
    assert debts == {(people["S"], people["M"], "UAH"): Decimal("5.00"),
                     (people["M"], people["D"], "UAH"): Decimal("5.00")}
    paid = [e["payload"] for e in recording["launches"][0]["events"] if e["type"] == "tx.updated"]
    assert len(paid) == 1 and len(paid[0]["edges"]) == 2, paid
