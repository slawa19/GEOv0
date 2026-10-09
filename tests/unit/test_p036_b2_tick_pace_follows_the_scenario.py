"""036 B2: the heartbeat's tick period follows `settings.playback.tick_seconds` of the run's scenario.

TARGET (spec 036, "Темп"): the real duration of a tick is `settings.playback.tick_seconds` (default 1.0 = the behaviour
before B2), applied in `_SimulatorRuntimeBase._heartbeat_loop` instead of the constant `asyncio.sleep(1.0)`. It is a LOWER
bound of the period, not a frequency: the period between two tick starts is `tick_seconds` plus the cost of the tick. The pace
changes nothing else: the tick numbers and the sim time advance by one tick per iteration exactly as before, so neither the
money phase nor the sim clock is skipped or doubled when the pace differs; a paused run does not tick and resumes where it
was; a run stopped while paused ends the loop.

HOW IT IS MEASURED, AND WHY NOT WITH `sleep`. A virtual clock. The module's `asyncio.sleep` is replaced by a fake that advances
the clock by the requested duration (and yields once), and the runner's `tick_real_mode` by a fake that records the clock at
its start and advances it by a fixed cost. The period is the difference of two recorded starts: no wall-clock time is spent
and no throughput is claimed.

The loop is the PRODUCTION `runtime._heartbeat_loop` of the process-wide runtime. The run carries the scenario (`_scenario_raw`,
the deep copy a run takes at creation) and the same scenario is registered, so the test does not pin which of the two the
implementation reads.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

import app.core.simulator.runtime_impl as runtime_impl
import app.core.simulator.storage as simulator_storage
from app.core.simulator.models import RunRecord
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import scenario_to_record

TICK_COST_S = 0.3
TICKS = 4
SCENARIO_ID = "p036-b2-pace"


class _VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.requested_sleeps: list[float] = []
        self.tick_starts: list[float] = []
        self.guard = 0


def _scenario(playback: dict | None) -> dict:
    raw = {
        "schema_version": "scenario/1",
        "scenario_id": SCENARIO_ID,
        "participants": [{"id": "A", "type": "person"}, {"id": "B", "type": "person"}],
        "trustlines": [{"from": "A", "to": "B", "equivalent": "UAH", "limit": "10"}],
        "equivalents": ["UAH"],
        "events": [],
    }
    if playback is not None:
        raw["settings"] = {"playback": playback}
    return raw


def _run_over(raw: dict) -> RunRecord:
    run = RunRecord(run_id="p036-b2-run", scenario_id=SCENARIO_ID, mode="real", state="running")
    run._scenario_raw = deepcopy(raw)
    return run


def _install_clock(monkeypatch, run: RunRecord, raw: dict, *, on_sleep=None, stop_after_ticks: int | None = TICKS) -> _VirtualClock:
    clock = _VirtualClock()
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *_a, **_kw):
        clock.guard += 1
        assert clock.guard < 60, "the heartbeat loop did not stop"  # a broken stand fails, it does not hang
        clock.requested_sleeps.append(float(delay))
        clock.now += float(delay)
        if on_sleep is not None:
            on_sleep(clock, run)
        await real_sleep(0)

    class _AsyncioWithVirtualSleep:
        sleep = staticmethod(fake_sleep)

        def __getattr__(self, name):  # CancelledError, create_task, ... stay the real ones
            return getattr(asyncio, name)

    async def fake_tick(_run_id: str) -> None:
        clock.tick_starts.append(clock.now)
        clock.now += TICK_COST_S
        if stop_after_ticks is not None and len(clock.tick_starts) >= stop_after_ticks:
            run.state = "stopped"  # the loop's own exit condition, read after the next sleep

    async def no_upsert(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(runtime_impl, "asyncio", _AsyncioWithVirtualSleep())
    monkeypatch.setattr(runtime._real_runner, "tick_real_mode", fake_tick)
    monkeypatch.setattr(simulator_storage, "upsert_run", no_upsert)
    monkeypatch.setitem(runtime._runs, run.run_id, run)
    monkeypatch.setitem(runtime._scenarios, SCENARIO_ID, scenario_to_record(deepcopy(raw), source_path=None, created_at=None))
    return clock


def _periods(clock: _VirtualClock) -> list[float]:
    return [round(b - a, 6) for a, b in zip(clock.tick_starts, clock.tick_starts[1:])]


@pytest.mark.asyncio
async def test_a_scenario_without_playback_keeps_the_one_second_period_plus_the_cost(monkeypatch) -> None:
    """CONTROL (green on 7df35fcf): the default is the behaviour before B2, 1.0 s + the cost of the tick."""

    raw = _scenario(None)
    run = _run_over(raw)
    clock = _install_clock(monkeypatch, run, raw)

    await runtime._heartbeat_loop(run.run_id)

    assert len(clock.tick_starts) == TICKS, clock.tick_starts  # non-vacuity: the loop really ticked
    assert _periods(clock) == pytest.approx([1.0 + TICK_COST_S] * (TICKS - 1)), _periods(clock)


@pytest.mark.asyncio
@pytest.mark.parametrize("tick_seconds", [0.25, 2.0, 5.0])
async def test_the_tick_period_is_tick_seconds_of_the_scenario_plus_the_cost(monkeypatch, tick_seconds: float) -> None:
    """TARGET (red on 7df35fcf): 0.25 s + cost, 2.0 s + cost, 5.0 s + cost - and not one tick lost or doubled."""

    raw = _scenario({"tick_seconds": tick_seconds})
    run = _run_over(raw)
    clock = _install_clock(monkeypatch, run, raw)

    await runtime._heartbeat_loop(run.run_id)

    assert len(clock.tick_starts) == TICKS, clock.tick_starts  # control: the loop ticked, the measurement is not empty
    expected = [tick_seconds + TICK_COST_S] * (TICKS - 1)
    assert _periods(clock) == pytest.approx(expected), (
        f"tick_seconds={tick_seconds}: the period between tick starts is {_periods(clock)} (requested sleeps "
        f"{clock.requested_sleeps}), expected {expected} = tick_seconds + the {TICK_COST_S} s cost of a tick"
    )
    # the pace is not the sim clock: one tick number and one tick of sim time per iteration, whatever the pace
    assert run.tick_index == TICKS and run.sim_time_ms == TICKS * runtime._tick_ms_base, (run.tick_index, run.sim_time_ms)


@pytest.mark.asyncio
async def test_a_paused_run_does_not_tick_resumes_where_it_was_and_a_run_stopped_while_paused_ends_the_loop(monkeypatch) -> None:
    """CONTROL (green on 7df35fcf): `pause` stops the sim time, `resume` continues from the same tick (spec 'Что уже есть'),
    and `stop` while paused ends the loop - checked on the same run, in this order."""

    raw = _scenario({"tick_seconds": 0.5})
    run = _run_over(raw)
    seen = SimpleNamespace(at_pause=None, during_pause=[], after_resume=None)

    def on_sleep(clock: _VirtualClock, run: RunRecord) -> None:
        sleeps = len(clock.requested_sleeps)
        if sleeps == 3:  # after two ticks the operator pauses
            run.state = "paused"
            seen.at_pause = (run.tick_index, run.sim_time_ms)
        elif sleeps in (4, 5, 6):
            seen.during_pause.append((run.tick_index, run.sim_time_ms))
        elif sleeps == 7:
            run.state = "running"
        elif sleeps == 10:  # three more ticks later (sleeps 7-9) the operator pauses again, and then stops the paused run
            run.state = "paused"
        elif sleeps == 12:
            run.state = "stopping"

    clock = _install_clock(monkeypatch, run, raw, on_sleep=on_sleep, stop_after_ticks=None)

    await runtime._heartbeat_loop(run.run_id)

    assert seen.at_pause == (2, 2 * runtime._tick_ms_base), seen  # non-vacuity: it paused after two ticks
    assert seen.during_pause == [seen.at_pause] * 3, seen  # the pause is observable and the clock stood still
    assert len(clock.tick_starts) == 5, clock.tick_starts  # resumed from tick 2: ticks 3, 4 and 5, none lost or doubled
    assert run.tick_index == 5 and run.sim_time_ms == 5 * runtime._tick_ms_base
    assert run.state == "stopping" and clock.guard == 12  # the loop ended at the first sleep that saw the stop


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tick_seconds",
    [0, -1, 0.1, 0.24, 5.01, 60, True, "2", None],
    ids=["zero", "negative", "0.1", "0.24", "5.01", "60", "bool", "string", "null"],
)
async def test_a_tick_seconds_outside_the_schema_range_is_not_trusted_and_the_default_pace_stays(monkeypatch, tick_seconds) -> None:
    """Fix-delta guard (the range 0.25-5 of the schema is enforced where the loop reads it, not only at creation): a stored
    scenario's value outside it, or not a number, gives the 1.0 s default - and the two edges (0.25, 5.0) above are accepted,
    so the range is neither widened (`> 0`) nor shut."""

    raw = _scenario({"tick_seconds": tick_seconds})
    run = _run_over(raw)
    clock = _install_clock(monkeypatch, run, raw)

    await runtime._heartbeat_loop(run.run_id)

    assert len(clock.tick_starts) == TICKS, clock.tick_starts
    assert _periods(clock) == pytest.approx([1.0 + TICK_COST_S] * (TICKS - 1)), (tick_seconds, _periods(clock))
