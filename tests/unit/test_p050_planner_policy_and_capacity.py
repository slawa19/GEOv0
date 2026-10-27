"""050 decision 5: observable plans, router policy and direct-pair headroom."""

import logging
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.simulator.real_payment_planner import (
    RealPaymentPlanner,
    _build_plan_context,
    _choose_receiver,
    _reachable_nodes,
)


def _planner():
    return RealPaymentPlanner(
        actions_per_tick_max=20,
        amount_cap_limit=None,
        logger=logging.getLogger(__name__),
        action_factory=lambda seq, eq, sender, receiver, amount: (sender, receiver, amount),
    )


def _line(payer, payee, *, policy=None, limit="100", status="active"):
    return {"from": payee, "to": payer, "equivalent": "UAH", "limit": limit,
            "status": status, "policy": policy}


def _scenario(lines, *, groups=None, props=None, flow=False):
    pids = sorted({p for line in lines for p in (line["from"], line["to"])})
    return {
        "equivalents": ["UAH"], "trustlines": lines,
        "participants": [{"id": p, "type": "business", "groupId": (groups or {}).get(p, ""),
                          "behaviorProfileId": "sender" if p == "S" else "idle"} for p in pids],
        "behaviorProfiles": [{"id": "sender", "props": {"tx_rate": 1, **(props or {})}},
                             {"id": "idle", "props": {"tx_rate": 0}}],
        "settings": {"flow": {"enabled": flow, "default_affinity": 1}},
    }


def _context(scenario, debt=None):
    return _build_plan_context(
        scenario=scenario, candidates=_planner().candidates_from_scenario(scenario),
        tick_seed=42, stress=(1, {}, {}), debt_snapshot=debt, precision_by_eq=None,
    )


def _plan(scenario, debt=None, precision=2):
    return _planner().plan_payments(
        SimpleNamespace(seed=42, tick_index=100, intensity_percent=100, sim_time_ms=100000),
        scenario, debt_snapshot=debt, precision_by_eq={"UAH": precision},
    )


@pytest.mark.parametrize("used,expected", [("50", 20), ("100", 0)])
def test_three_credit_lines_compare_debt_with_sum_not_single_max(used, expected):
    lines = [_line(p, "R") for p in ("S", "D2", "D3")]
    scenario = _scenario(lines)
    debt = {(p, "R", "UAH"): Decimal(used) for p in ("S", "D2", "D3")}
    planned = _plan(scenario, debt)
    assert len(planned) == expected
    assert all(receiver == "R" and Decimal(amount) <= 100 - Decimal(used)
               for _, receiver, amount in planned)


def test_sender_aggregate_also_compares_with_three_outgoing_limits():
    scenario = _scenario([_line("S", p) for p in ("R1", "R2", "R3")])
    planned = _plan(scenario, {("S", p, "UAH"): Decimal(50) for p in ("R1", "R2", "R3")})
    assert len(planned) == 20
    assert all(Decimal(amount) <= 50 for _, _, amount in planned)


@pytest.mark.parametrize("precision", [0, 2, 8])
def test_reverse_debt_restores_direct_capacity_without_overrunning_pair(precision):
    # A netted ledger holds one direction only. Headroom: 100 - 0 + 50 = 150.
    scenario = _scenario([_line("S", "R"), _line("R", "S")])
    scenario["settings"]["flow"]["reciprocity_bonus"] = 1
    planned = _plan(scenario, {("R", "S", "UAH"): Decimal(50)}, precision)
    assert len(planned) == 20
    amounts = [Decimal(a[2]) for a in planned]
    assert max(amounts) <= 150
    assert max(amounts) > 100  # Repayment extends the credit limit, rather than merely unblocking it.


def test_reverse_debt_adds_to_unused_limit_not_only_repays_full_line():
    scenario = _scenario([_line("S", "R"), _line("R", "S")], props={
        "amount_model": {"UAH": {"min": "140", "max": "140", "p50": "140"}},
    })
    planned = _plan(scenario, {("R", "S", "UAH"): Decimal(50)})
    assert len(planned) == 20
    assert {Decimal(a[2]) for a in planned} == {Decimal(140)}


@pytest.mark.parametrize("policy,reachable", [
    ({"can_be_intermediate": False}, False),
    ({"max_hop_usage": "0.0"}, False),
    ({"can_be_intermediate": True, "max_hop_usage": "0.5"}, True),
    (None, True),
])
def test_nontransit_owner_can_receive_but_cannot_mediate(policy, reachable):
    scenario = _scenario([_line("S", "M", policy=policy), _line("M", "R")],
                         groups={"R": "target"}, props={"recipient_group_weights": {"target": 1}})
    if not reachable:
        next(p for p in scenario["participants"] if p["id"] == "M")["type"] = "person"
    nodes = _reachable_nodes(_context(scenario), "UAH", "S")
    assert "M" in nodes  # Same owner is a permitted endpoint.
    assert ("R" in nodes) is reachable
    planned = _plan(scenario)
    assert len(planned) == 20
    assert any(receiver == "R" for _, receiver, _ in planned) is reachable


@pytest.mark.parametrize("status,reachable", [("active", False), ("closed", True), ("frozen", True)])
@pytest.mark.parametrize("reverse_limit", ["0", "100"])
def test_reverse_active_line_policy_also_forbids_owner_mediation(status, reachable, reverse_limit):
    scenario = _scenario([
        _line("S", "M"), _line("M", "R"),
        _line("R", "M", policy={"can_be_intermediate": False}, status=status, limit=reverse_limit),
    ])
    assert ("R" in _reachable_nodes(_context(scenario), "UAH", "S")) is reachable


