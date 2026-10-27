import json
from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import measure_scenario_economy as economy


def scenario(*, scripted=False):
    return {
        "scenario_id": "bound",
        "equivalents": ["UAH"],
        "participants": [
            {"id": p, "groupId": role}
            for p, role in (("a", "households"), ("b", "retail"), ("c", "producers"))
        ],
        "trustlines": [
            {"from": b, "to": a, "equivalent": "UAH", "limit": "1000"}
            for a, b in (("a", "b"), ("b", "c"), ("c", "a"))
        ],
        "events": (
            [
                {
                    "type": "payment",
                    "time": 1000,
                    "from": "a",
                    "to": "b",
                    "amount": "1.00000001",
                    "equivalent": "UAH",
                }
            ]
            if scripted
            else []
        ),
    }


def recording(
    s, *, ticks=1, fail=False, epoch=0, intensity=15, empty=False, clear=True
):
    """Complete synthetic observations; normal first, scripted after it, absolute patches."""
    run_id, seq = "run_bound", 10
    debt = Decimal(0)
    counts = {
        "attempts_total": 0,
        "committed_total": 0,
        "rejected_total": 0,
        "errors_total": 0,
    }

    def status(tick, progress):
        return {
            "run_id": run_id,
            "sim_time_ms": tick * 1000,
            "intensity_percent": intensity,
            "episode_progress": deepcopy(progress),
            **counts,
        }

    launch = {
        "run_id": run_id,
        "epoch": epoch,
        "expected_ticks": ticks,
        "clearing_every": 25,
        "start_event_id": f"evt_{run_id}_{seq:06d}",
        "initial_status": status(0, []),
        "initial_snapshot": {
            "equivalent": "UAH",
            "links": [
                {
                    "source": e["from"],
                    "target": e["to"],
                    "used": "0",
                    "trust_limit": "1000",
                }
                for e in s["trustlines"]
            ],
        },
        "events": [],
        "completed_ticks": [],
    }

    def emit(kind, payload):
        nonlocal seq
        seq += 1
        event_id = f"evt_{run_id}_{seq:06d}"
        payload = {"event_id": event_id, "type": kind, **payload}
        launch["events"].append({"id": event_id, "type": kind, "payload": payload})
        return event_id

    progress = []
    for tick in range(1, ticks + 1):
        payments = (
            [] if empty else [("tx.failed" if fail else "tx.updated", "2.00000001")]
        )
        if tick == 1 and s["events"]:
            event = s["events"][0]
            payments.append(("tx.updated", event["amount"]))
            progress = [
                {
                    "index": 0,
                    "epoch": epoch,
                    "kind": "payment",
                    "status": "done",
                    "attempts": 1,
                    "payment": {
                        key: event[key]
                        for key in ("from", "to", "amount", "equivalent")
                    },
                }
            ]
        for kind, amount in payments:
            counts["attempts_total"] += 1
            p = {"equivalent": "UAH", "from": "a", "to": "b"}
            if kind == "tx.updated":
                counts["committed_total"] += 1
                debt += Decimal(amount)
                p.update(
                    amount=amount,
                    edge_patch=[{"source": "b", "target": "a", "used": str(debt)}],
                )
            else:
                counts["rejected_total"] += 1
                p["error"] = {"code": "NO_CAPACITY"}
            emit(kind, p)
        if tick % 25 == 0 and clear:
            debt -= Decimal(1)
            emit(
                "clearing.done",
                {
                    "equivalent": "UAH",
                    "cleared_amount": "0.33333333",
                    "edge_patch": [{"source": "b", "target": "a", "used": str(debt)}],
                },
            )
        state = status(tick, progress)
        event_id = emit(
            "run_status",
            {key: value for key, value in state.items() if key != "episode_progress"},
        )
        launch["completed_ticks"].append(
            {"tick": tick, "event_id": event_id, "status": state}
        )
    return {"launches": [launch]}


def measured(s, r):
    return economy.measure_live(s, r)["launches"][0]


def test_one_completed_tick_runtime_rejection_is_not_assumed_full_run():
    s = scenario()
    report = measured(s, recording(s, fail=True))
    assert report["completed_ticks"] == 1
    assert report["normal"]["rejection"] == {
        "numerator": 1,
        "denominator": 1,
        "share": "1",
    }
    assert report["normal"]["assigned"]["share"] == "0"
    assert report["normal"]["errors"] == {"NO_CAPACITY": 1}
    assert report["windows"]["steady"] is None
    assert report["clearings"] == []


