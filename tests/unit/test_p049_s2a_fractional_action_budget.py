"""049 S2a (`T4915`, decision 10): the tick's action budget is fractional, carried between ticks.

THE DEFECT (red on `5d0779a8`). `RealPaymentPlanner.plan_payments` sized a tick as
`max(1, int(ACTIONS_PER_TICK_MAX * intensity))`: any intensity above zero planned at least one payment per 1 s tick,
so the visitor's slow step «2 %» (one payment every 2.5 s) planned ten payments in ten ticks instead of four.

THE RULE. Tick `t` (the run's `tick_index`, 1 for a run's first tick) at rate `r = ACTIONS_PER_TICK_MAX * intensity`
plans `floor(r * t) - floor(r * (t - 1))` actions. This is the accumulator `carry += r; actions = floor(carry);
carry -= actions` started at 0, with its carry derived from the tick number (`frac(r * (t - 1))`) instead of stored:
a replay of the same tick's money phase (`money_replay.py`) plans the same count, a paused run does not advance
`tick_index`, and a restart sets it to 0.

The planner is driven with the configured `SIMULATOR_ACTIONS_PER_TICK_MAX` (20) on a mesh where every planning slot
can be filled, so the planned count IS the budget (anti-vacuum: 15 % must fill 30 of 30).
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone
from typing import Any

import pytest

from app.config import settings
from app.core.simulator.models import RunRecord
from app.core.simulator.real_payment_action import _RealPaymentAction
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.core.simulator.runtime import runtime
from tests.unit.test_p024_heartbeat_follows_every_entry_into_running import _started, clock  # noqa: F401

ACTIONS_PER_TICK_MAX = 20


def _runner() -> RealRunnerImpl:
    return RealRunnerImpl(
        lock=threading.RLock(),
        get_run=lambda _rid: None,  # type: ignore[arg-type]
        get_scenario_raw=lambda _sid: {},
        sse=None,  # type: ignore[arg-type]
        artifacts=None,  # type: ignore[arg-type]
        utc_now=lambda: datetime.now(timezone.utc),
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: False,
        actions_per_tick_max=ACTIONS_PER_TICK_MAX,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )


def _mesh(n: int = 5) -> dict[str, Any]:
    pids = [f"P{i}" for i in range(n)]
    return {
        "scenario_id": "s",
        "equivalents": ["UAH"],
        "participants": [{"id": p, "type": "person", "groupId": "g1", "behaviorProfileId": "default"} for p in pids],
        "behaviorProfiles": [{"id": "default", "props": {"tx_rate": 1.0, "equivalent_weights": {"UAH": 1.0}}}],
        "trustlines": [
            {"equivalent": "UAH", "from": a, "to": b, "limit": "1000", "status": "active"}
            for a in pids
            for b in pids
            if a != b
        ],
    }


def _run(intensity_percent: int) -> RunRecord:
    run = RunRecord(run_id="r", scenario_id="s", mode="real", state="running")
    run.seed = 4242
    run.intensity_percent = intensity_percent
    return run


def _planned_per_tick(runner: RealRunnerImpl, run: RunRecord, ticks: range) -> list[int]:
    scenario = _mesh()
    counts = []
    for t in ticks:
        run.tick_index = t
        run.sim_time_ms = t * 1000
        counts.append(len(runner._plan_real_payments(run, scenario)))
    return counts


def test_the_configured_maximum_is_the_one_decision_10_counts_with() -> None:
    # Decision 10's three steps (2 % = 1 per 2.5 s, 5 % = 1 per s, 15 % = 3 per s) are stated for 20 actions per tick.
    assert settings.SIMULATOR_ACTIONS_PER_TICK_MAX == ACTIONS_PER_TICK_MAX


@pytest.mark.parametrize(
    ("intensity_percent", "expected_in_ten_ticks"),
    [(2, 4), (5, 10), (15, 30), (0, 0)],
)
def test_ten_ticks_plan_exactly_the_fractional_budget(intensity_percent: int, expected_in_ten_ticks: int) -> None:
    """TARGET (049 (з)): 2 % plans 4 actions in 10 ticks (was 10), 5 % — 10, 15 % — 30, 0 % — 0."""

    runner = _runner()
    for first_tick in (1, 7, 123):  # any ten consecutive ticks, not only the run's first ten
        counts = _planned_per_tick(runner, _run(intensity_percent), range(first_tick, first_tick + 10))
        assert sum(counts) == expected_in_ten_ticks, (intensity_percent, first_tick, counts)


def test_at_two_percent_a_payment_comes_every_two_or_three_ticks() -> None:
    counts = _planned_per_tick(_runner(), _run(2), range(1, 41))
    assert set(counts) <= {0, 1}, counts
    ticks_with_action = [t for t, n in zip(range(1, 41), counts) if n]
    gaps = {b - a for a, b in zip(ticks_with_action, ticks_with_action[1:])}
    assert gaps == {2, 3}, (ticks_with_action, gaps)


def test_a_zero_action_tick_is_an_ordinary_empty_plan() -> None:
    """At 2 % most ticks plan nothing: the planner returns an empty list, it does not raise."""

    counts = _planned_per_tick(_runner(), _run(2), range(1, 3))
    assert counts == [0, 0]


def test_a_replanned_tick_spends_its_budget_once() -> None:
    """The money phase's bounded replay (`money_replay.py`) replans the SAME tick: the count must not move."""

    runner, run, scenario = _runner(), _run(2), _mesh()
    for t in range(1, 11):
        run.tick_index = t
        first = runner._plan_real_payments(run, scenario)
        assert runner._plan_real_payments(run, scenario) == first