@pytest.mark.parametrize("blocked,reachable", [("M", False), ("S", True), ("R", True)])
def test_blocked_participants_apply_only_to_route_interior(blocked, reachable):
    scenario = _scenario([_line("S", "M"),
                          _line("M", "R", policy={"blocked_participants": [blocked]})])
    assert ("R" in _reachable_nodes(_context(scenario), "UAH", "S")) is reachable


def test_alternate_path_survives_first_path_with_later_blocked_interior():
    # S-A-X-R fails only on X-R; S-B-X-R must still be explored after X was seen.
    scenario = _scenario([_line("S", "A"), _line("S", "B"), _line("A", "X"),
                          _line("B", "X"), _line("X", "R", policy={"blocked_participants": ["A"]})])
    assert "R" in _reachable_nodes(_context(scenario), "UAH", "S")
    scenario["trustlines"] = [line for line in scenario["trustlines"] if line["from"] != "B"]
    assert "R" not in _reachable_nodes(_context(scenario), "UAH", "S")


def test_alternate_path_survives_early_policy_becoming_binding_only_after_endpoint():
    scenario = _scenario([_line("S", "A", policy={"blocked_participants": ["M"]}),
                          _line("S", "B"), _line("A", "M"), _line("B", "M"), _line("M", "T")])
    assert "T" in _reachable_nodes(_context(scenario), "UAH", "S")
    scenario["trustlines"] = [line for line in scenario["trustlines"] if line["from"] != "B"]
    nodes = _reachable_nodes(_context(scenario), "UAH", "S")
    assert "M" in nodes and "T" not in nodes


def test_sender_endpoint_can_own_line_forbidding_mediation():
    scenario = _scenario([_line("S", "M"), _line("M", "R"),
                          _line("M", "S", policy={"can_be_intermediate": False})])
    assert "R" in _reachable_nodes(_context(scenario), "UAH", "S")


@pytest.mark.parametrize("branch", ["flow", "weights", "flow-missing", "weights-missing", "default", "any"])
@pytest.mark.parametrize("used,direct", [("99.99", True), ("100", False)])
def test_direct_target_group_neighbor_precedes_ten_deeper_members(branch, used, direct):
    lines = [_line("S", "D"), _line("S", "M")]
    lines += [_line("M", f"R{i}") for i in range(10)]
    groups = {p: "target" for p in ["D", *[f"R{i}" for i in range(10)]]}
    props = {}
    if branch.startswith("flow"):
        groups["S"] = "origin"
        props = {"flow_chains": [["origin", "missing" if branch == "flow-missing" else "target"]], "flow_affinity": 1}
    elif branch.startswith("weights"):
        props = {"recipient_group_weights": {"missing" if branch == "weights-missing" else "target": 1}}
    elif branch == "any":
        groups = {}  # any-reachable includes the other direct neighbor M as well.
    ctx = _context(_scenario(lines, groups=groups, props=props, flow=branch.startswith("flow")),
                   {("S", "D", "UAH"): Decimal(used)})
    chosen = {_choose_receiver(ctx, rng=random.Random(seed), eq="UAH", sender="S", sender_props=props)
              for seed in range(40)}
    if branch == "any":
        assert chosen <= ({"D", "M"} if direct else {"M"})
    elif direct:
        assert chosen == {"D"}
    else:
        assert chosen and chosen <= {f"R{i}" for i in range(10)}


def test_missing_profiles_and_settings_keep_direct_plans_and_empty_snapshot_equivalence():
    scenario = _scenario([_line("S", "R")])
    scenario.pop("behaviorProfiles")
    scenario.pop("settings")
    assert _plan(scenario, None) == _plan(scenario, {})
    assert len(_plan(scenario)) == 20


def test_reachability_keeps_depth_and_unique_node_bounds():
    scenario = _scenario([_line("S", "A"), _line("A", "B"), _line("B", "C"), _line("C", "D")])
    ctx = _context(scenario)
    assert _reachable_nodes(ctx, "UAH", "S") == ["A", "B", "C"]
    assert _reachable_nodes(ctx, "UAH", "S", max_nodes=3) == ["A", "B"]
    assert _reachable_nodes(ctx, "UAH", "S", max_depth=0) == []


def test_indirect_per_action_hint_is_not_sum_of_participant_limits():
    scenario = _scenario([_line("S", "A"), _line("S", "B"),
                          _line("A", "R"), _line("B", "R")])
    # Aggregate room at each end is 200; static single-line action hint stays 100.
    assert _planner()._capacity_limit(_context(scenario), eq="UAH", sender_pid="S", receiver_pid="R") == 100


def test_bonus_small_reverse_debt_does_not_create_extra_pair_capacity():
    scenario = _scenario([_line("S", "R"), _line("R", "S")])
    scenario["settings"]["flow"]["reciprocity_bonus"] = 0.3
    planned = _plan(scenario, {("R", "S", "UAH"): Decimal(1)})
    assert len(planned) == 20
    assert max(Decimal(amount) for _, _, amount in planned) <= 101


def test_debt_inputs_are_rebuilt_for_each_plan_even_for_same_seed_and_tick():
    scenario = _scenario([_line("S", "R")])
    planner = _planner()
    run = SimpleNamespace(seed=42, tick_index=100, intensity_percent=100, sim_time_ms=100000)
    empty = planner.plan_payments(run, scenario, debt_snapshot={})
    assert len(empty) == 20
    assert planner.plan_payments(run, scenario, debt_snapshot={("S", "R", "UAH"): Decimal(100)}) == []
    assert planner.plan_payments(run, scenario, debt_snapshot={}) == empty