def test_full_prefix_money_is_decimal_absolute_and_clearing_uses_edge_debt():
    s = scenario(scripted=True)
    report = measured(s, recording(s, ticks=100))
    assert report["normal"]["attempts"] == 100
    assert report["normal"]["successful_gross"] == "200.00000100"
    assert report["scripted"]["successful_gross"] == "1.00000001"
    assert report["windows"]["opening"]["attempts"] == 20
    assert report["windows"]["steady"]["attempts"] == 80
    assert [c["tick"] for c in report["clearings"]] == [25, 50, 75, 100]
    for c in report["clearings"]:
        assert Decimal(c["cleared_edge_debt"]) == 1
        assert Decimal(c["cleared_cycle_amount"]) == Decimal("0.33333333")
        assert Decimal(c["normal_successful_turnover"]) == Decimal("50.00000025")
    assert Decimal(report["clearings"][0]["denominator"]) == Decimal("1.00000001")
    assert Decimal(report["clearings"][1]["denominator"]) == Decimal("50.00000025")
    assert (
        next(e["used"] for e in report["final_debt_by_edge"] if e["source"] == "b")
        == "197.00000101"
    )


def test_first_clearing_without_opening_uses_successful_normal_turnover():
    s = scenario(scripted=False)
    report = measured(s, recording(s, ticks=25))
    clearing = report["clearings"][0]
    assert report["normal"]["successful"] == 25
    assert report["scripted"]["nominal_gross"] == "0"
    assert clearing["denominator_basis"] == "normal_successful_turnover"
    assert Decimal(clearing["denominator"]) == Decimal("50.00000025")
    assert Decimal(clearing["cleared_edge_debt"]) == 1
    assert Decimal(clearing["share"]) == Decimal(1) / Decimal("50.00000025")


@pytest.mark.parametrize("empty", [False, True])
def test_first_clearing_without_opening_rejects_zero_successful_turnover(empty):
    s = scenario(scripted=False)
    with pytest.raises(ValueError, match="zero scheduled clearing denominator"):
        measured(s, recording(s, ticks=25, fail=True, empty=empty, clear=False))


@pytest.mark.parametrize("nominal", ["31200", "30900"])
def test_positive_opening_keeps_frozen_nominal_denominator(nominal):
    s = scenario(scripted=True)
    s["events"][0]["amount"] = nominal
    report = measured(s, recording(s, ticks=50))
    first, second = report["clearings"]
    assert first["denominator_basis"] == "nominal_opening_gross"
    assert Decimal(first["denominator"]) == Decimal(nominal)
    assert Decimal(first["share"]) == Decimal(1) / Decimal(nominal)
    assert second["denominator_basis"] == "normal_successful_turnover"
    assert Decimal(second["denominator"]) == Decimal("50.00000025")


def test_identical_script_and_normal_tuple_outcome_is_reconciled_once():
    s = scenario(scripted=True)
    s["events"][0]["amount"] = "2.00000001"
    report = measured(s, recording(s))
    assert report["normal"]["successful"] == report["scripted"]["successful"] == 1
    assert (
        report["normal"]["successful_gross"]
        == report["scripted"]["successful_gross"]
        == "2.00000001"
    )


def test_success_role_and_assigned_numerators_include_failures_in_denominator():
    s = scenario()
    r = recording(s, ticks=2)
    launch = r["launches"][0]
    p = launch["events"][0]["payload"]
    p["type"] = launch["events"][0]["type"] = "tx.failed"
    p.pop("amount")
    p.pop("edge_patch")
    p["error"] = {"code": "NO_CAPACITY"}
    for completion in launch["completed_ticks"]:
        for target in (
            completion["status"],
            next(
                e["payload"]
                for e in launch["events"]
                if e["id"] == completion["event_id"]
            ),
        ):
            target["committed_total"] -= 1
            target["rejected_total"] += 1
    normal = measured(s, r)["normal"]
    assert normal["rejection"]["share"] == "0.5"
    assert normal["assigned"]["share"] == "0.5"
    assert normal["role_send"]["households"]["share"] == "0.5"
    assert normal["role_receive"]["retail"]["share"] == "0.5"
    assert normal["attempt_assigned"]["share"] == "1"


def test_exact_replay_duplicates_are_deduplicated_without_double_money():
    s = scenario()
    r = recording(s)
    r["launches"][0]["events"].insert(1, deepcopy(r["launches"][0]["events"][0]))
    assert measured(s, r)["normal"]["attempts"] == 1