def test_the_slow_step_from_a_runs_first_tick() -> None:
    """The heartbeat's first tick is `tick_index` 1 and the carry starts at 0: the first payment comes on tick 3."""

    assert _planned_per_tick(_runner(), _run(2), range(1, 11)) == [0, 0, 1, 0, 1, 0, 0, 1, 0, 1]


@pytest.mark.parametrize(("before", "after"), [(15, 2), (2, 15), (100, 2), (2, 5), (5, 2), (15, 0)])
def test_an_intensity_change_never_bursts_above_the_new_rate(before: int, after: int) -> None:
    """The tick after a change plans at most `ceil(20 * after / 100)`: no budget saved at the old rate is spent."""

    runner, run, scenario = _runner(), _run(before), _mesh()
    ceiling = math.ceil(ACTIONS_PER_TICK_MAX * after / 100)
    for change_at in range(1, 30):
        run.intensity_percent = before
        run.tick_index = change_at - 1
        runner._plan_real_payments(run, scenario)
        run.intensity_percent = after
        for t in range(change_at, change_at + 5):
            run.tick_index = t
            assert len(runner._plan_real_payments(run, scenario)) <= ceiling, (before, after, change_at, t)


def _scripted_rejection(seq: int) -> _RealPaymentAction:
    # A scripted payment of a sender the run does not hold is refused as `REJECTED` without touching the database
    # (`real_payments_executor.py`, `SENDER_NOT_FOUND` of a scripted action).
    return _RealPaymentAction(seq, "UAH", "nobody", "P1", "1", idempotency_key=f"story-{seq}")


def _unknown_sender_error(seq: int) -> _RealPaymentAction:
    return _RealPaymentAction(seq, "UAH", "nobody", "P1", "1")


async def _execute(runner: RealRunnerImpl, run: RunRecord, planned: list[Any]) -> int:
    res = await runner._real_payments_executor.execute_planned_payments(
        session=None,
        run_id="r",
        run=run,
        planned=planned,
        equivalents=["UAH"],
        sender_id_by_pid={},
        max_in_flight=1,
        max_timeouts_per_tick=0,
        fail_run=lambda *_a: None,
    )
    assert run._real_consec_all_rejected_ticks == res.stall_ticks
    return int(res.stall_ticks)


async def test_a_tick_with_nothing_planned_neither_extends_nor_breaks_a_stall() -> None:
    """At 2 % the attempted ticks are 2-3 ticks apart: the empty ticks between them must not reset the run of
    all-rejected ticks (else the Stall plaque, `>= 3`, could never appear at the slow step) nor count as one."""

    runner, run = _runner(), _run(2)
    assert await _execute(runner, run, []) == 0  # nothing planned: not a stall
    assert await _execute(runner, run, [_scripted_rejection(0)]) == 1
    assert await _execute(runner, run, []) == 1  # held, not reset
    assert await _execute(runner, run, []) == 1
    assert await _execute(runner, run, [_scripted_rejection(0)]) == 2
    assert await _execute(runner, run, [_scripted_rejection(0), _scripted_rejection(1)]) == 3


async def test_real_outcomes_still_drive_the_stall_counter_as_before() -> None:
    """Counter-check: an all-rejected attempted tick still counts, and an attempted tick with an error still resets."""

    runner, run = _runner(), _run(100)
    assert await _execute(runner, run, [_scripted_rejection(0)]) == 1
    assert await _execute(runner, run, [_scripted_rejection(0)]) == 2
    assert await _execute(runner, run, [_unknown_sender_error(0)]) == 0


async def test_restart_starts_the_stall_run_from_zero(clock) -> None:  # noqa: F811
    """A restarted launch at the slow step plans nothing on its first ticks, which no longer clear the counter: the
    restart itself does (`run_lifecycle.restart`), or the old launch's Stall plaque would greet the new one."""

    run = await _started(clock, "anon:p049-s2a-restart")
    run._real_consec_all_rejected_ticks = 4
    await runtime.stop(run.run_id)
    await runtime.restart(run.run_id)
    assert run._real_consec_all_rejected_ticks == 0
    assert run.tick_index == 0  # the derived carry starts from zero with it


async def test_a_paused_run_accrues_no_budget(clock) -> None:  # noqa: F811
    """The carry is a function of `tick_index`; the heartbeat does not advance it while the run is paused, so a resume
    cannot release ticks of budget saved during the pause."""

    run = await _started(clock, "anon:p049-s2a-pause")
    await runtime.pause(run.run_id)
    before = run.tick_index
    assert (await clock.progress(run))[0] == 0 and run.tick_index == before
    await runtime.resume(run.run_id)
    assert (await clock.progress(run))[0] == 1  # control: the same stand does see a running run tick
