"""Reproducer R-1 (p030 external review), the guard of F-030-1 since 030 S2: clearing never executes an amount
finer than the equivalent's step.

Target (owner decision 2026-10-04, `specs/028-backlog-rework/spec.md:28`; В2 of 030): clearing amounts are
multiples of the accounting step, and a database whose debts are finer than the step is reseeded, not repaired by
code. The stand builds such a database the one way that is left - debts finer than the step through `Book` in one
`TEST_FIXTURE` operation (until 030 S2 `scripts/seed_db.py` imported them so, as `SEED`, with no step check; that
importer is deleted) - takes the baseline and runs one ordinary pass through
`app/core/clearing/runner.py::run_clearing_pass`, the path of the tick, `/clearing/auto` and the periodic job.
The assertion is the CORRECT behaviour: every debt is either untouched or reduced by a multiple of 0.01, and a
finer occurrence is REFUSED by the executor (`occurrence_amount_not_in_step`), not rounded. The guard stays after
the reseed: it is what notices such a database (runbook of S1).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from decimal import Decimal

import pytest

from app.core.clearing.runner import ClearingPassError, run_clearing_pass
from app.core.clearing.service import ClearingOccurrenceRefused
from app.core.ledger.book import Book, NewDebt, operation_for
from app.core.ledger.reconciliation import open_verification_snapshot, take_baseline, verify_journal_equals_change
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import participant_uuid
from tests.p023_support import remaining_debts

pytestmark = MODE_B

STEP = Decimal("0.01")
PIDS = ["p030r1a", "p030r1b", "p030r1c"]


async def _seed_like_the_importer(session, code: str, amount: Decimal):
    eq = Equivalent(code=code, precision=2, is_active=True)
    session.add(eq)
    for pid in PIDS:
        session.add(
            Participant(
                id=participant_uuid(pid),
                pid=pid,
                display_name=pid.upper(),
                public_key=hashlib.sha256(pid.encode()).hexdigest(),
                type="person",
                status="active",
                profile={},
            )
        )
    await session.commit()
    ring = [(PIDS[i], PIDS[(i + 1) % 3]) for i in range(3)]  # (debtor, creditor): A->B, B->C, C->A
    for debtor, creditor in ring:
        session.add(
            TrustLine(
                from_participant_id=participant_uuid(creditor),
                to_participant_id=participant_uuid(debtor),
                equivalent_id=eq.id,
                limit=Decimal("1.00"),
                policy={"auto_clearing": True},
                status="active",
            )
        )
    await session.flush()
    # Debts finer than the step: TEST_FIXTURE, unscoped, NewDebt - the deleted importer's form (it wrote SEED).
    async with Book.operation(
        session,
        operation_for("TEST_FIXTURE", f"p030-r1:{code}:{uuid.uuid4()}", {"label": "p030-r1"}, scope_equivalent_ids=None),
    ) as posting:
        for debtor, creditor in ring:
            await posting.apply(
                NewDebt(
                    debtor_id=participant_uuid(debtor),
                    creditor_id=participant_uuid(creditor),
                    equivalent_id=eq.id,
                    amount=amount,
                )
            )
    await session.commit()
    return eq


def _record(name: str, facts: dict) -> None:
    root = os.environ.get("GEO_TEST_ARTIFACT_ROOT")
    if root:
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump(facts, fh, indent=2, default=str)


async def _run_cell(db_session, code: str, amount: str):
    amount = Decimal(amount)
    eq = await _seed_like_the_importer(db_session, code, amount)
    factory = sessionmaker_of(db_session)

    async with factory() as s:
        before = await remaining_debts(s, code)
    assert len(before) == 3 and all(row[3] == amount for row in before), f"stand: debts not seeded: {before}"

    async with factory() as s:
        taken = await take_baseline(s, eq.id)
        await s.commit()
    assert taken.edges_seen == 3, f"stand: baseline did not see the three edges: {taken}"

    handed: list = []
    refusal = None
    try:
        result = await run_clearing_pass(factory, code, on_committed=handed.append)
    except ClearingPassError as failed:
        result, refusal = failed.result, failed.cause

    async with factory() as s:
        after = await remaining_debts(s, code)
    async with factory() as s:
        await open_verification_snapshot(s)
        outcome = await verify_journal_equals_change(s, eq.id)
        await s.rollback()

    before_by_id = {row[0]: row[3] for row in before}
    after_by_id = {row[0]: row[3] for row in after}
    reductions = {i: before_by_id[i] - after_by_id.get(i, Decimal(0)) for i in before_by_id}
    facts = {
        "seeded": str(amount),
        "pass_status": result.status,
        "refusal": None if refusal is None else repr(refusal),
        "plans": result.plans,
        "executed_amounts": [o.amount_text for o in handed],
        "debts_after": [(d, c, str(a)) for _i, d, c, a in after],
        "reductions": sorted(str(r) for r in reductions.values()),
        "reconcile_status": outcome.status,
        "reconcile_findings": list(outcome.findings),
    }
    _record(f"r1_{amount}", facts)
    return result, handed, reductions, outcome, facts, refusal


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["0.015", "0.025"], ids=["cell_0_015", "cell_0_025"])
async def test_r1_clearing_reduces_debts_only_by_whole_steps(db_session, amount) -> None:
    code = "PR1A" if amount == "0.015" else "PR1B"
    result, handed, reductions, outcome, facts, refusal = await _run_cell(db_session, code, amount)

    # Precondition: the pass ran (it planned at least once on a real snapshot).
    assert result.plans >= 1, f"stand: the runner never planned: {facts}"
    # TARGET: every reduction is zero or a whole number of steps.
    off_step = {i: r for i, r in reductions.items() if r % STEP != 0}
    assert not off_step, f"clearing reduced debts by a non-multiple of {STEP}: {facts}"
    # And it is the executor's refusal by name that kept them - not a planner that happened to find nothing.
    assert isinstance(refusal, ClearingOccurrenceRefused), f"no refusal of the finer occurrence: {facts}"
    assert refusal.reason == "occurrence_amount_not_in_step", facts
    assert handed == [] and all(r == 0 for r in reductions.values()), f"a debt moved: {facts}"
    assert outcome.status == "PASSED", f"the refused clearing left a trace the reconciliation sees: {facts}"


@pytest.mark.asyncio
async def test_r1_control_whole_step_debts_are_cleared_fully(db_session) -> None:
    result, handed, reductions, outcome, facts, refusal = await _run_cell(db_session, "PR1C", "0.02")

    assert refusal is None and result.status == "complete", facts
    assert [o.amount_text for o in handed] == ["0.02000000"], f"control: the stand did not see the clearing: {facts}"
    assert all(r == Decimal("0.02") for r in reductions.values()), facts
    assert outcome.status == "PASSED", facts
