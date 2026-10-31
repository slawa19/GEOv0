"""MM2-SOL-1 (BACKLOG, MiniMax pilot M2, 2026-10-31): the planner plans no payment for a participant the scenario has
taken out of circulation.

`RealPaymentPlanner.candidates_from_scenario` judged only the LINE's status, so a participant whose scenario status is
not active - `frozen` in the scenario file, or `suspended` as the freeze inject leaves it in memory
(`cache_invalidator.py`, step 7) - kept its lines in the plan: witness W1 (on 4197b3cd) planned 1000 of 1000 payments
from the frozen sender, every one bound to be refused (by the router as `ROUTING_NO_ROUTE` on the tick path, and at the
money boundary as 409 `participant_suspended`).

The scenario's word for a status is read by the seeder's one rule (`scenario_participant_status`: `frozen` ->
`suspended`, `banned` -> `deleted`, anything unknown -> `active`). A participant the scenario does not declare is not
judged here (MM2-MM-A, the load path). Pure planner, no database: the scenario dict is what the run holds.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.core.simulator.real_payment_planner import RealPaymentPlanner

EQ = "UAH"


def _planner() -> RealPaymentPlanner:
    return RealPaymentPlanner(
        actions_per_tick_max=20,
        amount_cap_limit=None,
        logger=logging.getLogger(__name__),
        action_factory=lambda seq, eq, sender, receiver, amount: (eq, sender, receiver, amount),
    )


def _scenario(status_of_a: str | None) -> dict:
    a = {"id": "A"} if status_of_a is None else {"id": "A", "status": status_of_a}
    return {
        "equivalents": [EQ],
        "participants": [a, {"id": "B"}, {"id": "C"}],
        "trustlines": [  # creditor -> debtor; the payment runs debtor -> creditor
            {"from": "B", "to": "A", "equivalent": EQ, "limit": "100"},  # A pays B
            {"from": "A", "to": "B", "equivalent": EQ, "limit": "100"},  # B pays A (the same pair, other direction)
            {"from": "A", "to": "C", "equivalent": EQ, "limit": "100"},  # C pays A
            {"from": "C", "to": "B", "equivalent": EQ, "limit": "100"},  # B pays C - the control: A is not on it
        ],
    }


def _planned(scenario: dict, ticks: int = 50) -> list[tuple]:
    planner, out = _planner(), []
    for tick in range(1, ticks + 1):
        run = SimpleNamespace(seed=42, tick_index=tick, sim_time_ms=1000 * tick, intensity_percent=100)
        out += planner.plan_payments(run, scenario, debt_snapshot=None, precision_by_eq={EQ: 2})
    return out


@pytest.mark.parametrize("status", ["frozen", "suspended", " Suspended ", "left", "deleted", "banned"])
def test_a_participant_out_of_circulation_gets_no_line_and_no_payment(status) -> None:
    scenario = _scenario(status)
    candidates = _planner().candidates_from_scenario(scenario)
    touching_a = [c for c in candidates if "A" in (c["sender_pid"], c["receiver_pid"])]
    planned = _planned(scenario)
    with_a = [p for p in planned if "A" in (p[1], p[2])]
    assert not touching_a and not with_a, (
        f"actual: status {status!r} - {len(touching_a)} candidate lines touch A, {len(with_a)} of {len(planned)} "
        f"planned payments over 50 ticks have A as sender or receiver; each is bound to be refused"
    )
    # anti-vacuum: the line between the two active participants is still planned
    assert [(c["sender_pid"], c["receiver_pid"]) for c in candidates] == [("B", "C")], candidates
    assert planned and all((p[1], p[2]) == ("B", "C") for p in planned), planned[:3]


@pytest.mark.parametrize("status", [None, "active", "ACTIVE", "something-unknown"])
def test_an_active_participant_is_planned_as_before(status) -> None:
    """Control: no status, `active` in any case, and a word the scenario rule reads as `active` keep every line."""
    scenario = _scenario(status)
    pairs = {(c["sender_pid"], c["receiver_pid"]) for c in _planner().candidates_from_scenario(scenario)}
    assert pairs == {("A", "B"), ("B", "A"), ("C", "A"), ("B", "C")}, pairs
    senders = {p[1] for p in _planned(scenario)}
    assert "A" in senders and len(senders) > 1, senders


def test_the_freeze_inject_in_memory_status_takes_effect_on_the_next_plan() -> None:
    """The run's scenario dict is planned anew every tick (no cache of the candidates): the in-place `suspended` the
    inject writes (`cache_invalidator.py`, step 7) removes A from the very next plan."""
    scenario = _scenario(None)
    before = [p for p in _planned(scenario, ticks=10) if p[1] == "A"]
    scenario["participants"][0]["status"] = "suspended"
    after = [p for p in _planned(scenario, ticks=50) if p[1] == "A"]
    assert before, "control: A pays while active"
    assert not after, f"actual: {len(after)} payments from A planned after the freeze; first {after[:1]}"


def test_an_undeclared_participant_is_not_judged_here() -> None:
    """A line to a pid the scenario does not declare is not this rule's business (MM2-MM-A): it stays a candidate."""
    scenario = _scenario(None)
    scenario["trustlines"].append({"from": "GHOST", "to": "B", "equivalent": EQ, "limit": "100"})
    pairs = {(c["sender_pid"], c["receiver_pid"]) for c in _planner().candidates_from_scenario(scenario)}
    assert ("B", "GHOST") in pairs, pairs
