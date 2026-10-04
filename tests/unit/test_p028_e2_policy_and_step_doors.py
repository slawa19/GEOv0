"""028 E2 (`T2821`, `T2822`, `T2823`): the policy grammar and the accounting step, as functions.

F-028-2: `max_hop_usage` `"NaN"` raised `InvalidOperation` (HTTP 500 `E010`), `"Infinity"` was stored.
F-028-3: `pair_rules` read `bool(None)` as "forbids mediation" although the API stores `null` as "not set".
F-028-23: the step `10**-precision` is a rule on the VALUE (`"1.500"` passes at precision 2), never a rounding.
The HTTP and database halves are in `tests/integration/test_p028_e2_accounting_step_postgres.py`.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.payments.capacity import pair_rules, pending_pair_capacity
from app.utils.exceptions import BadRequestException
from app.utils import validation
from app.utils.validation import validate_trustline_policy

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "inf", float("nan"), float("inf")])
def test_a_non_finite_max_hop_usage_is_a_400_not_a_500_and_not_stored(value) -> None:
    with pytest.raises(BadRequestException):
        validate_trustline_policy({"max_hop_usage": value})


@pytest.mark.parametrize("value", ["0", "0.5", 1, None])
def test_finite_max_hop_usage_values_are_still_accepted(value) -> None:
    validate_trustline_policy({"max_hop_usage": value})


def test_null_can_be_intermediate_means_not_set_and_false_still_forbids() -> None:
    assert pair_rules([("o", {"can_be_intermediate": None})]) == (frozenset(), frozenset())
    assert pair_rules([("o", {})]) == (frozenset(), frozenset())
    assert pair_rules([("o", {"can_be_intermediate": False})])[0] == {"o"}
    assert pair_rules([("o", {"max_hop_usage": "0"})])[0] == {"o"}


def test_every_scenario_fixture_policy_passes_the_validator() -> None:
    """Anti-vacuum for the seeder's validator (F-028-3): the shipped scenarios are not refused by it."""

    checked = 0
    for path in sorted((_ROOT / "fixtures" / "simulator").rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for line in (data.get("trustlines") or []) if isinstance(data, dict) else []:
            if isinstance(line.get("policy"), dict):
                validate_trustline_policy(line["policy"])
                checked += 1
    assert checked >= 1900, f"only {checked} scenario policies found - the walk lost the fixtures"


@pytest.mark.parametrize("amount", ["1.50", "1.500", "1", "0.01"])
def test_a_multiple_of_the_step_passes_whatever_its_spelling(amount) -> None:
    assert validation.require_money_step(Decimal(amount), precision=2, equivalent="UAH") == Decimal(amount)


@pytest.mark.parametrize("amount,precision", [("1.505", 2), ("0.005", 2), ("0.1", 0), ("1.23", 1)])
def test_more_fraction_than_the_step_is_refused_not_rounded(amount, precision) -> None:
    with pytest.raises(BadRequestException) as caught:
        validation.require_money_step(Decimal(amount), precision=precision, equivalent="UAH", field="limit")
    assert caught.value.details == {"field": "limit", "reason": "amount_precision_exceeded",
                                    "equivalent": "UAH", "precision": precision}


def test_a_pending_pair_carries_a_multiple_of_the_step() -> None:
    """`2*owes - step`, floored: owes 1 at precision 2 gives 1.99, never 1.99999999."""

    assert pending_pair_capacity(Decimal("11"), payee_owes=Decimal("1"), step=Decimal("0.01")) == Decimal("1.99")
    assert pending_pair_capacity(Decimal("1.5"), payee_owes=Decimal("1"), step=Decimal("1")) == Decimal("1")
    assert pending_pair_capacity(Decimal("11"), payee_owes=Decimal("0"), step=Decimal("0.01")) == 0


@pytest.mark.parametrize("precision", [0, 1, 2, 8])
def test_every_planned_clearing_amount_is_a_multiple_of_the_step(precision) -> None:
    """F-028-27 - a MEASUREMENT, exempt from the red baseline (spec, Verification plan §5): with debts in the
    step, the planner's cycles and remainders are in the step too (it divides the capacities by their gcd,
    `flow_planner.py`). A counterexample here would be the red reproducer of a planner change."""

    import random
    import uuid

    from app.core.clearing.flow_planner import PlanEdge, atoms_of, plan_clearing

    rng = random.Random(28027 + precision)
    step_atoms = atoms_of(Decimal(1).scaleb(-precision))
    planned = 0
    for _ in range(60):
        nodes = [uuid.UUID(int=rng.getrandbits(128)) for _ in range(rng.randint(3, 9))]
        pairs = {(a, b) for a in nodes for b in nodes if a != b and rng.random() < 0.45}
        pairs = {(a, b) for a, b in pairs if (b, a) not in pairs or a.int < b.int}  # one direction per pair
        edges = [PlanEdge(uuid.UUID(int=rng.getrandbits(128)), a, b, step_atoms * rng.randint(1, 5000))
                 for a, b in sorted(pairs)]
        plan = plan_clearing(edges)
        assert all(c.atoms % step_atoms == 0 for c in plan.cycles), plan.cycles
        assert all(m % step_atoms == 0 for m in plan.remaining.values())
        planned += len(plan.cycles)
    assert planned > 0, "no cycle was planned - the property was never exercised"
