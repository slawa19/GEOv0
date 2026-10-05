"""030 S2 (`F-030-1`, owner В2): every NAMED input of an initial state refuses an amount finer than the step.

The spec names the inputs (030, "S2 - допуск равен запросу"), because a ban on "all inputs" would be vacuous. Two of
them are checked here, without a database; the rest are checked where they live:

* the recipe (`scripts/seed_recipe.py`): its description and recipe are refused at load by their schemas - a
  trust-line limit, a payment amount and a clearing amount of `0.015` at precision 2 - and, behind the schemas, the
  services it calls refuse the same value (`tests/integration/test_p028_e2_accounting_step_postgres.py`: trust line
  create/update, the payment door, the core's route split) and the clearing executor refuses it
  (`tests/integration/test_p030_repro_r1_clearing_below_step_postgres.py`);
* the measurement graph (`scripts/measure_clearing_min_amount_plan.py::_edge`): refused before the book is asked;
* the simulator seeder's initial lines: `tests/integration/test_p028_e2_accounting_step_postgres.py`, the `0.015`
  cell of `test_seeding_refuses_a_line_the_doors_would_refuse_and_names_it`.

Each refusal has its counter-check: the unmodified community and recipe load, and a whole-step amount reaches the
book.
"""

from __future__ import annotations

import copy
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMUNITIES = _REPO_ROOT / "seeds" / "communities"
if str(_COMMUNITIES) not in sys.path:
    sys.path.insert(0, str(_COMMUNITIES))

import community_schema  # noqa: E402
import recipe_schema  # noqa: E402

from app.db.models.equivalent import Equivalent  # noqa: E402
from app.utils.exceptions import BadRequestException  # noqa: E402

COMMUNITY = "riverside-town-50"
FINER = "0.015"


def _loaded():
    community = community_schema.load_community(COMMUNITY, root=_COMMUNITIES)
    recipe = recipe_schema.load_recipe(COMMUNITY, root=_COMMUNITIES)
    return community, recipe


def _precision(community, code: str) -> int:
    return next(e["precision"] for e in community["equivalents"] if e["code"] == code)


def test_the_unmodified_community_and_recipe_load() -> None:
    community, recipe = _loaded()
    assert community["trustlines"] and recipe["commands"]


def test_a_trust_line_limit_finer_than_the_step_is_refused() -> None:
    community, _ = _loaded()
    doctored = copy.deepcopy(community)
    line = doctored["trustlines"][0]
    assert _precision(doctored, line["equivalent"]) == 2, "stand: the line's equivalent is not at precision 2"
    line["limit"] = FINER
    with pytest.raises(community_schema.CommunityError, match="fractional digits"):
        community_schema.validate_community(doctored)


@pytest.mark.parametrize("op", ["payment", "clearing"])
def test_a_command_amount_finer_than_the_step_is_refused(op) -> None:
    community, recipe = _loaded()
    doctored = copy.deepcopy(recipe)
    command = next(c for c in doctored["commands"] if c["op"] == op)
    assert _precision(community, command["equivalent"]) == 2, "stand: the command's equivalent is not at precision 2"
    command["amount"] = FINER
    with pytest.raises(recipe_schema.RecipeError, match="fractional digits"):
        recipe_schema.validate_recipe(doctored, community)


class _Posting:
    def __init__(self) -> None:
        self.applied: list = []

    async def apply(self, effect) -> None:
        self.applied.append(effect)


class _Session:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, row) -> None:
        self.added.append(row)


async def _edge(posting, session, amount: str) -> None:
    from scripts.measure_clearing_min_amount_plan import _edge as edge, _make_participant

    equivalent = Equivalent(code="UAH", precision=2, is_active=True)
    await edge(session, posting, equivalent, _make_participant(1), _make_participant(2), Decimal(amount))


async def test_the_measurement_graph_refuses_an_edge_finer_than_the_step() -> None:
    posting, session = _Posting(), _Session()
    with pytest.raises(BadRequestException) as refused:
        await _edge(posting, session, FINER)
    assert refused.value.details["reason"] == "amount_precision_exceeded"
    assert posting.applied == [] and session.added == []


async def test_the_measurement_graph_takes_a_whole_step_edge() -> None:
    posting, session = _Posting(), _Session()
    await _edge(posting, session, "0.01")
    assert [effect.amount for effect in posting.applied] == [Decimal("0.01")]
    assert len(session.added) == 1
