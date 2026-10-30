"""054-A-7a review: a tick refused by a money stop is not a tick of an exhausted scenario (owner, 2026-10-30).

"If the automatic payments do not go through, the scenario is exhausted - rework the scenario." An equivalent stopped by
the operator, or held by the integrity reaction, is a different state: it is not the scenario that has nothing left to
do, so such a tick neither extends nor breaks the run of all-refused ticks (`_real_consec_all_rejected_ticks`) - it is
neutral, like an empty share. Until this review every refusal counted, and four ticks of a stopped equivalent ran the
stall clearing out of its cadence and paused the run with `pause_reason = "scenario_exhausted"`.

The stand is the one of `test_p054_A7a_scenario_exhausted.py` (real run, real PostgreSQL in mode B, the runtime's own
tick, its planner, executor, payment core and clearing runner; the heartbeat held, the ticks driven with chosen numbers,
none on the clearing cadence 25; `run_clearing_pass` wrapped by a recorder that returns the real pass's result). The
stop is the operator's real path - `PATCH /admin/equivalents/{code}` with `is_active=false` - and the hold is set as
the step-5c stand sets it (`hold_directly`: a FAILED result and the hold pointing at it).

ANTI-VACUUM, in the same run: two capacity-refused ticks first (the line lowered to its debt, as in the 7a stand) bring
the counter to 2, so a money-stop tick that counted would reach the clearing threshold at once; and for the operator
stop, the equivalent restarted afterwards, one more capacity-refused tick continues the counter from 2 to 3 and runs the
stall clearing on that tick.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.core.money_boundary import MoneyBoundary
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.runtime import runtime
from app.db.models.equivalent import Equivalent
from app.utils.exceptions import ConflictException
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_p054_A7a_scenario_exhausted import (
    EQ,
    _admin,
    _lower_the_line_to_its_debt,
    _pay,
    _record_clearing_passes,
    _start,
    _tick,
)
from tests.unit.test_p015_step5c_reaction_and_hold import hold_directly

pytestmark = MODE_B


def _record_refusal_reasons(monkeypatch) -> list[Any]:
    """The `details.reason` of every staged payment the core refused (raised or returned as a stored refusal)."""

    reasons: list[Any] = []
    original = PaymentService.create_payment_internal_staged

    async def recorded(self, *args, **kwargs):
        try:
            staged = await original(self, *args, **kwargs)
        except ConflictException as exc:
            reasons.append((exc.details or {}).get("reason"))
            raise
        if staged.refusal is not None:
            reasons.append((getattr(staged.refusal, "details", None) or {}).get("reason"))
        return staged

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", recorded)
    return reasons


async def _patch_active(client, active: bool) -> None:
    r = await client.patch(
        f"/api/v1/admin/equivalents/{EQ}",
        headers=_admin(),
        json={"is_active": active, "reason": "054-A-7a review stand"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is active, r.json()
    PaymentRouter.invalidate_cache(EQ)


async def _hold(db_session) -> None:
    factory = sessionmaker_of(db_session)
    async with factory() as s:
        eq_id = await s.scalar(select(Equivalent.id).where(Equivalent.code == EQ))
    assert eq_id is not None, f"premise: the scenario's equivalent {EQ} is in the database"
    await hold_directly(factory, eq_id)
    PaymentRouter.invalidate_cache(EQ)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["operator_stop", "integrity_hold"])
async def test_a_money_stop_tick_neither_counts_nor_breaks_the_stall(client, db_session, monkeypatch, stop):
    run_id = await _start(client, monkeypatch)
    try:
        seeded = await _pay(client, run_id, "p2", "p1", "1")  # seeds the scenario; p2 owes p1 1.00
        assert seeded.status_code == 200, seeded.text
        await _lower_the_line_to_its_debt(db_session, run_id)
        passes = _record_clearing_passes(monkeypatch, run_id)
        run = runtime.get_run(run_id)
        assert int(runtime._real_runner._tick._clearing_every_n_ticks) == 25

        # Two capacity-refused ticks: the counter is one tick short of the stall clearing.
        for index in (1, 2):
            await _tick(run_id, index)
        assert run.committed_total == 0 and run.errors_total == 0 and run.attempts_total > 0, (
            run.committed_total, run.errors_total, run.attempts_total, run.last_error)
        assert run._real_consec_all_rejected_ticks == 2 and passes == [], (run._real_consec_all_rejected_ticks, passes)

        expected_reason = (
            MoneyBoundary.EQUIVALENT_INACTIVE_REASON if stop == "operator_stop"
            else MoneyBoundary.EQUIVALENT_INTEGRITY_HOLD_REASON
        )
        if stop == "operator_stop":
            await _patch_active(client, False)
        else:
            await _hold(db_session)
        reasons = _record_refusal_reasons(monkeypatch)
        attempts_before = int(run.attempts_total)

        for index in (3, 4, 5, 6):
            await _tick(run_id, index)

        # NON-VACUITY: the planner planned on every tick, and the core refused every payment for the money stop.
        assert int(run.attempts_total) - attempts_before >= 4, (attempts_before, run.attempts_total)
        assert reasons and all(r == expected_reason for r in reasons), reasons
        assert run.committed_total == 0 and run.errors_total == 0, (run.committed_total, run.errors_total, run.last_error)
        # THE PROPERTY: the stopped ticks did not count - no stall clearing out of the cadence, no pause.
        assert passes == [], f"clearing passes by tick actual={passes} expected=[] (the stop is not a stall)"
        assert run._real_consec_all_rejected_ticks == 2, run._real_consec_all_rejected_ticks
        assert (run.state, run.pause_reason) == ("running", None), (run.state, run.pause_reason, run.last_error)
        got = await client.get(f"/api/v1/simulator/runs/{run_id}", headers=_admin())
        assert got.status_code == 200, got.text
        assert (got.json()["state"], got.json()["pause_reason"]) == ("running", None), got.json()

        if stop == "operator_stop":
            # The operator restarts the equivalent: the next capacity-refused tick continues the counter from 2, and
            # it is the third counted one - the stall clearing runs on it.
            await _patch_active(client, True)
            await _tick(run_id, 7)
            assert run._real_consec_all_rejected_ticks == 3, run._real_consec_all_rejected_ticks
            assert passes == [7], f"clearing passes by tick actual={passes} expected=[7]"
            assert (run.state, run.pause_reason) == ("running", None), (run.state, run.pause_reason)
    finally:
        await runtime.stop(run_id)