@pytest.mark.parametrize(
    "damage, message",
    [
        ("gap", "gap"),
        ("conflict", "Conflicting duplicate"),
        ("completion", "completed tick coverage"),
        ("patch", "money edge patch"),
        ("used", "absolute used"),
        ("amount", "money measurement"),
        ("counters", "counters"),
        ("progress", "scripted episode progress"),
        ("epoch", "epoch"),
        ("outcome", "multiset"),
        ("payment", "mismatch"),
        ("attempts", "history"),
    ],
)
def test_required_evidence_fails_closed(damage, message):
    s = scenario(scripted=True)
    r = recording(s)
    l = r["launches"][0]
    if damage == "gap":
        l["events"].pop(0)
    elif damage == "conflict":
        duplicate = deepcopy(l["events"][0])
        duplicate["payload"]["amount"] = "3"
        l["events"].append(duplicate)
    elif damage == "completion":
        l["completed_ticks"] = []
    elif damage == "patch":
        l["events"][0]["payload"].pop("edge_patch")
    elif damage == "used":
        l["events"][0]["payload"]["edge_patch"][0].pop("used")
    elif damage == "amount":
        l["events"][0]["payload"].pop("amount")
    elif damage == "counters":
        l["completed_ticks"][0]["status"]["attempts_total"] += 1
    elif damage == "progress":
        l["completed_ticks"][0]["status"]["episode_progress"] = []
    else:
        p = l["completed_ticks"][0]["status"]["episode_progress"][0]
        if damage == "epoch":
            p["epoch"] += 1
        elif damage == "outcome":
            p.update(status="refused", reason="NO_CAPACITY")
        elif damage == "payment":
            p["payment"]["to"] = "c"
        elif damage == "attempts":
            p["attempts"] = 2
    with pytest.raises(ValueError, match=message):
        measured(s, r)


def test_zero_normal_is_missing_not_perfect_rejection_share():
    s = scenario()
    with pytest.raises(ValueError, match="normal attempt denominator"):
        measured(s, recording(s, empty=True))
    normal = measured(s, recording(s, empty=True, intensity=0))["normal"]
    assert normal["attempts"] == 0 and normal["rejection"]["share"] is None


def test_complete_empty_scheduled_clearing_is_zero_and_future_is_unavailable():
    s = scenario(scripted=True)
    report = measured(s, recording(s, ticks=50, clear=False))
    assert [c["cleared_edge_debt"] for c in report["clearings"]] == ["0", "0"]
    assert report["clearings"][1]["share"] == "0"
    assert len(report["clearings"]) == 2
    r = recording(s, ticks=50, clear=False)
    r["launches"][0]["completed_ticks"].pop()
    with pytest.raises(ValueError, match="coverage"):
        measured(s, r)


def test_scheduled_zero_successful_turnover_fails_instead_of_vacuous_pass():
    s = scenario(scripted=True)
    with pytest.raises(ValueError, match="clearing denominator"):
        measured(s, recording(s, ticks=50, fail=True, clear=False))


def test_clearing_missing_patch_is_missing_even_when_cycle_amount_is_present():
    s = scenario(scripted=True)
    r = recording(s, ticks=25)
    next(
        e["payload"] for e in r["launches"][0]["events"] if e["type"] == "clearing.done"
    ).pop("edge_patch")
    with pytest.raises(ValueError, match="money edge patch"):
        measured(s, r)


def test_restart_progress_and_money_are_isolated_per_launch():
    s = scenario(scripted=True)
    r = recording(s)
    restarted = recording(s, epoch=1)["launches"][0]
    r["launches"].append(restarted)
    report = economy.measure_live(s, r)
    assert [x["scripted"]["successful"] for x in report["launches"]] == [1, 1]
    restarted["completed_ticks"][0]["status"]["episode_progress"][0]["epoch"] = 0
    with pytest.raises(ValueError, match="epoch"):
        economy.measure_live(s, r)


def fail_script(l, *, incomplete=False):
    event = l["events"][1]
    event["type"] = event["payload"]["type"] = "tx.failed"
    event["payload"].pop("amount")
    event["payload"].pop("edge_patch")
    event["payload"]["error"] = {"code": "NO_CAPACITY"}
    completion = l["completed_ticks"][0]
    progress = completion["status"]["episode_progress"][0]
    progress.update(
        status="incomplete" if incomplete else "refused", reason="NO_CAPACITY"
    )
    for state in (completion["status"], l["events"][2]["payload"]):
        state["committed_total"] -= 1
        state["rejected_total"] += 1


