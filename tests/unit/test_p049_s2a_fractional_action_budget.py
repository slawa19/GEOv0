"""049 S2a (`T4915`, decision 10): the tick's action budget is fractional, carried between ticks.

THE DEFECT (red on `5d0779a8`). `RealPaymentPlanner.plan_payments` sized a tick as
`max(1, int(ACTIONS_PER_TICK_MAX * intensity))`: any intensity above zero planned at least one payment per 1 s tick,
so the visitor's slow step «2 %» (one payment every 2.5 s) planned ten payments in ten ticks instead of four.

THE RULE. Tick `t` (the run's `tick_index`; a created run is at 0 and the heartbeat's first tick is 1) at rate
`r_t = ACTIONS_PER_TICK_MAX * intensity_t` (the warm-up ramp included) plans `floor(B(t)) - floor(B(t - 1))`,
`B(t) = r_0 + ... + r_t`. This is the accumulator `carry += r_t; actions = floor(carry); carry -= actions` started at 0
at tick 0, with its carry derived from the tick number and the current percent instead of stored:
a replay of the same tick's money phase (`money_replay.py`) with unchanged inputs plans the same count, a paused run does not advance
`tick_index`, and a restart sets it to 0.

The planner is driven with the configured `SIMULATOR_ACTIONS_PER_TICK_MAX` (20) on a mesh where every planning slot
can be filled, so the planned count IS the budget (anti-vacuum: 15 % must fill 30 of 30). The launch's opening window
(ticks 1..5) plans at least one action per tick at any intensity above zero (the section at the end of the module).
"""

from __future__ import annotations

import logging
import math
from fractions import Fraction
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


