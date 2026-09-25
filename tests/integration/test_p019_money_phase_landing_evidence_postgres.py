"""Programme 019, stage-3 review (Codex, frozen `fae2208`, P2 #2): a STORED row replayed by a phase was
taken as proof that the phase's own commit landed.

THE PATH. A run's tick commits payment A (seq 0). The run is restarted at the same tick index - its
identity and transaction rows kept (`run_lifecycle.py` resets `tick_index`) - and the repeated plan
holds A again and a new payment B (seq 1). A is answered from its stored row (idempotency); B is staged
fresh inside the phase's transaction. The phase's COMMIT then fails and so does the rollback, so the
money-phase owner resolves the outcome by the phase's identifiers (`_attempt_landed`). Before the fix
the executor listed A among them (`staged_tx_ids` took every result's `tx_id`), A's historical row
answered "landed", and the owner published B's success and counted a committed tick although B has no
row and moved no money.

The commit failure is the one injected step (an infrastructure failure: the same form as the
unknown-outcome tests of `money_replay.py`); everything else is the real tick on PostgreSQL.

CONTROLS: A committed in tick 1; in tick 2 A was answered from its stored row and B was staged fresh;
the commit really failed with its outcome unknown. TARGET: B has no row and no debt, and nothing of it
was published or counted as committed.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.core.payments.service import PaymentService
from app.core.simulator.real_payment_action import _RealPaymentAction
from app.core.simulator.real_payments_executor import RealPaymentsExecutor
from tests.integration.p019_stand import tx_row
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _OPENING,
    _Sse,
    _debts,
    _forget_the_route_cache,
    _install,
    _run_record,
    _runner,
    _scenario,
    _seed,
    factory,
)
from tests.p019_support import require_target


@pytest.mark.asyncio
async def test_a_replayed_stored_row_is_not_evidence_that_the_phase_landed(factory, monkeypatch) -> None:  # noqa: F811
    world = await _seed(factory)
    sse = _Sse()
    run = _run_record(world, f"p019-landing-{uuid.uuid4().hex[:8]}")
    runner = _runner(run, _scenario(world), sse, actions_per_tick_max=2)
    _install(monkeypatch, factory)
    a = _RealPaymentAction(0, world.equivalent.code, world.sender.pid, world.receiver.pid, "1.00")
    b = _RealPaymentAction(1, world.equivalent.code, world.sender.pid, world.receiver.pid, "2.00")
    plans = [[a], [a, b]]
    monkeypatch.setattr(runner, "_plan_real_payments", lambda *_a, **_kw: list(plans[0 if not calls else 1]))

    calls: list[dict[str, Any]] = []
    original_staged = PaymentService.create_payment_internal_staged

    async def recording(self_, sender_id, **kwargs):
        staged = await original_staged(self_, sender_id, **kwargs)
        calls.append({**kwargs, "status": staged.result.status, "fresh": staged.post_commit_effects is not None})
        return staged

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", recording)

    # ── tick 1: A commits ─────────────────────────────────────────────────────────────────────
    try:
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), 90.0)
    finally:
        _forget_the_route_cache(world)
    assert [(c["status"], c["fresh"]) for c in calls] == [("COMMITTED", True)], calls
    assert run._real_money_committed_ticks_total == 1
    tick1_calls = len(calls)

    # ── tick 2 at the same tick index: A replays its stored row, B is fresh; the COMMIT fails ────
    original_execute = RealPaymentsExecutor.execute_planned_payments
    failed_commit: list[str] = []

    async def then_the_commit_fails(self, *, session, **kwargs):
        result = await original_execute(self, session=session, **kwargs)

        async def lost_commit():
            failed_commit.append("commit")
            raise RuntimeError("p019: the connection was lost during COMMIT")

        async def lost_rollback():
            failed_commit.append("rollback")
            raise RuntimeError("p019: the connection was lost during ROLLBACK")

        session.commit = lost_commit
        session.rollback = lost_rollback
        return result

    monkeypatch.setattr(RealPaymentsExecutor, "execute_planned_payments", then_the_commit_fails)
    sse.events.clear()
    try:
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), 90.0)
    finally:
        _forget_the_route_cache(world)

    tick2 = calls[tick1_calls:]
    b_tx = [c for c in tick2 if c["amount"] == "2.00"]
    # ── controls ──────────────────────────────────────────────────────────────────────────────
    assert [(c["amount"], c["status"], c["fresh"]) for c in tick2] == [
        ("1.00", "COMMITTED", False),
        ("2.00", "COMMITTED", True),
    ], tick2
    assert failed_commit[:1] == ["commit"], failed_commit
    assert run.last_error is not None and run.last_error["code"] == "REAL_MODE_TICK_FAILED", run.last_error

    b_row = await tx_row(factory, str(b_tx[0]["idempotency_key"]))
    debts = await _debts(factory, world)
    updated = [e for e in sse.events if e.get("type") == "tx.updated"]
    require_target(
        b_row is None
        and debts == {(world.sender.pid, world.receiver.pid): _OPENING + Decimal("1.00")}
        and updated == []
        and run._real_money_committed_ticks_total == 1,
        f"B row {b_row!r}, debts {debts!r}, tx.updated published {len(updated)}, committed ticks "
        f"{run._real_money_committed_ticks_total}, last error {run.last_error!r}",
    )
