"""028 F-028-34 (narrowed, orchestrator 2026-10-04): criterion (b) for an INJECT, per equivalent and in the step.

The base summed the `inject_debt` effects of EVERY equivalent of the event against the entries of one (`_equivalent_id`
was unused), so the bound of one equivalent was masked by the effects of another; and the grain was the finer of a
cent and the step, so at precision 0 an entry of `0.50` passed. Each case is a real inject, then a coordinated rewrite
of the entry AND the debt around the application (criterion (a) stays silent; only (b) can see it).

Mode B: the inject commits, and the rewrite runs on a connection of its own (`tests/ledger_corruption.py`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.ledger.reconciliation import FAILED
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_p028_e2_simulator_in_the_step_postgres import _inject, _world
from tests.unit.test_p015_step5a_reconciliation import _verify
from tests.unit.test_p015_step5b_criterion_b import _debt_id, _move_entry_and_debt, _operation, _operation_entries


async def _rewritten(db_session, precisions, effects, *, equivalent: str, new_after: str):
    factory = sessionmaker_of(db_session)
    eqs, c, d = await _world(db_session, precisions)
    await _inject(db_session, eqs, c, d, effects)
    eq = eqs[equivalent]
    envelope = await _operation(factory, equivalent_id=eq.id, kind="INJECT")
    (entry,) = [e for e in await _operation_entries(factory, envelope.id) if e.equivalent_id == eq.id]
    debt_id = await _debt_id(factory, eq.id, d.id, c.id)
    await _move_entry_and_debt(factory, entry, new_after=Decimal(new_after), debt_id=debt_id)
    outcome = await _verify(factory, eq.id)
    assert [f for f in outcome.findings if not f["kind"].startswith("b_")] == [], f"stand: not coordinated {outcome}"
    return outcome


@MODE_B
@pytest.mark.asyncio
async def test_the_inject_bound_is_per_equivalent_not_masked_by_another(db_session) -> None:
    """A gets 1, B gets 5; A's entry is rewritten to 4. Base: 4 <= 1 + 5, PASSED. Now: 4 > 1 (A's own effects)."""

    outcome = await _rewritten(db_session, {"A": 2, "B": 2}, [("A", "1"), ("B", "5")], equivalent="A", new_after="4")
    assert outcome.status == FAILED, outcome
    assert {f["rule"] for f in outcome.findings} == {"more_debt_than_the_intent_names"}, outcome.findings


@MODE_B
@pytest.mark.asyncio
async def test_an_inject_entry_off_the_step_is_failed_at_precision_0(db_session) -> None:
    """Precision 0, an inject of 2 rewritten to 1.50. Base: the grain was a cent, PASSED. Now: not a whole unit."""

    outcome = await _rewritten(db_session, {"A": 0}, [("A", "2")], equivalent="A", new_after="1.50")
    assert outcome.status == FAILED, outcome
    assert {f["rule"] for f in outcome.findings} == {"delta_is_not_whole_cents"}, outcome.findings


@MODE_B
@pytest.mark.asyncio
async def test_control_the_honest_two_equivalent_inject_passes_criterion_b(db_session) -> None:
    """Anti-vacuum for the filter: the same event unrewritten - each equivalent's entry is within its own effects."""

    factory = sessionmaker_of(db_session)
    eqs, c, d = await _world(db_session, {"A": 2, "B": 0})
    await _inject(db_session, eqs, c, d, [("A", "1.25"), ("B", "5")])
    for eq in eqs.values():
        outcome = await _verify(factory, eq.id)
        assert [f for f in outcome.findings if f["kind"].startswith("b_")] == [], (eq.code, outcome.findings)
