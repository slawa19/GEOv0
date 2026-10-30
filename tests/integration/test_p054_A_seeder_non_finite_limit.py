"""054-A-13: a non-finite scenario limit is refused by name by the seeder (brother of 034 S4b).

Defect (2026-10-30): `RealScenarioSeeder.seed_scenario_into_db` parsed the limit under `try` and then compared it with
zero outside it. `Decimal("NaN")` parses, and ordering it RAISES `InvalidOperation`: the seeding - and the tick that
runs it - failed with an unnamed error. `Infinity` passed the comparison and was dropped by the storability door
without a word. Both are now `ScenarioTrustLineRefused` with the reason `money_finiteness` and the line, as the doors
of the seeder refuse a limit finer than the step.

Real path: the seeder itself on PostgreSQL (mode B), the stage that owns the defect; the tick's handling of a named
refusal (`fail_run` with `SCENARIO_TRUSTLINE_REFUSED`) is covered by `test_p024_run_refuses_a_real_participant_postgres`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.simulator.real_scenario_seeder import RealScenarioSeeder, ScenarioTrustLineRefused
from app.db.models.equivalent import Equivalent
from app.db.models.trustline import TrustLine
from app.utils.validation import MONEY_FINITENESS
from tests.conftest import MODE_B

pytestmark = MODE_B


def _scenario(limit: str) -> dict:
    n = uuid.uuid4().hex[:6].upper()
    a, b = f"A13_A_{n}", f"A13_B_{n}"
    line = {"from": a, "to": b, "equivalent": f"A13{n}", "limit": limit}
    return {"equivalents": [f"A13{n}"], "participants": [{"id": a}, {"id": b}], "trustlines": [line]}


@pytest.mark.parametrize("limit", ["NaN", "Infinity", "-Infinity", "sNaN"])
@pytest.mark.asyncio
async def test_a_non_finite_limit_is_refused_by_name(db_session, limit) -> None:
    scenario = _scenario(limit)
    with pytest.raises(ScenarioTrustLineRefused) as caught:
        await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=scenario)
    line = scenario["trustlines"][0]
    assert caught.value.details["reason"] == MONEY_FINITENESS
    assert caught.value.details["line"] == f"{line['from']}->{line['to']} {line['equivalent']}"
    assert caught.value.permanent, "a non-finite limit does not clear by itself"


@pytest.mark.asyncio
async def test_control_a_finite_limit_is_seeded(db_session) -> None:
    scenario = _scenario("10")
    await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=scenario)
    await db_session.flush()
    eq_id = (
        await db_session.execute(select(Equivalent.id).where(Equivalent.code == scenario["equivalents"][0]))
    ).scalar_one()
    limits = (await db_session.execute(select(TrustLine.limit).where(TrustLine.equivalent_id == eq_id))).scalars()
    assert list(limits) == [Decimal("10")]
