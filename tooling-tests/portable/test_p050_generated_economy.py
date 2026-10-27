"""Generated model contracts; outputs stay temporary, never regenerate fixtures."""

import copy
import importlib.util
import json
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def generator():
    spec = importlib.util.spec_from_file_location(
        "_p050_generator", ROOT / "scripts/generate_simulator_seed_scenarios.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["riverside-town-50", "greenfield-village-100"])
def modeled(request):
    module = generator()
    community = module.load_community(request.param, root=ROOT / "seeds/communities")
    scenario = module._scenario_from_community(
        scenario_id="model-test", community=community, equivalents=["UAH"]
    )
    return module, community, scenario


def test_generated_lines_are_exactly_the_inverse_of_assigned_pairs(modeled):
    module, _, scenario = modeled
    model = module.economy_model(scenario["participants"])
    pairs = set(model["assigned_pairs"])
    lines = {(t["to"], t["from"]) for t in scenario["trustlines"]}
    assert pairs == lines
    assert len(lines) == len(scenario["trustlines"])
    assert pairs and not any((b, a) in pairs for a, b in pairs)
    active = {p["id"] for p in scenario["participants"] if p["status"] == "active"}
    assert {pid for pair in pairs for pid in pair} == active
    described = {p["pid"]: p.get("role") for p in modeled[1]["participants"]}
    assert all(
        p["metadata"].get("role") == described[p["id"].split(":", 1)[1]]
        for p in scenario["participants"]
    )


def test_opening_is_complete_rings_with_bounded_nominal_debt(modeled):
    module, _, scenario = modeled
    payments = [e for e in scenario["events"] if e["type"] == "payment"]
    assert 0 < len(payments) <= 100
    limits = {(t["to"], t["from"]): Decimal(t["limit"]) for t in scenario["trustlines"]}
    per_tick, pairs_per_tick, debt = Counter(), Counter(), Counter()
    for e in payments:
        assert isinstance(e["amount"], str)
        assert e["time"] % 1000 == 0 and 1000 <= e["time"] <= 20000
        pair = e["from"], e["to"]
        assert pair in limits
        per_tick[e["time"]] += 1
        pairs_per_tick[e["time"], pair] += 1
        debt[pair] += Decimal(e["amount"])
    assert max(per_tick.values()) <= 5
    assert max(pairs_per_tick.values()) == 1
    assert all(amount <= limits[pair] / 2 for pair, amount in debt.items())
    assert all(e["time"] > 100000 for e in scenario["events"] if e["type"] == "stress")
    # Every opening contribution circulates, rather than leaving a graph prefix.
    balances = Counter()
    for (a, b), amount in debt.items():
        balances[a] -= amount
        balances[b] += amount
    assert all(amount == 0 for amount in balances.values())


def test_profiles_are_shared_role_degree_and_model_is_deterministic(modeled, tmp_path):
    module, community, scenario = modeled
    repeated = module._scenario_from_community(
        scenario_id="model-test", community=community, equivalents=["UAH"]
    )
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    module._write_json(first, scenario)
    module._write_json(second, repeated)
    assert first.read_bytes() == second.read_bytes()
    degree = Counter(t["to"] for t in scenario["trustlines"])
    profiles = {p["id"]: p["props"] for p in scenario["behaviorProfiles"]}
    cohorts = {}
    rates = []
    for p in scenario["participants"]:
        if p["status"] != "active":
            continue
        key = p["groupId"], degree[p["id"]]
        assert cohorts.setdefault(key, p["behaviorProfileId"]) == p["behaviorProfileId"]
        rates.append(profiles[p["behaviorProfileId"]]["tx_rate"])
    assert sum(rates) / len(rates) >= 0.05
    module._validate_scenario_shape(first)
    broken = copy.deepcopy(scenario)
    broken["events"][0]["amount"] = 42
    module._write_json(first, broken)
    with pytest.raises(RuntimeError, match="Invalid scenario.*amount"):
        module._validate_scenario_shape(first)


def test_missing_employers_is_a_refusal_instead_of_a_dead_model(modeled):
    module, community, _ = modeled
    broken = copy.deepcopy(community)
    broken["participants"] = [
        p for p in broken["participants"] if p["group"] != "producers"
    ]
    with pytest.raises(RuntimeError, match="producers"):
        module._scenario_from_community(
            scenario_id="broken", community=broken, equivalents=["UAH"]
        )


def test_balance_gate_rejects_an_omitted_salary_even_with_other_green_roles():
    module = generator()
    scenario = {
        "participants": [
            {"id": "salary-worker", "status": "active", "groupId": "households"}
        ]
    }
    good = {
        "equivalents": {
            "UAH": {
                "participant_balances": {"salary-worker": {"in": "100", "out": "100"}}
            }
        }
    }
    module.validate_economy_reports(scenario, [good])
    broken = copy.deepcopy(good)
    broken["equivalents"]["UAH"]["participant_balances"]["salary-worker"]["in"] = "0"
    with pytest.raises(RuntimeError, match="households.*salary-worker"):
        module.validate_economy_reports(scenario, [broken])


@pytest.mark.parametrize(
    "counts",
    [
        {
            "households": 24,
            "producers": 16,
            "anchors": 8,
            "retail": 8,
            "services": 12,
            "agents": 4,
        },
        {
            "households": 40,
            "producers": 28,
            "anchors": 12,
            "retail": 12,
            "services": 16,
            "agents": 6,
        },
    ],
)
def test_assignment_is_a_role_rule_not_a_community_id_or_roster_fixture(counts):
    module = generator()
    participants = [
        {"id": f"{role}-{i:03}", "groupId": role, "status": "active"}
        for role, count in counts.items()
        for i in range(count)
    ]
    model = module.economy_model(participants)
    pairs = set(model["assigned_pairs"])
    assert all((b, a) not in pairs for a, b in pairs)
    assert {pid for pair in pairs for pid in pair} == {p["id"] for p in participants}
    for h in model["by_role"]["households"]:
        employers = [
            a
            for a, b in pairs
            if b == h and model["role_by_pid"][a] in {"producers", "anchors"}
        ]
        assert len(employers) == 1
        if model["role_by_pid"][employers[0]] == "producers":
            assert any(
                (r, employers[0]) in pairs for r in model["outgoing"][h]["retail"]
            )


def test_pooled_gate_does_not_average_ratios_and_role_threshold_is_independent():
    module = generator()
    scenario = {"participants": [{"id": "worker", "groupId": "households"}]}

    def report(incoming, outgoing):
        return {
            "equivalents": {
                "UAH": {
                    "participant_balances": {
                        "worker": {"in": str(incoming), "out": str(outgoing)}
                    }
                }
            }
        }

    # Two individually one-sided flows cancel exactly when amounts are pooled.
    module.validate_economy_reports(scenario, [report(100, 0), report(0, 100)])
    with pytest.raises(RuntimeError, match="Unbalanced role households.*threshold=.10"):
        module.validate_economy_reports(scenario, [report(80, 100)])
    with pytest.raises(RuntimeError, match="Unrepresented flow households worker"):
        module.validate_economy_reports(scenario, [report(0, 0)])


def test_real_planner_refuses_missing_payroll_without_overwriting_previous_output(
    tmp_path, monkeypatch
):
    module = generator()
    community = module.load_community(
        "riverside-town-50", root=ROOT / "seeds/communities"
    )
    raw = module._scenario_from_community(
        scenario_id="missing-salary", community=community, equivalents=["UAH"]
    )
    roles = {p["id"]: p["groupId"] for p in raw["participants"]}
    raw["trustlines"] = [
        t
        for t in raw["trustlines"]
        if not (roles[t["to"]] == "producers" and roles[t["from"]] == "households")
    ]
    path = tmp_path / "missing-salary.json"
    module._write_json(path, raw)
    module._validate_scenario_shape(path)
    spec = {
        "community_id": "riverside-town-50",
        "scenario_id": "missing-salary",
        "equivalents": ["UAH"],
    }
    previous = tmp_path / "output" / "missing-salary" / "scenario.json"
    previous.parent.mkdir(parents=True)
    previous.write_text("previous accepted output", encoding="utf-8")
    monkeypatch.setattr(module, "_scenario_from_community", lambda **kwargs: raw)
    with pytest.raises(
        RuntimeError,
        match=r"Unbalanced (households|producers|services).*missing-salary:",
    ):
        module.generate(spec, out_root=tmp_path / "output")
    assert previous.read_text(encoding="utf-8") == "previous accepted output"


def test_public_generation_validates_without_parent_runtime_or_environment_mutation(
    tmp_path, monkeypatch
):
    module = generator()
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "GEO_TEST_ALLOW_DB_RESET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.setenv("SIMULATOR_STATE_DIR", str(tmp_path / "parent-state"))
    before = dict(os.environ)
    path = module.generate(module.SCENARIOS[1], out_root=tmp_path / "generated")
    assert (
        json.loads(path.read_text(encoding="utf-8"))["scenario_id"]
        == module.SCENARIOS[1]["scenario_id"]
    )
    assert dict(os.environ) == before
    assert not (tmp_path / "parent-state").exists()
