"""034 S1, reproducer of F-034-1 (`T3401`): a restarted run repeats the idempotency keys of its first launch.

THE PATH. `RunLifecycle.restart` (`app/core/simulator/run_lifecycle.py:496-497`) resets `sim_time_ms` and
`tick_index` under the SAME `run_id`. The heartbeat (`runtime_impl.py:900`) advances `tick_index` before each tick,
so the first tick of the first launch and the first tick after the restart both run at the same index. The key of a
simulated payment is `run_id|tick_index|sender|receiver|equivalent|amount|seq` (`real_runner_impl.py:693`) and it
becomes the payment's `tx_id`; nothing in it tells the two launches apart. When the restarted run plans the same
pair and amount at the same index, the payment service answers with the STORED result of the first launch
(`app/core/payments/service.py:952`) - no money moves - and the executor counts and publishes it as a payment made.

THE FIXTURE, AND WHY THE PLAN REALLY REPEATS. The planner sizes an amount from the capacity left AFTER the debts
(`real_payment_planner.py:416-442`), so the same seed alone does not repeat an amount once debts changed (`T3400`).
It does repeat when the sender's `amount_model.max` is below the capacity left in both launches: the cap is then the
model's, not the line's. Here the line is 1000.00, the opening debt 100.00, and the model allows 5.00-40.00. That
the restarted plan equals the first one AFTER the debts changed is asserted below as a control, on the real planner;
it is not constructed.

Nothing of the application is replaced: the real `RunLifecycle.restart`, the real planner, executor and payment
service on PostgreSQL. `_heartbeat_tick` repeats the two lines of the heartbeat that advance the tick; the heartbeat
itself waits on a wall clock.

TARGET (red on 75dafc82; green since the launch epoch entered the key, 034 S1a): what the restarted tick reports as
paid is what moved - a new `tx_id` and a real debt effect, or an honest refusal with nothing reported as paid.
COUNTER-CHECK (green before and after, and it must stay green): the same `tx_id` repeated inside ONE launch is
answered from its stored row and moves nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.core.simulator.run_lifecycle import RunLifecycle
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _OPENING,
    _Sse,
    _debts,
    _forget_the_route_cache,
    _install,
    _record_plans,
    _run_record,
    _runner,
    _scenario,
    _seed,
    _transactions,
    _utc_now,
    factory,
)
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

#: Far below the capacity left on the line in both launches (1000.00 - 100.00 - at most two payments).
_AMOUNT_MODEL = {"min": "5.00", "max": "40.00"}


def _scenario_with_a_bounded_amount(world) -> dict[str, Any]:
    scenario = _scenario(world)
    scenario["behaviorProfiles"] = [
        {"id": "payer", "props": {"amount_model": {world.equivalent.code: dict(_AMOUNT_MODEL)}}}
    ]
    for participant in scenario["participants"]:
        participant["behaviorProfileId"] = "payer"
    return scenario


def _new_run(world) -> RunRecord:
    run = _run_record(world, f"p034-s1-{uuid.uuid4().hex[:8]}")
    run.tick_index, run.sim_time_ms = 0, 0  # a run as `create_run` leaves it: the heartbeat has not ticked yet
    return run


async def _heartbeat_tick(runner, run: RunRecord) -> None:
    """One heartbeat iteration of a real-mode run: advance the tick, then run it (`runtime_impl.py:900-915`)."""

    run.tick_index += 1
    run.sim_time_ms = run.tick_index * 1000
    await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)


async def _restart(run: RunRecord, lock) -> None:
    """The real `RunLifecycle.restart` of a stopped run; only its collaborators are stand-ins."""

    async def _no_heartbeat(_run_id: str) -> None:
        return None

    unused = dict.fromkeys(["new_run_id", "get_scenario_raw", "edges_by_equivalent"])
    artifacts = type("A", (), {"start_events_writer": lambda _s, _r: None})()
    sse = type("S", (), {"prune_event_buffer_locked": lambda _s, _r: None})()
    run.state = "stopped"  # what `stop()` leaves; a restart of a stopped run is the product path
    await RunLifecycle(
        lock=lock, runs={run.run_id: run}, set_active_run_id=lambda *_: None, utc_now=_utc_now, sse=sse,
        heartbeat_loop=_no_heartbeat, publish_run_status=lambda _: None, run_to_status=lambda _: None,
        get_run_status_payload_json=lambda _: {}, real_max_in_flight_default=1, get_max_active_runs=lambda: 0,
        get_max_run_records=lambda: 0, logger=None, artifacts=artifacts, **unused,
    ).restart(run.run_id)


def _record_staged(monkeypatch) -> list[dict[str, Any]]:
    """Every call of the staged payment entry, with what it answered. Calls straight through."""

    calls: list[dict[str, Any]] = []
    original = PaymentService.create_payment_internal_staged

    async def recording(self_, sender_id, **kwargs):
        staged = await original(self_, sender_id, **kwargs)
        calls.append({
            "key": str(kwargs.get("idempotency_key")),
            "amount": str(kwargs.get("amount")),
            "tx_id": str(staged.result.tx_id),
            "status": str(staged.result.status),
            "written_here": bool(staged.written_here),
        })
        return staged

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", recording)
    return calls


def _pair_and_amount(plan: list[Any]) -> list[tuple[str, str, str]]:
    return [(str(a.sender_pid), str(a.receiver_pid), str(a.amount)) for a in plan]


@pytest.mark.asyncio
async def test_a_restarted_run_reports_as_paid_only_what_moved(factory, monkeypatch) -> None:  # noqa: F811
    world = await _seed(factory)
    pair = (world.sender.pid, world.receiver.pid)
    try:
        sse = _Sse()
        run = _new_run(world)
        runner = _runner(run, _scenario_with_a_bounded_amount(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        calls = _record_staged(monkeypatch)

        # ── first launch: its first tick pays ───────────────────────────────────────────────────────
        await _heartbeat_tick(runner, run)
        first_tick = run.tick_index
        assert len(plans) == 1 and len(plans[0]) == 1, plans
        amount = Decimal(plans[0][0].amount)
        assert amount > 0 and _pair_and_amount(plans[0]) == [(*pair, plans[0][0].amount)], plans[0]
        assert [(c["status"], c["written_here"]) for c in calls] == [("COMMITTED", True)], calls
        debts_after_first = await _debts(factory, world)
        assert debts_after_first == {pair: _OPENING + amount}, debts_after_first  # the debts DID change
        assert (sse.published("tx.updated"), run.committed_total) == (1, 1), sse.events

        # ── restart, and the first tick of the new launch ───────────────────────────────────────────
        await _restart(run, runner._lock)
        assert (run.tick_index, run.sim_time_ms, run.state) == (0, 0, "running"), run
        sse.events.clear()
        await _heartbeat_tick(runner, run)

        # Controls: the same tick index, and the real planner planned the same pair and positive amount again
        # although the debts changed in between. Without this the test would be about some other tick.
        assert run.tick_index == first_tick, (run.tick_index, first_tick)
        assert len(plans) == 2 and _pair_and_amount(plans[1]) == _pair_and_amount(plans[0]), plans
        assert run.last_error is None, run.last_error

        debts_after_restart = await _debts(factory, world)
        moved = debts_after_restart[pair] - debts_after_first[pair]
        reported = sse.published("tx.updated")
        restart_calls = calls[1:]
        transactions = await _transactions(factory, world)
    finally:
        _forget_the_route_cache(world)

    reused = [c["tx_id"] for c in restart_calls if c["tx_id"] == calls[0]["tx_id"]]
    assert moved == amount * reported and not (reported and reused), (
        f"the restarted run reported {reported} payment(s) of {amount} as made (tx.updated), committed_total "
        f"{run.committed_total}, while the debt moved by {moved}; tx_id of the first launch reused: {bool(reused)} "
        f"(first {calls[0]['tx_id']}, restart {[(c['tx_id'], c['status'], c['written_here']) for c in restart_calls]}); "
        f"stored payments of the pair: {transactions}. Expected: a new tx_id with a debt effect of {amount}, or "
        f"nothing reported as paid"
    )


@pytest.mark.asyncio
async def test_counter_check_the_same_tx_id_inside_one_launch_is_idempotent(factory, monkeypatch) -> None:  # noqa: F811
    """No restart between the two ticks: the tick is run again at the SAME index of the SAME launch."""

    world = await _seed(factory)
    pair = (world.sender.pid, world.receiver.pid)
    try:
        run = _new_run(world)
        runner = _runner(run, _scenario_with_a_bounded_amount(world), _Sse())
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        calls = _record_staged(monkeypatch)

        await _heartbeat_tick(runner, run)
        amount = Decimal(plans[0][0].amount)
        debts_after_first = await _debts(factory, world)
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)  # same launch, same tick index

        assert len(plans) == 2 and _pair_and_amount(plans[1]) == _pair_and_amount(plans[0]), plans
        debts_after_repeat = await _debts(factory, world)
        transactions = await _transactions(factory, world)
    finally:
        _forget_the_route_cache(world)

    assert amount > 0 and debts_after_first == {pair: _OPENING + amount}, debts_after_first
    assert [(c["status"], c["written_here"]) for c in calls] == [("COMMITTED", True), ("COMMITTED", False)], calls
    assert calls[1]["tx_id"] == calls[0]["tx_id"] and calls[1]["key"] == calls[0]["key"], calls
    assert debts_after_repeat == debts_after_first, (debts_after_first, debts_after_repeat)
    assert transactions == {calls[0]["tx_id"]: "COMMITTED"}, transactions