def test_identical_failed_endpoints_keep_scripted_gross_and_normal_count_separate():
    s = scenario(scripted=True)
    r = recording(s, fail=True)
    fail_script(r["launches"][0])
    report = measured(s, r)
    assert report["normal"]["attempts"] == report["normal"]["failed"] == 1
    assert report["normal"]["successful_gross"] == "0"
    assert report["scripted"]["failed"] == 1
    assert report["scripted"]["refused_gross"] == "1.00000001"


def test_scripted_retry_retains_first_failure_and_second_success():
    s = scenario(scripted=True)
    r = recording(s)
    l = r["launches"][0]
    fail_script(l, incomplete=True)
    first = deepcopy(l["completed_ticks"][0]["status"]["episode_progress"][0])
    retry = recording(s)["launches"][0]
    # Second tick has a normal plus retried script and cumulative counters.
    for event in retry["events"]:
        number = int(event["id"].rsplit("_", 1)[1]) + 3
        event["id"] = event["payload"]["event_id"] = f"evt_run_bound_{number:06d}"
        if event["type"] == "run_status":
            event["payload"].update(
                sim_time_ms=2000, attempts_total=4, committed_total=3, rejected_total=1
            )
    retry_status = retry["completed_ticks"][0]["status"]
    retry_status.update(
        sim_time_ms=2000, attempts_total=4, committed_total=3, rejected_total=1
    )
    retry_status["episode_progress"][0]["attempts"] = 2
    l["events"].extend(retry["events"])
    l["completed_ticks"].append(
        {"tick": 2, "event_id": retry["events"][-1]["id"], "status": retry_status}
    )
    l["expected_ticks"] = 2
    report = measured(s, r)
    assert report["normal"]["successful"] == 2
    assert report["scripted"]["failed"] == report["scripted"]["successful"] == 1
    assert report["scripted"]["refused_gross"] == "0"
    assert report["scripted"]["incomplete_gross"] == "1.00000001"
    assert report["scripted"]["errors"] == {"NO_CAPACITY": 1}
    assert l["completed_ticks"][0]["status"]["episode_progress"][0] == first


def test_initial_assignment_survives_topology_changes_and_parser_does_not_mutate_input():
    s = scenario()
    r = recording(s)
    l = r["launches"][0]
    original = deepcopy(l["events"][0])
    l["events"][0] = {
        "id": original["id"],
        "type": "topology.changed",
        "payload": {
            "event_id": original["id"],
            "type": "topology.changed",
            "equivalent": "UAH",
            "payload": {
                "added_edges": [
                    {
                        "from_pid": "c",
                        "to_pid": "a",
                        "equivalent_code": "UAH",
                        "limit": "10",
                    }
                ]
            },
        },
    }
    original["id"] = original["payload"]["event_id"] = "evt_run_bound_000012"
    original["payload"].update(
        to="c", edge_patch=[{"source": "c", "target": "a", "used": "2"}]
    )
    l["events"].insert(1, original)
    l["events"][-1]["id"] = l["events"][-1]["payload"]["event_id"] = (
        "evt_run_bound_000013"
    )
    l["completed_ticks"][0]["event_id"] = "evt_run_bound_000013"
    before = deepcopy(r)
    report = measured(s, r)
    assert report["normal"]["assigned"]["share"] == "0"
    assert report["max_debt_limit_sample"] == {
        "tick": 1,
        "source": "c",
        "target": "a",
        "used": "2",
        "trust_limit": "10",
        "ratio": "0.2",
    }
    assert report == measured(s, r)
    assert r == before


@pytest.mark.parametrize("raw", [None, 0.1, "NaN", "Infinity", "-1", "not-money"])
def test_missing_inexact_and_invalid_money_is_rejected(raw):
    s = scenario()
    r = recording(s)
    r["launches"][0]["events"][0]["payload"]["amount"] = raw
    with pytest.raises(ValueError):
        measured(s, r)


def test_stall_evidence_and_terminal_pause_status_are_retained():
    s = scenario()
    r = recording(s, fail=True)
    l = r["launches"][0]
    l["completed_ticks"][0]["status"]["consec_all_rejected_ticks"] = 3
    l["events"][-1]["payload"]["consec_all_rejected_ticks"] = 3
    terminal = deepcopy(l["events"][-1])
    terminal["id"] = terminal["payload"]["event_id"] = "evt_run_bound_000013"
    terminal["payload"]["state"] = "stopped"
    l["events"].append(terminal)
    assert measured(s, r)["stall_ticks"] == [1]
    terminal["payload"]["attempts_total"] += 1
    with pytest.raises(ValueError, match="trailing"):
        measured(s, r)


