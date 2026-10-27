"""The shipped files must carry the accepted model, with independent counterexamples."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _generator():
    spec = importlib.util.spec_from_file_location("_p050_fixture_generator", ROOT / "scripts/generate_simulator_seed_scenarios.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("community", ["greenfield-village-100", "riverside-town-50"])
def test_tracked_fixture_is_the_validated_economy_model_with_no_second_assignments(community, tmp_path):
    module = _generator()
    scenario_id = f"{community}-realistic-v2"
    actual = json.loads((ROOT / "fixtures/simulator" / scenario_id / "scenario.json").read_text(encoding="utf-8"))
    description = module.load_community(community, root=ROOT / "seeds/communities")
    expected = module._scenario_from_community(scenario_id=scenario_id, community=description, equivalents=["UAH"])
    assert actual == expected, "tracked fixture differs from the canonical accepted economy"
    model = module.economy_model(actual["participants"])
    intended = set(model["assigned_pairs"])
    inverse_lines = {(line["to"], line["from"]) for line in actual["trustlines"]}
    assert intended == inverse_lines and len(intended) == len(actual["trustlines"])
    assert len([e for e in actual["events"] if e["type"] == "payment"]) > 0
    output = tmp_path / "measured.json"
    env = dict(os.environ)
    env.update(ENV="test", DEBUG="false",
               DATABASE_URL="postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_p050_fixture_measure",
               SIMULATOR_STATE_DIR=str(ROOT / ".local-run/test-runs" / f"p050_fixture_{tmp_path.name}" / "state"))
    env.pop("ENVIRONMENT", None)
    process = subprocess.run([sys.executable, str(ROOT / "scripts/measure_scenario_economy.py"),
                              str(ROOT / "fixtures/simulator" / scenario_id / "scenario.json"),
                              "--ticks", "2000", "--intensity", "15", "--seed", "42", "--output", str(output)],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["planned_actions"] > 0
    assert report["planned_static_unreachable"]["numerator"] == 0
    _minimum(report["direct_planned_payment_share"], Decimal(".75"))
    equivalent = report["equivalents"]["UAH"]
    circulation = equivalent["circulation_upper_bound"]
    assert Decimal(circulation["share"]) >= Decimal("0.04430853798956837468"), circulation
    assert set(equivalent["role_cycle_participation"]) == {"anchors", "producers", "retail", "services", "households", "agents"}
    for participation in equivalent["role_cycle_participation"].values():
        _minimum(participation, Decimal(1))


def _minimum(stat, threshold):
    assert stat["denominator"] > 0 and stat["share"] is not None, stat
    assert Decimal(stat["share"]) >= threshold, f"actual={stat} threshold={threshold}"


def test_missing_salary_breaks_balance_and_missing_ring_is_not_an_accepted_fixture():
    module = _generator()
    description = module.load_community("greenfield-village-100", root=ROOT / "seeds/communities")
    original = module._scenario_from_community(scenario_id="counterproof", community=description, equivalents=["UAH"])
    broken = copy.deepcopy(original)
    model = module.economy_model(original["participants"])
    # Remove actual generated payroll: this must change the model and opening ring evidence.
    roles = model["role_by_pid"]
    broken["trustlines"] = [line for line in broken["trustlines"]
                            if not (roles[line["to"]] == "producers" and roles[line["from"]] == "households")]
    assert {(t["to"], t["from"]) for t in broken["trustlines"]} != set(model["assigned_pairs"])
    removed = set(model["assigned_pairs"]) - {(t["to"], t["from"]) for t in broken["trustlines"]}
    assert removed and all(roles[a] == "producers" and roles[b] == "households" for a, b in removed)
    broken_report = {"equivalents": {"UAH": {"participant_balances": {
        p["id"]: {"in": "0" if roles[p["id"]] == "households" else "100", "out": "100"}
        for p in original["participants"] if p["status"] == "active"}}}}
    with pytest.raises(RuntimeError, match="households"):
        module.validate_economy_reports(original, [broken_report])


def test_role_prices_keep_a_common_even_basket_and_actual_clipped_mean():
    module = _generator()
    expected = {"households": 100, "retail": 200, "producers": 150,
                "anchors": 200, "services": 120, "agents": 150}
    assert module.ROLE_P50 == expected
    scales = []
    for role, price in expected.items():
        assert price % 2 == 0
        assert module.amount_model(role) == {"p50": price, "p90": 2 * price, "min": price // 2, "max": 3 * price}
        mean = module.approximate_amount_mean(role)
        assert Decimal(price) < mean < Decimal(2 * price)
        # The same clipped shape scales linearly before the shared half-cent
        # quantization midpoint, rather than introducing role-specific tails.
        scales.append((mean + Decimal(".005")) / price)
    assert max(scales) - min(scales) < Decimal("1e-15")
