from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.measure_scenario_economy import ROOT, StaticPolicyGraph, main, measure


def scenario(edges, *, ids=("a", "b", "c"), transit=True):
    return {
        "scenario_id": "synthetic",
        "equivalents": ["UAH"],
        "participants": [
            {"id": pid, "type": "business" if transit else "person", "groupId": pid}
            for pid in ids
        ],
        # Input edges here are payments, inverted to creditor-owned lines.
        "trustlines": [
            {
                "from": b,
                "to": a,
                "equivalent": "UAH",
                "limit": "100",
                "policy": {"can_be_intermediate": transit},
            }
            for a, b in edges
        ],
    }


def test_policy_nontransit_creditor_business_and_endpoints():
    permitted = scenario([("a", "b"), ("b", "c")])
    forbidden = scenario([("a", "b"), ("b", "c")], transit=False)
    good, bad = StaticPolicyGraph(permitted), StaticPolicyGraph(forbidden)
    assert good.reachable("UAH", "a", "c")
    assert not bad.reachable("UAH", "a", "c")
    assert bad.reachable("UAH", "a", "b")  # endpoint is not an intermediate
    # 050 decision 5 filters forbidden routes before sampling plans. Keep the
    # policy counterexample on the independent ordered-pair domain, while a
    # nonempty sample prevents a planner returning no actions from passing.
    for raw, expected_coverage in ((forbidden, 2), (permitted, 3)):
        measured = measure(raw, ticks=50, intensity=100)
        sampled = measured["planned_static_unreachable"]
        assert sampled["denominator"] > 0
        assert sampled["numerator"] == 0
        coverage = measured["equivalents"]["UAH"]["eligible_ordered_pair_coverage"]
        assert (coverage["numerator"], coverage["denominator"]) == (expected_coverage, 6)


def test_reverse_line_policy_and_alternate_valid_path():
    s = scenario([("a", "b"), ("b", "c"), ("b", "a")])
    # b owns a->b; reverse active line a owns b->a. The latter forbids a,
    # which is an endpoint, so it does not prohibit b's transit.
    s["trustlines"][2]["policy"] = {"can_be_intermediate": False}
    assert StaticPolicyGraph(s).reachable("UAH", "a", "c")
    # On pair b,c reverse line b->c is owned by b and forbids b mediating.
    s["trustlines"].append(
        {
            "from": "b",
            "to": "c",
            "equivalent": "UAH",
            "limit": "100",
            "policy": {"max_hop_usage": "0.0"},
        }
    )
    assert not StaticPolicyGraph(s).reachable("UAH", "a", "c")
    s["participants"].append({"id": "d", "status": "active"})
    s["trustlines"] += scenario([("a", "d"), ("d", "c")])["trustlines"]
    assert StaticPolicyGraph(s).reachable("UAH", "a", "c")


def test_blocked_nodes_are_inner_only_and_path_dependent():
    s = scenario([("a", "b"), ("b", "c")])
    s["trustlines"][0]["policy"]["blocked_participants"] = ["b"]
    graph = StaticPolicyGraph(s)
    assert graph.reachable("UAH", "a", "b")
    assert not graph.reachable("UAH", "a", "c")
    s["trustlines"][0]["policy"]["blocked_participants"] = ["a", "c"]
    assert StaticPolicyGraph(s).reachable("UAH", "a", "c")


def test_active_population_and_line_status_orientation_and_hop_bound():
    s = scenario([("a", "b"), ("b", "c")], ids=("a", "b", "c", "isolated"))
    graph = StaticPolicyGraph(s, max_hops=1)
    assert graph.reachable("UAH", "a", "b")
    assert not graph.reachable("UAH", "b", "a")
    assert not graph.reachable("UAH", "a", "c")
    s["participants"][1]["status"] = "frozen"
    report = measure(s, ticks=20, intensity=100)
    assert report["population"]["excluded_nonactive"] == ["b"]
    assert (
        report["planned_nonactive_endpoint_actions"] > 0
    )  # actual planner output retained
    assert report["equivalents"]["UAH"]["eligible_ordered_pair_coverage"] == {
        "numerator": 0,
        "denominator": 6,
        "share": "0",
    }
    s["participants"][1]["status"] = "active"
    s["trustlines"][1]["status"] = "frozen"
    s["trustlines"] += [
        {"from": "a", "to": "a", "limit": "100", "equivalent": "UAH"},
        {"from": "unknown", "to": "a", "limit": "100", "equivalent": "UAH"},
    ]
    graph = StaticPolicyGraph(s)
    assert graph.reachable("UAH", "a", "b")
    assert not graph.reachable("UAH", "a", "c")
    assert graph.exclusions == {
        "nonactive_lines": 1,
        "self_lines": 1,
        "unknown_or_nonactive_endpoint_lines": 1,
    }