def _mesh(n: int = 5, warmup: dict[str, Any] | None = None) -> dict[str, Any]:
    pids = [f"P{i}" for i in range(n)]
    scenario: dict[str, Any] = {
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
    if warmup is not None:
        scenario["settings"] = {"warmup": warmup}
    return scenario


def _run(intensity_percent: int) -> RunRecord:
    run = RunRecord(run_id="r", scenario_id="s", mode="real", state="running")
    run.seed = 4242
    run.intensity_percent = intensity_percent
    return run


def _planned_per_tick(
    runner: RealRunnerImpl, run: RunRecord, ticks: range, warmup: dict[str, Any] | None = None
) -> list[int]:
    scenario = _mesh(warmup=warmup)
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
    """TARGET (049 (з)): 2 % plans 4 actions in 10 ticks (was 10), 5 % — 10, 15 % — 30, 0 % — 0, in any ten ticks past
    the launch's opening window (`LAUNCH_OPENING_TICKS`, ticks 1..5, which plan at least one each)."""

    runner = _runner()
    for first_tick in (6, 17, 123):  # any ten consecutive ticks past the opening window
        counts = _planned_per_tick(runner, _run(intensity_percent), range(first_tick, first_tick + 10))
        assert sum(counts) == expected_in_ten_ticks, (intensity_percent, first_tick, counts)


def test_at_two_percent_a_payment_comes_every_two_or_three_ticks() -> None:
    counts = _planned_per_tick(_runner(), _run(2), range(6, 46))  # past the opening window (an action each)
    assert set(counts) <= {0, 1}, counts
    ticks_with_action = [t for t, n in zip(range(6, 46), counts) if n]
    gaps = {b - a for a, b in zip(ticks_with_action, ticks_with_action[1:])}
    assert gaps == {2, 3}, (ticks_with_action, gaps)


def test_a_zero_action_tick_is_an_ordinary_empty_plan() -> None:
    """At 2 % most ticks plan nothing: the planner returns an empty list, it does not raise."""

    counts = _planned_per_tick(_runner(), _run(2), range(15, 17))  # ticks 15 and 16: carry 0.0 -> 0.4 -> 0.8
    assert counts == [0, 0]


def test_a_replanned_tick_spends_its_budget_once() -> None:
    """Invariant (green on the old formula too): the money phase's bounded replay (`money_replay.py`) replans the SAME
    tick, so the count must not move - a stored carry advanced inside the planner would break this."""

    runner, run, scenario = _runner(), _run(2), _mesh()
    for t in range(1, 11):
        run.tick_index = t
        first = runner._plan_real_payments(run, scenario)
        assert runner._plan_real_payments(run, scenario) == first


def test_the_slow_step_from_a_runs_first_tick() -> None:
    """The carry starts at 0 at tick 0 (never run by the heartbeat); the opening window (ticks 1..5,
    `LAUNCH_OPENING_TICKS`) plans one action on each tick, and from tick 6 the budget's 2-3 tick pattern resumes."""

    assert _planned_per_tick(_runner(), _run(2), range(1, 16)) == [1] * 5 + [0, 1, 0, 1, 0, 0, 1, 0, 1, 0]


@pytest.mark.parametrize(("before", "after"), [(15, 2), (2, 15), (100, 2), (2, 5), (5, 2), (15, 0)])
def test_an_intensity_change_never_bursts_above_the_new_rate(before: int, after: int) -> None:
    """Counter-check (green on the old formula too, `max(1, int(r)) <= ceil(r)`): the ticks after a change plan at most
    `ceil(20 * after / 100)` - no budget saved at the old rate is released as a burst."""

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


WARMUP = {"ticks": 50, "floor": 0.1}  # the warm-up of the visitor presets greenfield-village-100 and riverside-town-50


def _accumulator(intensity_percent: int, ticks: int, warmup: dict[str, Any] | None) -> list[int]:
    """The rule of decision 10 as written - a STORED carry from 0 at tick 0, `carry += r_t; n = floor(carry);
    carry -= n` - with `r_t` = 20 x percent x the warm-up ramp of tick t (`floor + (1 - floor) * t / ticks` while
    `t < ticks`). Returns ticks 1..`ticks` (tick 0 is never run by the heartbeat)."""

    carry, out = Fraction(0), []
    for t in range(0, ticks + 1):
        ramp = Fraction(1)
        if warmup and t < warmup["ticks"]:
            floor_ = Fraction(str(warmup["floor"]))
            ramp = floor_ + (1 - floor_) * Fraction(t, warmup["ticks"])
        carry += ACTIONS_PER_TICK_MAX * Fraction(intensity_percent, 100) * ramp
        n = math.floor(carry)
        carry -= n
        if t >= 1:
            out.append(n)
    return out


@pytest.mark.parametrize("warmup", [None, WARMUP], ids=["no-warmup", "warmup-50"])
@pytest.mark.parametrize("intensity_percent", [0, 2, 5, 7, 15, 30])
def test_every_tick_plans_what_the_stored_accumulator_would(intensity_percent: int, warmup: dict | None) -> None:
    """The derived carry equals the stored one tick by tick, warm-up ramp included (a carry derived per tick from the
    current RATE alone would drift from it while the ramp changes the rate every tick)."""

    planned = _planned_per_tick(_runner(), _run(intensity_percent), range(1, 121), warmup)
    budget = _accumulator(intensity_percent, 120, warmup)
    # The opening window (ticks 1..5) plans at least one at an intensity above zero; from tick 6 it is the budget.
    assert planned[:5] == [max(1, n) if intensity_percent > 0 else 0 for n in budget[:5]]
    assert planned[5:] == budget[5:]


def test_the_normal_step_during_the_presets_warm_up() -> None:
    """5 % on a preset with the 50-tick ramp: the budget's first payment comes on tick 6 and it plans 28 in the ramp's
    first 50 ticks; the opening window (ticks 1..5, all with share 0) adds one on each, so 33; no gap over 4 ticks."""

    planned = _planned_per_tick(_runner(), _run(5), range(1, 51), WARMUP)
    budget = _accumulator(5, 50, WARMUP)
    assert [t for t, n in zip(range(1, 51), budget) if n][0] == 6 and sum(budget) == 28, budget
    with_action = [t for t, n in zip(range(1, 51), planned) if n]
    assert with_action[:6] == [1, 2, 3, 4, 5, 6] and sum(planned) == 28 + 5, planned
    assert max(b - a for a, b in zip(with_action, with_action[1:])) <= 4, with_action


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


async def test_at_intensity_zero_an_empty_tick_clears_the_stall_as_before() -> None:
    """Nothing is attempted at 0 %: the Stall plaque of the tempo before must not stay on screen for ever."""

    runner, run = _runner(), _run(2)
    for _ in range(3):
        await _execute(runner, run, [_scripted_rejection(0)])
    assert run._real_consec_all_rejected_ticks == 3
    run.intensity_percent = 0
    assert await _execute(runner, run, []) == 0


async def test_real_outcomes_still_drive_the_stall_counter_as_before() -> None:
    """Counter-check: an all-rejected attempted tick still counts, and an attempted tick with an error still resets."""

    runner, run = _runner(), _run(100)
    assert await _execute(runner, run, [_scripted_rejection(0)]) == 1
    assert await _execute(runner, run, [_scripted_rejection(0)]) == 2
    assert await _execute(runner, run, [_unknown_sender_error(0)]) == 0


async def test_restart_starts_the_stall_run_from_zero(clock) -> None:  # noqa: F811
    """Empty ticks no longer clear the counter, so the restart itself does (`run_lifecycle.restart`), or the old
    launch's Stall plaque and failure count would greet the new one."""

    run = await _started(clock, "anon:p049-s2a-restart")
    run._real_consec_all_rejected_ticks = 4
    run._real_consec_tick_failures = 3  # the run stopped by `REAL_MODE_TICK_FAILED_REPEATED`
    await runtime.stop(run.run_id)
    await runtime.restart(run.run_id)
    assert run._real_consec_all_rejected_ticks == 0
    assert run._real_consec_tick_failures == 0  # a single failure of the new launch must not stop it again
    assert run.tick_index == 0  # the derived carry starts from zero with it


async def test_a_paused_run_accrues_no_budget(clock) -> None:  # noqa: F811
    """The premise the derived carry stands on (the tick itself is a no-op on this stand): the heartbeat does not advance
    `tick_index` while the run is paused, so a resume cannot release budget saved during the pause."""

    run = await _started(clock, "anon:p049-s2a-pause")
    await runtime.pause(run.run_id)
    before = run.tick_index
    assert (await clock.progress(run))[0] == 0 and run.tick_index == before
    await runtime.resume(run.run_id)
    assert (await clock.progress(run))[0] == 1  # control: the same stand does see a running run tick


# ── the consecutive tick-failure counter (049 S2a, BACKLOG 049-2 brought into T4915) ────────────────────────────────
#
# Target: three consecutive ATTEMPTED failing ticks stop the run at any tempo. An empty tick while the run plans payments
# (intensity above 0) neither counts as a failure nor clears the run of failures; an attempted tick whose money phase
# completed clears it. The stand drives the REAL `RealTick.tick` and its REAL `run_payments_phase` and executor; only
# the planner (a script per tick), the database session and the tail after the money phase are doubles.

from app.core.simulator import tick as tick_module  # noqa: E402
from app.core.simulator.real_payments_executor import RealPaymentsExecutor  # noqa: E402
from app.core.simulator.tick import RealTick  # noqa: E402
from tests.unit.test_tick_money_phase_resolution import (  # noqa: E402
    _Runner,
    _run_with_phase,
    _bind_session,
    _SuccessfulRollbackSession,
)


class _PlanFailure(RuntimeError):
    """A programmatic failure inside an attempted money phase (the class `PaymentTransactionUnusable` and
    `MoneyCommitOutcomeUnknown` take at the tick: out of the money phase, before the executor's end)."""


class _Sse:
    def __init__(self) -> None:
        self.n = 0

    def next_event_id(self, _run: Any) -> str:
        self.n += 1
        return f"evt_{self.n}"

    def broadcast(self, _run_id: str, _payload: Any) -> None:
        return None


class _ScriptedRunner(_Runner):
    """`plan` per tick: "fail" raises inside the money phase, "empty" plans nothing, "ok" plans one action that the
    real executor runs to its end (a scripted payment of an unknown sender: refused without the database), "landed"
    is "ok" whose money attempt then raises AFTER the executor ran. That is the accounting position of a commit that
    landed and still failed the tick (`money_replay.py`, the confirmed-commit and unresolved-outcome branches: the
    executor has run, `money` stays None, the failure counts) - the stand raises from the attempt, it does not run
    those branches themselves."""

    def __init__(self, *, run: RunRecord, logger: logging.Logger, script: list[str]) -> None:
        _, phase, _, _ = _run_with_phase(logger=logger, run_id=run.run_id)
        super().__init__(run=run, phase=phase, logger=logger)
        self._script = list(script)
        self.raise_after_payments = False
        self._real_max_consec_tick_failures_limit = 3
        self._real_max_errors_total_limit = 0
        self._real_payments_executor = RealPaymentsExecutor(
            lock=self._lock,
            sse=_Sse(),  # type: ignore[arg-type]
            utc_now=self._utc_now,
            logger=logger,
            edge_patch_builder=None,  # type: ignore[arg-type]
            should_warn_this_tick=lambda *_a, **_k: False,
            sim_idempotency_key=lambda **_k: "k",
        )

    def _plan_real_payments(self, *args, **kwargs) -> list:
        step = self._script.pop(0)
        if step == "fail":
            raise _PlanFailure("the money phase failed")
        self.raise_after_payments = step == "landed"
        return [] if step == "empty" else [_scripted_rejection(0)]

    def scripted_payments_due(self, run: Any, scenario: Any, *, first_seq: int) -> tuple[list, dict, int]:
        return [], {}, 0

    def mark_scripted_events_fired(self, *args, **kwargs) -> None:
        return None


async def _no_tail(**_kwargs: Any) -> None:
    return None


async def _drive(
    monkeypatch, intensity_percent: int, script: list[str], *, tail_fails: bool = False
) -> tuple[RunRecord, _ScriptedRunner, list[int]]:
    logger = logging.getLogger("test.p049_s2a_tick_failures")
    run, _phase, _emitter, _effect = _run_with_phase(logger=logger, run_id=f"p049-s2a-{intensity_percent}")
    run.intensity_percent = intensity_percent
    runner = _ScriptedRunner(run=run, logger=logger, script=script)
    _bind_session(monkeypatch, _SuccessfulRollbackSession())
    tick = RealTick(runner)  # type: ignore[arg-type]

    async def _no_scripted_clearings(**_kwargs: Any) -> tuple[dict, set]:
        return {}, set()

    async def _clearing(**_kwargs: Any) -> dict:
        if tail_fails:
            raise _PlanFailure("the tail failed")
        return {}

    real_phase = tick.run_payments_phase

    async def _phase(**kwargs: Any):
        res = await real_phase(**kwargs)
        if runner.raise_after_payments:
            raise _PlanFailure("the tick failed after its payments ran")
        return res

    tick.run_payments_phase = _phase  # type: ignore[method-assign]
    tick.run_scripted_clearings = _no_scripted_clearings  # type: ignore[method-assign]
    tick.maybe_run_clearing = _clearing  # type: ignore[method-assign]
    for name in ("apply_trust_decay_and_broadcast", "drop_closed_trustlines", "populate_per_eq_metric_values",
                 "persist_tick_tail"):
        setattr(tick, name, _no_tail)
    counters = []
    for t in range(1, len(script) + 1):
        if runner.failures:
            break
        run.tick_index = t
        await tick.tick(run.run_id)
        counters.append(int(run._real_consec_tick_failures))
    return run, runner, counters


def _stops(runner: _ScriptedRunner) -> list[str]:
    return [code for code, _ in runner.failures]


async def test_at_the_slow_step_three_failing_attempted_ticks_stop_the_run_across_empty_ticks(monkeypatch) -> None:
    """TARGET (red on `aa31f6df`): the empty ticks between attempts no longer forgive the failures."""

    _run, runner, counters = await _drive(monkeypatch, 2, ["fail", "empty", "empty", "fail", "empty", "fail"])
    assert counters == [1, 1, 1, 2, 2, 3], counters
    assert _stops(runner) == ["REAL_MODE_TICK_FAILED_REPEATED"]


async def test_an_attempted_tick_that_completes_clears_the_failures_at_the_slow_step(monkeypatch) -> None:
    _run, runner, counters = await _drive(monkeypatch, 2, ["fail", "empty", "fail", "ok", "empty", "fail", "empty"])
    assert counters == [1, 1, 2, 0, 0, 1, 1], counters
    assert _stops(runner) == []


async def test_a_failing_tail_on_an_empty_tick_is_not_an_attempted_failure(monkeypatch) -> None:
    """Guard against the gate-only alternative (green on `aa31f6df` too, where the executor cleared every tick). The
    concern of the first round - tail failures piling up across empty ticks once empty ticks stop clearing the
    counter - does not hold: an empty tick neither counts nor clears, its failing tail included."""

    _run, runner, counters = await _drive(monkeypatch, 2, ["fail", "empty", "empty", "empty", "empty"], tail_fails=True)
    assert counters == [1, 1, 1, 1, 1], counters
    assert _stops(runner) == []


@pytest.mark.parametrize("intensity_percent", [2, 15])
async def test_a_tick_failing_after_its_payments_ran_is_an_attempted_failure(monkeypatch, intensity_percent: int) -> None:
    """TARGET (red on `aa31f6df` at both tempos): the executor cleared the counter as soon as the payments ran, so a
    tick that failed after them - a landed commit whose outcome still failed the tick, a failing tail - always ended
    at 1 and three of them in a row never stopped the run, while the run kept moving money."""

    script = ["landed", "empty", "landed", "landed"] if intensity_percent == 2 else ["landed", "landed", "landed"]
    _run, runner, counters = await _drive(monkeypatch, intensity_percent, script)
    assert counters == ([1, 1, 2, 3] if intensity_percent == 2 else [1, 2, 3]), counters
    assert _stops(runner) == ["REAL_MODE_TICK_FAILED_REPEATED"]

    _run, runner, counters = await _drive(monkeypatch, intensity_percent, ["ok", "ok", "ok"], tail_fails=True)
    assert counters == [1, 2, 3], counters
    assert _stops(runner) == ["REAL_MODE_TICK_FAILED_REPEATED"]


async def test_the_stall_warning_logs_once_per_fifth_counted_tick(monkeypatch, caplog) -> None:
    """Codex §15 P3: the warning is logged by the tick that counted the fifth all-rejected attempt, not by every empty
    share that holds the counter at five afterwards."""

    with caplog.at_level(logging.WARNING, logger="test.p049_s2a_tick_failures"):
        run, _runner, _ = await _drive(monkeypatch, 2, ["ok"] * 5 + ["empty"] * 4)
    assert run._real_consec_all_rejected_ticks == 5
    assert sum("simulator.real.all_rejected_stall" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.parametrize(
    ("intensity_percent", "script", "expected", "stops"),
    [
        # 15 %: no empty ticks - three failures in a row stop the run, a completed money phase clears.
        (15, ["fail", "ok", "fail", "fail", "fail"], [1, 0, 1, 2, 3], ["REAL_MODE_TICK_FAILED_REPEATED"]),
        # 0 %: nothing is attempted, an empty tick clears as before 049.
        (0, ["fail", "empty", "fail", "fail", "empty"], [1, 0, 1, 2, 0], []),
    ],
    ids=["15-percent", "0-percent"],
)
async def test_where_no_tick_is_an_empty_share_the_counter_behaves_as_before(
    monkeypatch, intensity_percent: int, script: list[str], expected: list[int], stops: list[str]
) -> None:
    _run, runner, counters = await _drive(monkeypatch, intensity_percent, script)
    assert counters == expected, counters
    assert _stops(runner) == stops


# ── the first payment within 15 s: the launch's opening window (049 T4915, after S2a) ─────────────────────────────────
#
# THE DEFECT (red on `51058522`). Before S2a `max(1, int(r_t))` planned an action on every tick at any intensity above
# zero, so a launch's first payment came within 2-4 s of opening; that, not the warm-up, kept spec 043's «a payment
# within 15 s». S2a made the warm-up (`settings.warmup`, 50 ticks from 0.1) really apply at a low tempo: at 5 % the
# budget reaches one action on tick 6, at 2 % on tick 12 - ui-smoke's live step on PR #339 (visitor default 5 %) timed
# out at 15 s. One action on tick 1 was not enough (measured): the plan can come out empty under candidate sampling or
# its payment be rejected (about 27 % of opening attempts), and the next attempt was the budget's again.
#
# THE RULE. The launch's opening window - ticks 1..`LAUNCH_OPENING_TICKS` (5), tick 1 being the first the heartbeat
# runs (a created or restarted run is at 0 and the heartbeat advances it before each tick) - plans `max(1, share)` at
# any intensity above zero; from tick 6 every tick plans exactly the budget's share as before; 0 % plans nothing.

from app.core.simulator.real_payment_planner import LAUNCH_OPENING_TICKS  # noqa: E402


def _launch_counts(intensity_percent: int, warmup: dict[str, Any] | None, ticks: int = 20) -> list[int]:
    return _planned_per_tick(_runner(), _run(intensity_percent), range(1, ticks + 1), warmup)


def test_the_opening_window_is_the_first_five_ticks() -> None:
    assert LAUNCH_OPENING_TICKS == 5


@pytest.mark.parametrize("warmup", [None, WARMUP], ids=["no-warmup", "warmup-50"])
@pytest.mark.parametrize("intensity_percent", [2, 5, 15])
def test_every_tick_of_the_opening_window_plans_a_payment(intensity_percent: int, warmup: dict | None) -> None:
    """TARGET: ticks 1..5 plan at least one at 2 %, 5 % and 15 %, on the presets' warm-up too; tick 6 plans the share."""

    planned = _launch_counts(intensity_percent, warmup, ticks=6)
    budget = _accumulator(intensity_percent, 6, warmup)
    assert all(n >= 1 for n in planned[:5]), (intensity_percent, warmup, planned)
    assert planned[5] == budget[5], (planned[5], budget[5])


@pytest.mark.parametrize("warmup", [None, WARMUP], ids=["no-warmup", "warmup-50"])
def test_at_intensity_zero_the_opening_window_plans_nothing(warmup: dict | None) -> None:
    """Counter-check: the window does not plan at 0 % - the run attempts nothing."""

    assert _launch_counts(0, warmup, ticks=20) == [0] * 20


@pytest.mark.parametrize("warmup", [None, WARMUP], ids=["no-warmup", "warmup-50"])
@pytest.mark.parametrize("intensity_percent", [2, 5, 7, 15, 30])
def test_the_window_adds_exactly_one_action_per_empty_share_and_nothing_after_it(
    intensity_percent: int, warmup: dict | None
) -> None:
    """The extra actions of a launch are exactly the window's ticks whose share is 0 (at most 5): the window plans
    `max(1, share)`, every tick after it the share."""

    planned = _launch_counts(intensity_percent, warmup, ticks=80)
    budget = _accumulator(intensity_percent, 80, warmup)
    assert planned[:5] == [max(1, n) for n in budget[:5]]
    assert planned[5:] == budget[5:]
    extra = sum(planned) - sum(budget)
    assert extra == sum(1 for n in budget[:5] if n == 0) and extra <= LAUNCH_OPENING_TICKS, (extra, budget[:5])


def test_the_presets_steps_get_an_extra_action_on_each_empty_window_tick() -> None:
    """On the presets' warm-up the window's shares are 0 except at 15 % on ticks 2 and 4: 5, 5 and 3 extra actions."""

    for intensity_percent, empty_window_ticks in ((2, 5), (5, 5), (15, 3)):
        budget = _accumulator(intensity_percent, 5, WARMUP)
        assert sum(1 for n in budget if n == 0) == empty_window_ticks, (intensity_percent, budget)
        assert sum(_launch_counts(intensity_percent, WARMUP, ticks=5)) == sum(budget) + empty_window_ticks


def test_a_restarted_launch_has_its_window_again_a_replay_the_same_count_and_a_resume_no_extra() -> None:
    """Restart sets `tick_index` to 0 (`run_lifecycle.restart`, asserted above): the heartbeat's next ticks are 1..5
    again and plan one each; a replay of a window tick plans the same; a run resumed past the window gets no extra."""

    runner, run, scenario = _runner(), _run(5), _mesh(warmup=WARMUP)
    _planned_per_tick(runner, run, range(1, 30), WARMUP)
    run.tick_index = 0  # restart
    for t in range(1, LAUNCH_OPENING_TICKS + 1):
        run.tick_index = t  # the heartbeat's ticks of the new launch
        plan = runner._plan_real_payments(run, scenario)
        assert len(plan) >= 1, t
        assert runner._plan_real_payments(run, scenario) == plan  # replay
    budget = _accumulator(5, 40, WARMUP)
    assert _planned_per_tick(runner, run, range(6, 41), WARMUP) == budget[5:]  # resume-like continuation: no extra


@pytest.mark.parametrize(("before", "after"), [(15, 2), (2, 15), (100, 2), (2, 5), (5, 2), (15, 0), (0, 2)])
def test_an_intensity_change_inside_the_window_stays_within_the_new_rate(before: int, after: int) -> None:
    """With the presets' warm-up, a change on any window tick: that tick and the next ones plan at most
    `ceil(20 * after / 100)` (`max(1, share) <= ceil(r)` for `r > 0`)."""

    runner, run, scenario = _runner(), _run(before), _mesh(warmup=WARMUP)
    ceiling = math.ceil(ACTIONS_PER_TICK_MAX * after / 100)
    for change_at in range(1, LAUNCH_OPENING_TICKS + 1):
        run.intensity_percent = before
        run.tick_index = change_at - 1
        runner._plan_real_payments(run, scenario)
        run.intensity_percent = after
        for t in range(change_at, change_at + 5):
            run.tick_index = t
            assert len(runner._plan_real_payments(run, scenario)) <= ceiling, (before, after, change_at, t)
