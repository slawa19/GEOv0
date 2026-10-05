"""030 S1, F-030-10 (Codex R2b, item 5): a stale PASSED must not authorise clearing a newer hold.

SCHEDULE, real and barrier-driven (no synthetic verdict): verifier A verifies a sound equivalent and is parked
before it publishes; a criterion (b) defect is introduced (a re-digested PAYMENT intent, journal untouched);
verifier B finds FAILED, the reaction confirms it and commits a hold; A publishes its older PASSED; the admin
clears the hold through the real route. Two overlapping verifier runs need two processes on one database
without the integrity lock (no Redis, or an expired non-renewed TTL) - here two tasks of one process.

MECHANISM ASSERTION: A's verdict was computed before the defect (event order, A's outcome PASSED while every
verification after the defect is FAILED), and A published after B's hold committed.
"""

from __future__ import annotations

import asyncio
import copy

import pytest

from app.config import settings
from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import FAILED, PASSED, run_scheduled_reconciliation
from tests.conftest import MODE_B, sessionmaker_of
from tests.unit.test_p015_b4_wrong_writer_is_recorded_faithfully import _seed_triangle
from tests.unit.test_p015_step5a_reconciliation import _baseline, _fixture_debts, _pay, _verify
from tests.unit.test_p015_step5b_criterion_b import _a_findings, _operation, _rewrite_intent
from tests.unit.test_p015_step5c_reaction_and_hold import _hold_of


@MODE_B
@pytest.mark.asyncio
async def test_f030_10_a_stale_passed_published_after_a_hold_does_not_clear_it(client, db_session, monkeypatch) -> None:
    factory = sessionmaker_of(db_session)
    monkeypatch.setattr(settings, "ROUTING_PATH_FINDING_TIMEOUT_MS", 5000)  # the stand's budget (see 015 5b, F-028-11)
    triangle = await _seed_triangle(factory, trustlines=[("b", "a", "100")])
    await _fixture_debts(factory, triangle, [("a", "b", "10")])
    await _baseline(factory, triangle.equivalent.id)
    await _pay(factory, triangle, ["a", "b"], "5")
    eq = triangle.equivalent.id

    order: list[str] = []
    parked, go = asyncio.Event(), asyncio.Event()
    original = reconciliation.record_outcome
    a_task: list[asyncio.Task] = []

    async def record(session, outcome):
        if asyncio.current_task() is a_task[0]:
            order.append(f"A verified {outcome.status}")
            parked.set()
            await go.wait()
            order.append("A publishes")
        return await original(session, outcome)

    monkeypatch.setattr(reconciliation, "record_outcome", record)
    a_task.append(asyncio.create_task(run_scheduled_reconciliation(factory, equivalent_ids=[eq])))
    await asyncio.wait_for(parked.wait(), 30)

    envelope = await _operation(factory, equivalent_id=eq, kind="PAYMENT")
    intent = copy.deepcopy(envelope.intent)
    intent["locks"][0]["flows"][0]["amount"] = "4.00000000"
    await _rewrite_intent(factory, envelope.id, intent)
    order.append("defect")
    defect = await _verify(factory, eq)
    assert defect.status == FAILED and _a_findings(defect) == [], "stand: the defect must be criterion (b) only"

    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[eq])
    hold = await _hold_of(factory, eq)
    assert counts["hold_set"] == 1 and hold is not None, counts
    order.append("B held")

    go.set()
    a_counts = await asyncio.wait_for(a_task[0], 30)
    assert a_counts[PASSED] == 1 and a_counts["rows_inserted"] == 1, a_counts
    assert order == ["A verified PASSED", "defect", "B held", "A publishes"], order
    assert (await _verify(factory, eq)).status == FAILED, "the defect is still there when the admin clears"

    response = await client.post(
        f"/api/v1/admin/equivalents/{triangle.equivalent.code}/integrity-hold/clear",
        json={"reason": "p030 F-030-10 stand"}, headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )
    assert response.status_code == 409, f"a stale PASSED cleared a hold on a FAILED equivalent: {response.text}"
    assert response.json()["error"]["details"]["reason"] == "no_later_passed_reconciliation_result"
    assert await _hold_of(factory, eq) == hold