def test_flow_amount_balance_and_circulation_are_observed_not_thresholded():
    s = scenario([("a", "b")], ids=("a", "b"))
    result = measure(s, ticks=8, intensity=100, precision_by_eq={"UAH": 8})
    eq = result["equivalents"]["UAH"]
    paid = Decimal(eq["role_flow_amounts"]["a"]["b"])
    assert paid > 0
    assert eq["role_flow_counts"]["a"]["b"] == result["planned_actions"] == 160
    assert Decimal(eq["participant_balances"]["a"]["signed_in_minus_out"]) == -paid
    assert eq["participant_balances"]["a"]["relative_signed"] == "-1"
    assert eq["participant_balances"]["b"]["relative_signed"] == "1"
    assert eq["circulation_upper_bound"]["share"] == "0"
    assert result["direct_planned_payment_share"]["share"] == "1"
    assert eq["eligible_ordered_pair_coverage"]["denominator"] == 2


def test_directed_cycles_count_rotations_once_and_check_every_mediator():
    s = scenario(
        [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "a")],
        ids=("a", "b", "c", "d"),
    )
    graph = StaticPolicyGraph(s)
    assert graph.cycles("UAH", 3) == [["a", "b", "c"]]
    assert graph.cycles("UAH", 4) == [["a", "b", "c", "d"]]
    measured = measure(s, ticks=5)["equivalents"]["UAH"]
    assert all(v["share"] == "1" for v in measured["role_cycle_participation"].values())
    # Starting at a must not hide that a cannot mediate when closing the ring.
    s["trustlines"][2]["policy"]["can_be_intermediate"] = False
    assert StaticPolicyGraph(s).cycles("UAH", 3, transit_circuit=True) == []
    assert StaticPolicyGraph(s).cycles("UAH", 3) == [["a", "b", "c"]]


def test_missing_profiles_settings_graph_and_empty_denominators():
    empty = measure({"participants": [], "equivalents": ["UAH"]}, ticks=2)
    assert empty["planned_actions"] == 0
    assert empty["planned_static_unreachable"]["share"] is None
    assert (
        empty["equivalents"]["UAH"]["eligible_ordered_pair_coverage"]["share"] is None
    )
    assert empty["equivalents"]["UAH"]["circulation_upper_bound"]["share"] is None
    assert empty["model"]["profiles_present"] is False
    assert empty["model"]["settings_present"] is False
    assert measure({"participants": []}, ticks=1)["equivalents"] == {}
    s = scenario([("a", "b")], ids=("a", "b"))
    zero = measure(s, ticks=5, intensity=0)
    assert zero["planned_static_unreachable"]["share"] is None
    assert (
        zero["equivalents"]["UAH"]["eligible_ordered_pair_coverage"]["share"] == "0.5"
    )
    assert (
        zero["equivalents"]["UAH"]["participant_balances"]["a"]["relative_signed"]
        is None
    )


def test_household_direct_debt_ring_survives_without_transit_permission():
    s = scenario([("a", "b"), ("b", "c"), ("c", "a")], transit=False)
    for participant in s["participants"]:
        participant["groupId"] = "households"
    report = measure(s, ticks=5, intensity=0)["equivalents"]["UAH"]
    assert report["structural_cycles"]["3"] == {"count": 1, "paths": [["a", "b", "c"]]}
    assert report["transit_policy_circuits"]["3"]["count"] == 0
    assert report["role_cycle_participation"]["households"] == {
        "numerator": 3,
        "denominator": 3,
        "share": "1",
    }
    # Reverse orientation has different directed hops and is counted separately.
    s["trustlines"] += scenario([("b", "a"), ("c", "b"), ("a", "c")], transit=False)[
        "trustlines"
    ]
    assert len(StaticPolicyGraph(s).cycles("UAH", 3)) == 2


