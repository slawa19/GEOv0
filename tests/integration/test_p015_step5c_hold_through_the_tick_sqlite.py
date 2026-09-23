"""Step 5c through the real-mode TICK: an integrity-hold refusal is a rejection, not an error of the run.

The rule is T1544's, and so is the stand (`test_p015_t1544_operator_stop_through_the_tick_sqlite.py`, a
mode-B PostgreSQL clone since 017 stage 3 slice S2a - it was a SQLite file before): this module drives
`RealRunner.tick_real_mode` itself - not the phase functions - for MORE ticks than the consecutive-failure limit, with the equivalent under an integrity hold instead of deactivated, and each test
asserts that the hold refusal really happened, so a path that never ran cannot pass.

Asserted after every run, because a refusal that works when a function is called directly can still damage
the run through its caller: the run is `running`, `errors_total == 0`, no consecutive tick failures,
`last_error is None`; the published phase is reset; the refused inject is consumed, not left pending; no
debt and no envelope was written.

The hold is set directly (`hold_directly`): the subject here is the tick's classification of the refusal.
The reaction that sets a hold in production is exercised in `tests/unit/test_p015_step5c_reaction_and_hold.py`.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.core.payments.engine import PaymentEngine
from app.core.payments.service import PaymentService
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from app.utils.exceptions import ConflictException
from tests.debt_setup import debt_fixture_setup
from tests.simulator_tick_stand import install_tick_stand as _install
from tests.integration.test_p015_t1544_operator_stop_through_the_tick_sqlite import (  # noqa: F401 - fixture
    _Artifacts,
    _assert_the_run_was_not_charged,
    _debts,
    _messages,
    _run,
    _runner,
    _seed,
    _ticks,
    _trust,
    factory,
)
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

HOLD = PaymentEngine.EQUIVALENT_INTEGRITY_HOLD_REASON


@pytest.mark.asyncio
async def test_step5c_a_held_inject_is_consumed_and_does_not_fail_the_run(factory, monkeypatch, caplog) -> None:
    """MUTATION: match only `equivalent_inactive` in the inject owner's handler - the event stays pending,
    every tick fails on it and the run is stopped after the limit, red."""
    eq, (creditor, debtor) = await _seed(factory, ["C", "D"])
    await _trust(factory, eq, creditor, debtor, "100.00")
    await hold_directly(factory, eq.id)
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": creditor.pid}, {"id": debtor.pid}],
        "trustlines": [
            {"from": creditor.pid, "to": debtor.pid, "equivalent": eq.code, "limit": "100.00", "status": "active"}
        ],
        "behaviorProfiles": [],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {"op": "inject_debt", "from": creditor.pid, "to": debtor.pid, "equivalent": eq.code,
                     "amount": "5.00"}
                ],
            }
        ],
    }
    artifacts = _Artifacts()
    run = _run(f"s5c-inject-{uuid.uuid4().hex[:6]}", [creditor, debtor], eq.code, intensity=0)
    runner = _runner(run, scenario, actions=1, clearing_every=10_000, artifacts=artifacts)
    _install(monkeypatch, factory)
    limit = int(runner._real_max_consec_tick_failures_limit)
    assert limit >= 1, f"premise: the consecutive-failure limit is disabled ({limit})"

    with caplog.at_level(logging.WARNING):
        await _ticks(runner, run, limit + 2)

    _assert_the_run_was_not_charged(run)
    refusals = _messages(caplog, f"simulator.real.inject.refused_{HOLD}")
    assert len(refusals) == 1, (
        f"premise and consumption: expected the hold refusal exactly once over {limit + 2} ticks, got {refusals}"
    )
    notes = [
        p for p in artifacts.payloads
        if p.get("type") == "note"
        and (p.get("scenario") or {}).get("description") == "inject refused (equivalent integrity hold)"
    ]
    assert len(notes) == 1, artifacts.payloads
    assert 0 in run._real_fired_scenario_event_indexes, "the refused inject was left pending"
    assert await _debts(factory, eq) == []
    async with factory() as s:
        assert await s.scalar(text("SELECT count(*) FROM debt_operations")) == 0


@pytest.mark.asyncio
async def test_step5c_a_held_tick_clearing_does_not_spend_the_error_budget(factory, monkeypatch, caplog) -> None:
    """MUTATION: match only `equivalent_inactive` in the tick clearing handler - `errors_total` grows by one
    per clearing tick and `last_error` is `CLEARING_ERROR`, red; drop the phase reset there - the published
    phase stays `clearing`, red."""
    eq, people = await _seed(factory, ["A", "B", "C"])
    a, b, c = people
    ring = [(a, b), (b, c), (c, a)]  # (debtor, creditor)
    for debtor, creditor in ring:
        await _trust(factory, eq, creditor, debtor, "1000.00")
    ring_debts = [
        Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal("10.00"))
        for debtor, creditor in ring
    ]
    async with factory() as s:
        async with debt_fixture_setup(s, label="setup"):
            s.add_all(ring_debts)
        await s.commit()
    await hold_directly(factory, eq.id)
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": p.pid} for p in people],
        "trustlines": [
            {"from": creditor.pid, "to": debtor.pid, "equivalent": eq.code, "limit": "1000.00", "status": "active"}
            for debtor, creditor in ring
        ],
        "behaviorProfiles": [],
    }
    run = _run(f"s5c-clearing-{uuid.uuid4().hex[:6]}", people, eq.code, intensity=0)
    runner = _runner(run, scenario, actions=1, clearing_every=1, artifacts=_Artifacts())
    _install(monkeypatch, factory)
    assert runner._real_tick_clearing_coordinator._clearing_policy == "static", (
        "premise: the stand needs the static clearing policy, which clears on every tick here"
    )
    ticks = int(runner._real_max_consec_tick_failures_limit) + 2

    with caplog.at_level(logging.INFO):
        await _ticks(runner, run, ticks)

    _assert_the_run_was_not_charged(run)
    assert run.current_phase is None, f"the refused clearing left the published phase at {run.current_phase!r}"
    refusals = _messages(caplog, f"simulator.real.clearing_refused_{HOLD}")
    assert len(refusals) == ticks, f"premise: expected the hold refusal on each of {ticks} ticks, got {refusals}"
    assert await _debts(factory, eq) == [Decimal("10.00")] * 3


@pytest.mark.asyncio
async def test_step5c_a_held_staged_payment_is_rejected_and_the_tick_continues(
    factory, monkeypatch, caplog
) -> None:
    """The payments phase's existing 4xx rule, proved for the hold by running it past the failure limit.

    MUTATION: make `refuse_inactive_equivalents` and the prepare check ignore the hold - payments commit
    into the held equivalent, red."""
    eq, (sender, receiver) = await _seed(factory, ["S", "R"])
    await _trust(factory, eq, receiver, sender, "1000.00")
    await hold_directly(factory, eq.id)
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": sender.pid}, {"id": receiver.pid}],
        "trustlines": [
            {"from": receiver.pid, "to": sender.pid, "equivalent": eq.code, "limit": "1000.00", "status": "active"}
        ],
        "behaviorProfiles": [],
    }
    run = _run(f"s5c-payment-{uuid.uuid4().hex[:6]}", [sender, receiver], eq.code, intensity=100)
    runner = _runner(run, scenario, actions=1, clearing_every=10_000, artifacts=_Artifacts())
    _install(monkeypatch, factory)

    outcomes: list[str] = []
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        try:
            staged = await original(self, *args, **kwargs)
        except ConflictException as exc:
            outcomes.append(f"refused:{(exc.details or {}).get('reason')}")
            raise
        outcomes.append(f"result:{staged.result.status}")
        return staged

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    ticks = int(runner._real_max_consec_tick_failures_limit) + 1

    with caplog.at_level(logging.WARNING):
        await _ticks(runner, run, ticks)

    refused = [o for o in outcomes if o == f"refused:{HOLD}"]
    assert refused, f"premise: no staged payment was refused by the hold: {outcomes}"
    assert refused == outcomes, outcomes
    assert run.rejected_total == len(refused), (run.rejected_total, outcomes)
    assert run.committed_total == 0
    _assert_the_run_was_not_charged(run)
    assert await _debts(factory, eq) == []
    async with factory() as s:
        assert await s.scalar(select(func.count(Transaction.id))) == 0