def test_money_after_claimed_completion_is_not_forged_complete():
    s = scenario()
    r = recording(s)
    l = r["launches"][0]
    trailing = deepcopy(l["events"][0])
    trailing["id"] = trailing["payload"]["event_id"] = "evt_run_bound_000013"
    l["events"].append(trailing)
    with pytest.raises(ValueError, match="trailing"):
        measured(s, r)


def test_clearing_precedes_growth_and_optional_cycle_diagnostic_is_unavailable():
    s = scenario(scripted=True)
    r = recording(s, ticks=25)
    l = r["launches"][0]
    clearing = l["events"][-2]["payload"]
    clearing.pop("cleared_amount")
    status = l["events"][-1]
    l["events"].insert(
        -1,
        {
            "id": status["id"],
            "type": "topology.changed",
            "payload": {
                "event_id": status["id"],
                "type": "topology.changed",
                "equivalent": "UAH",
                "payload": {
                    "edge_patch": [
                        {**clearing["edge_patch"][0], "trust_limit": "10000"}
                    ]
                },
            },
        },
    )
    new_id = f"evt_run_bound_{int(status['id'].rsplit('_', 1)[1]) + 1:06d}"
    status["id"] = status["payload"]["event_id"] = l["completed_ticks"][-1][
        "event_id"
    ] = new_id
    report = measured(s, r)
    assert Decimal(report["clearings"][0]["cleared_edge_debt"]) == 1
    assert report["clearings"][0]["cleared_cycle_amount"] is None
    sample = report["max_debt_limit_sample"]
    assert sample["tick"] == 25
    assert Decimal(sample["used"]) == Decimal("51.00000026")
    assert sample["trust_limit"] == "1000"


def test_unobserved_ratio_is_none_and_initial_zero_is_an_observation():
    s = scenario()
    r = recording(s, empty=True, intensity=0)
    assert measured(s, r)["max_debt_limit"] == "0"
    r["launches"][0]["initial_snapshot"]["links"] = []
    report = measured(s, r)
    assert report["max_debt_limit"] is report["max_debt_limit_sample"] is None


def test_get_tick_number_without_post_tick_sse_is_not_completion():
    s = scenario()
    r = recording(s)
    l = r["launches"][0]
    l["completed_ticks"][0]["event_id"] = l["events"][0]["id"]
    with pytest.raises(ValueError, match="post-tick run_status"):
        measured(s, r)


def test_progress_from_another_story_event_cannot_be_classified_as_normal():
    s = scenario(scripted=True)
    r = recording(s)
    r["launches"][0]["completed_ticks"][0]["status"]["episode_progress"][0][
        "index"
    ] = 99
    with pytest.raises(ValueError, match="bound scenario event"):
        measured(s, r)


def test_nominal_opening_denominator_excludes_future_scripted_payments():
    s = scenario(scripted=True)
    s["events"].append({**s["events"][0], "time": 500000, "amount": "1000000"})
    report = measured(s, recording(s, ticks=25))
    assert report["scripted"]["nominal_gross"] == "1.00000001"


def test_stall_get_and_sse_cannot_disagree():
    s = scenario()
    r = recording(s)
    r["launches"][0]["completed_ticks"][0]["status"]["consec_all_rejected_ticks"] = 3
    with pytest.raises(ValueError, match="stall evidence"):
        measured(s, r)


def test_cli_replays_real_json_from_clean_process_without_runtime_environment(tmp_path):
    s = scenario(scripted=True)
    source, capture, output = (
        tmp_path / name for name in ("scenario.json", "capture.json", "report.json")
    )
    source.write_text(json.dumps(s), encoding="utf-8")
    capture.write_text(json.dumps(recording(s)), encoding="utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ENV", "ENVIRONMENT", "DATABASE_URL", "SIMULATOR_STATE_DIR")
    }
    result = subprocess.run(
        [
            sys.executable,
            str(Path(economy.__file__)),
            str(source),
            "--replay",
            str(capture),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (
        json.loads(output.read_text(encoding="utf-8"))["launches"][0]["normal"][
            "attempts"
        ]
        == 1
    )


def test_replay_cli_uses_live_recording(tmp_path, monkeypatch):
    scenario = tmp_path / "scenario.json"
    capture = tmp_path / "capture.json"
    output = tmp_path / "report.json"
    scenario.write_text(json.dumps({"scenario_id": "bound"}), encoding="utf-8")
    capture.write_text(json.dumps({"launches": []}), encoding="utf-8")
    monkeypatch.setattr(
        economy, "measure_live", lambda s, r: {"live": r}, raising=False
    )
    assert (
        economy.main([str(scenario), "--replay", str(capture), "--output", str(output)])
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8")) == {"live": {"launches": []}}