def test_equivalent_default_precision_cap_and_separate_amount_units():
    s = scenario([("a", "b")], ids=("a", "b"))
    s["baseEquivalent"] = "UAH"
    s["trustlines"][0].pop("equivalent")
    s["trustlines"].append({**s["trustlines"][0], "equivalent": "HOUR"})
    s["equivalents"].append("HOUR")
    result = measure(
        s,
        ticks=10,
        intensity=100,
        amount_cap="1.25",
        default_precision=0,
        precision_by_eq={"HOUR": 2},
    )
    assert result["parameters"]["precision_by_eq"] == {"HOUR": 2, "UAH": 0}
    for eq in result["equivalents"].values():
        assert (
            Decimal(eq["role_flow_amounts"]["a"]["b"])
            <= Decimal("1.25") * eq["planned_actions"]
        )
    assert result["parameters"]["amount_cap"] == "1.25"


def test_stress_windows_and_simulated_time_and_scripted_money_not_executed():
    s = scenario([("a", "b")], ids=("a", "b"))
    baseline = measure(s, ticks=2, intensity=100)
    s["events"] = [
        {
            "type": "stress",
            "time": 1000,
            "duration_ms": 1000,
            "effects": [{"op": "mult", "field": "tx_rate", "value": 0.000001}],
        }
    ]
    first = measure(s, ticks=1, intensity=100)
    both = measure(s, ticks=2, intensity=100)
    assert first["planned_actions"] == 0
    assert both["planned_actions"] == baseline["planned_actions"] // 2 == 20
    s["events"] = [
        {"type": "payment", "time": 1000, "from": "b", "to": "a", "amount": "999"},
        {"type": "inject", "time": 1000},
    ]
    actual = measure(s, ticks=2, intensity=100)
    assert actual["equivalents"] == baseline["equivalents"]


def test_demo_and_deterministic_output():
    s = json.loads(
        (ROOT / "fixtures/simulator/clearing-demo-10/scenario.json").read_text(
            encoding="utf-8"
        )
    )
    first = measure(s, ticks=40, intensity=15, seed=123)
    assert first == measure(deepcopy(s), ticks=40, intensity=15, seed=123)
    assert first["planned_actions"] > 0
    assert first["planned_static_unreachable"]["share"] == "0"
    assert (
        first["equivalents"]["UAH"]["eligible_ordered_pair_coverage"]["denominator"]
        == 90
    )


