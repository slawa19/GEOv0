"""030 S2, §15 review `T3092` finding 1: the clearing executor's step refusal stays NAMED where it surfaces.

`ClearingOccurrenceRefused("occurrence_amount_not_in_step")` (F-030-1) is a logical refusal - the database holds debts
finer than the equivalent's step and is to be reseeded (runbook of S1) - not a failed execution. Through the REAL
runner and executor on a disposable clone (`MODE_B`), with a ring of three debts of `0.015` at precision 2:

* the Interact action `clearing-real` answers `409 CLEARING_REFUSED` with `details.reason`, not `500 CLEARING_FAILED`,
  and no debt moves;
* the tick (unit stand) records the reason in the run's `last_error` - `tests/unit/test_p030_s2_tick_names_the_step_refusal.py`.

The reason is NOT a money stop (`MoneyBoundary.MONEY_STOP_REASONS` means "the operator or the reaction stopped money
in this equivalent"; the tick skips those without a run error). The counter-check: a whole-step ring still clears
through the same route (`test_p023_d_interact_through_runner_postgres.py::test_interact_clears_through_the_runner_…`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.clearing.service import OCCURRENCE_AMOUNT_NOT_IN_STEP
from tests.conftest import MODE_B
from tests.integration.test_p023_d_interact_through_runner_postgres import HEADERS, PIDS, URL, interact_run  # noqa: F401
from tests.p020_support import debt_uuid, ring, seed_graph
from tests.p023_support import fresh_read, positive_debt_total

pytestmark = MODE_B

CODE = "PQS"
FINER = ring(PIDS[:3], ["0.015", "0.015", "0.015"], [debt_uuid(0x30D2, k) for k in range(3)])


@pytest.mark.asyncio
async def test_interact_answers_a_step_refusal_with_a_named_409(db_session, client, interact_run) -> None:  # noqa: F811
    await seed_graph(db_session, CODE, FINER, precision=2)
    assert await fresh_read(db_session, positive_debt_total, CODE) == Decimal("0.045"), "stand: the ring is not seeded"

    response = await client.post(URL, headers=HEADERS, json={"equivalent": CODE})

    body = response.json()
    assert response.status_code == 409 and body.get("code") == "CLEARING_REFUSED", f"{response.status_code} {body!r}"
    assert (body.get("details") or {}).get("reason") == OCCURRENCE_AMOUNT_NOT_IN_STEP, body
    assert await fresh_read(db_session, positive_debt_total, CODE) == Decimal("0.045"), "a debt moved"
    assert interact_run == [], "a clearing.done was emitted for a refused pass"