@pytest.mark.parametrize(
    "option,value",
    [
        ("--ticks", "0"),
        ("--intensity", "101"),
        ("--intensity", "-1"),
        ("--actions-per-tick", "0"),
        ("--precision", "9"),
        ("--eq-precision", "UAH=-1"),
        ("--eq-precision", "bad"),
        ("--max-hops", "0"),
        ("--amount-cap", "NaN"),
    ],
)
def test_cli_rejects_invalid_arguments(option, value, tmp_path, capsys):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario([("a", "b")])), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main([str(path), option, value])
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_cli_json_and_table_and_file(tmp_path, capsys):
    source = tmp_path / "scenario.json"
    source.write_text(
        json.dumps(scenario([("a", "b")], ids=("a", "b"))), encoding="utf-8"
    )
    assert main([str(source), "--ticks", "2", "--seed", "17"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["parameters"]["seed"] == 17
    assert "unreachable/plans" in captured.err
    destination = tmp_path / "result.json"
    assert (
        main(
            [str(source), "--ticks", "2", "--seed", "17", "--output", str(destination)]
        )
        == 0
    )
    assert json.loads(destination.read_text(encoding="utf-8")) == result
    assert capsys.readouterr().out == ""


def test_cli_import_preflight_does_not_use_developer_runtime(tmp_path):
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(scenario([])), encoding="utf-8")
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("SIMULATOR_STATE_DIR", None)
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/measure_scenario_economy.py"),
            str(source),
            "--ticks",
            "1",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 2
    assert "Set DATABASE_URL explicitly" in process.stderr
    assert process.stdout == ""
    env["DATABASE_URL"] = os.environ["DATABASE_URL"]
    env["SIMULATOR_STATE_DIR"] = str(ROOT / ".local-run/simulator")
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/measure_scenario_economy.py"),
            str(source),
            "--ticks",
            "1",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 2
    assert "default runtime state is unsafe" in process.stderr


def test_profiles_group_stress_and_balanced_cycle_positive_circulation():
    s = scenario([("a", "b"), ("b", "c"), ("c", "a")])
    for participant in s["participants"]:
        participant["behaviorProfileId"] = participant["id"]
    s["behaviorProfiles"] = [
        {"id": pid, "props": {"tx_rate": 1, "recipient_group_weights": {target: 1}}}
        for pid, target in (("a", "b"), ("b", "c"), ("c", "a"))
    ]
    s["settings"] = {"warmup": {"ticks": 4, "floor": 0.2}}
    baseline = measure(s, ticks=10, intensity=100)
    assert baseline["model"]["profiles_present"] is True
    assert baseline["model"]["settings_present"] is True
    assert (
        Decimal(baseline["equivalents"]["UAH"]["circulation_upper_bound"]["share"]) > 0
    )
    s["events"] = [
        {
            "type": "stress",
            "time": 1000,
            "duration_ms": 10000,
            "effects": [
                {
                    "op": "mult",
                    "field": "tx_rate",
                    "scope": "profile:a",
                    "value": 0.000001,
                }
            ],
        }
    ]
    stressed = measure(s, ticks=10, intensity=100)
    flows = stressed["equivalents"]["UAH"]["role_flow_counts"]
    assert sum(flows["a"].values()) == 0
    assert sum(flows["b"].values()) > 0
    assert sum(flows["c"].values()) > 0


def test_cli_subprocess_emits_json_with_no_database_connection(tmp_path):
    source = tmp_path / "scenario.json"
    source.write_text(
        json.dumps(scenario([("a", "b")], ids=("a", "b"))), encoding="utf-8"
    )
    env = dict(os.environ)
    # Unlistened port: successful output cannot depend on a database connection.
    env["DATABASE_URL"] = (
        "postgresql+asyncpg://geo:geo@127.0.0.1:1/geov0_test_p050_t5001_offline"
    )
    env["ENV"] = env["ENVIRONMENT"] = "test"
    env["DEBUG"] = "false"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/measure_scenario_economy.py"),
            str(source),
            "--ticks",
            "2",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["planned_actions"] == 2
    assert "unreachable/plans" in process.stderr


def test_cli_wrong_driver_is_refused_by_app_config_before_runtime_state(tmp_path):
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(scenario([])), encoding="utf-8")
    default_state = ROOT / ".local-run/simulator"

    def state_snapshot():
        return (
            [
                (
                    str(path.relative_to(default_state)),
                    path.stat().st_mtime_ns,
                    path.stat().st_size,
                )
                for path in sorted(default_state.rglob("*"))
            ]
            if default_state.exists()
            else None
        )

    before = state_snapshot()
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'refused-offline-driver.db'}"
    task_state = Path(env["SIMULATOR_STATE_DIR"]) / "wrong-driver-refusal"
    assert not task_state.exists()
    env["SIMULATOR_STATE_DIR"] = str(task_state)
    env["ENV"] = env["ENVIRONMENT"] = "test"
    env["DEBUG"] = "false"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/measure_scenario_economy.py"),
            str(source),
            "--ticks",
            "1",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 1
    assert "app" in process.stderr and "config.py" in process.stderr
    assert (
        "DATABASE_URL uses 'sqlite'; the application runs only on PostgreSQL"
        in process.stderr
    )
    assert process.stdout == ""
    assert not task_state.exists()
    assert not (tmp_path / "refused-offline-driver.db").exists()
    assert state_snapshot() == before
