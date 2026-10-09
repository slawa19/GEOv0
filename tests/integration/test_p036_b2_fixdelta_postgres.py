"""036 B2 fix-delta (two reviews of `7c11ee0f`): what the reviews executed and the first pass did not guard.

Each TARGET below is RED on `7c11ee0f`; a CONTROL is the same stand with the input in its good spelling.

1. `episode_progress[].attempts` counts REAL attempts. The writer was reached twice per money phase (the commit's
   confirmation and the tail's `_commit_and_resolve` both call `apply_deferred_effects`), so one payment, one call of the
   core, reported 2. One increment per attempt; an attempt whose outcome is not established and the one that lands after
   it are two.
2. A skipped inject writes ONE note to the events artifact, and it names the reason; it was two (the direct one and the
   progress writer's "scripted inject refused").
4. A pause publishes `run_status` once. Measured on the PRODUCT `_heartbeat_loop`: the runner published, then the loop
   published unconditionally after the tick.
(5, `pause_after` is a boolean on every event, is `tests/unit/test_p036_b2_pause_after_is_a_boolean_on_every_event.py`.)

The product heartbeat is driven with a virtual `asyncio.sleep` (no wall-clock time): see `_heartbeat`.
"""

from __future__ import annotations

import asyncio

import pytest

import app.core.simulator.runtime_impl as runtime_impl
import app.core.simulator.storage as simulator_storage
from app.core.simulator.runtime import runtime
from app.core.simulator.runtime_utils import run_to_status
from app.utils.exceptions import TimeoutException
from tests.integration.test_p034_s1_restart_repeats_the_idempotency_key_postgres import _restart
from tests.integration.test_p036_b1_scripted_events_postgres import (  # noqa: F401 - `factory` is a fixture
    PAY_LINES,
    _payment_event,
    _stand,
    factory,
    ticks,
)
from tests.p021_support import require_target

CAPTION = {"ru": "р", "en": "e"}


def _progress(run) -> list:
    return run_to_status(run).episode_progress or []


def _attempts(run) -> list[tuple[int, str, int | None]]:
    return [(i.index, i.status, i.attempts) for i in _progress(run)]


# ------------------------------------------------------------------------------------------- 1. attempts of a payment


class _CoreCalls:
    """Counts the real calls of the core by the scripted payments (one per attempt that reached it)."""

    def __init__(self, monkeypatch, *, fail_first: bool = False) -> None:
        from app.core.payments.service import PaymentService

        self.n = 0
        original = PaymentService.create_payment_internal_staged

        async def counting(self_, *args, **kwargs):
            self.n += 1
            if fail_first and self.n == 1:
                raise TimeoutException("p036 b2 fix-delta: routing timed out")
            return await original(self_, *args, **kwargs)

        monkeypatch.setattr(PaymentService, "create_payment_internal_staged", counting)


@pytest.mark.asyncio
async def test_a_payment_that_lands_on_the_first_attempt_reports_one_attempt(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    core = _CoreCalls(monkeypatch)

    await ticks(runner, run, 1)

    assert core.n == 1, core.n  # control: ONE call of the core
    require_target(_attempts(run) == [(0, "done", 1)], f"one payment, {core.n} call of the core, progress (index, status, attempts) {_attempts(run)}")


@pytest.mark.asyncio
async def test_a_payment_the_core_refuses_reports_one_attempt(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["C"], "5.00")])

    await ticks(runner, run, 1)

    failed = [e for e in runner._sse.events if e.get("type") == "tx.failed"]
    assert len(failed) == 1  # control: refused once
    require_target(_attempts(run) == [(0, "refused", 1)], f"progress (index, status, attempts) {_attempts(run)}")


@pytest.mark.asyncio
async def test_an_attempt_whose_outcome_is_not_established_and_the_one_that_lands_after_it_are_two(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    core = _CoreCalls(monkeypatch, fail_first=True)

    await ticks(runner, run, 1)
    first = _attempts(run)
    run.state, run.errors_total, run.last_error = "running", 0, None
    await ticks(runner, run, 1)

    assert core.n == 2, core.n  # control: two calls
    require_target(first == [(0, "incomplete", 1)] and _attempts(run) == [(0, "done", 2)], f"after the first tick {first}, after the second {_attempts(run)}")


@pytest.mark.asyncio
async def test_after_a_restart_the_payment_is_a_new_launch_with_its_own_first_attempt(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])

    await ticks(runner, run, 1)
    await _restart(run, runner._lock)
    await ticks(runner, run, 1)

    items = _progress(run)
    assert run._launch_epoch == 1 and len(items) == 1 and items[0].epoch == 1, (run._launch_epoch, items)  # control
    require_target(items[0].attempts == 1, f"attempts {items[0].attempts} for the first attempt of epoch 1")


# ----------------------------------------------------------------------------------------- 2. one note per skipped inject


def _freeze(pid: str) -> dict:
    return {"time": 0, "type": "inject", "effects": [{"op": "freeze_participant", "participant_id": pid}]}


async def _notes_of_inject(factory, monkeypatch, *, process_flag: bool, scenario_flag):  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_freeze(q["B"].pid)])
    runner._real_enable_inject = process_flag
    if scenario_flag is not None:
        run._scenario_raw = None
        runner._get_scenario_raw("")["settings"]["playback"] = {"inject_enabled": scenario_flag}
    artifacts: list[dict] = []
    monkeypatch.setattr(runner._inject_executor._artifacts, "enqueue_event_artifact", lambda _run_id, payload: artifacts.append(payload))

    await ticks(runner, run, 1)

    return [a["scenario"] for a in artifacts if a.get("type") == "note" and a["scenario"]["event_index"] == 0]


@pytest.mark.asyncio
async def test_control_an_applied_inject_writes_its_note(factory, monkeypatch) -> None:  # noqa: F811
    """Anti-vacuum: the recorder does see the notes of an inject (here the applied one), so 'one note' below is a count."""

    notes = await _notes_of_inject(factory, monkeypatch, process_flag=True, scenario_flag=True)
    assert len(notes) >= 1, notes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("process_flag", "scenario_flag", "words"),
    [(False, None, "SIMULATOR_REAL_ENABLE_INJECT"), (True, False, "inject_enabled"), (False, True, "SIMULATOR_REAL_ENABLE_INJECT")],
    ids=["process-flag-off", "scenario-says-false", "scenario-true-under-process-off"],
)
async def test_a_skipped_inject_writes_one_note_and_it_names_the_reason(factory, monkeypatch, process_flag, scenario_flag, words) -> None:  # noqa: F811
    notes = await _notes_of_inject(factory, monkeypatch, process_flag=process_flag, scenario_flag=scenario_flag)

    require_target(
        len(notes) == 1 and words in notes[0]["description"] and not notes[0]["description"].startswith("scripted"),
        f"notes of event 0: {[n['description'] for n in notes]}",
    )


# --------------------------------------------------------------------------------- 4. a pause publishes the status once


def _heartbeat(monkeypatch, runner, run):
    """The PRODUCT `runtime._heartbeat_loop` over the stand's real runner and run, with a virtual `asyncio.sleep`.

    Returns the statuses published, `(state, sim_time_ms)`, by the loop AND by the runner. The loop ends when the run is no
    longer `running` at its next wake-up (a paused run does not tick, so the loop would sleep for ever)."""

    published: list[tuple[str, int]] = []
    real_sleep = asyncio.sleep
    sleeps = {"n": 0}

    async def fake_sleep(_delay, *_a, **_kw):
        sleeps["n"] += 1
        if run.state != "running" or sleeps["n"] > 10:
            run.state = "stopped"  # the loop's own exit; a broken stand ends here instead of hanging
        await real_sleep(0)

    class _AsyncioWithVirtualSleep:
        sleep = staticmethod(fake_sleep)

        def __getattr__(self, name):
            return getattr(asyncio, name)

    async def no_upsert(*_a, **_kw) -> None:
        return None

    def record(_run_id: str) -> None:
        published.append((str(run.state), int(run.sim_time_ms)))

    monkeypatch.setattr(runtime_impl, "asyncio", _AsyncioWithVirtualSleep())
    monkeypatch.setattr(simulator_storage, "upsert_run", no_upsert)
    monkeypatch.setattr(runtime, "_real_runner", runner)
    monkeypatch.setattr(runtime, "publish_run_status", record)
    runner._publish_run_status = record
    monkeypatch.setitem(runtime._runs, run.run_id, run)
    return published


@pytest.mark.asyncio
async def test_a_pause_publishes_run_status_once_on_the_product_heartbeat(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B"], PAY_LINES, [], [{"time": 500, "type": "note", "caption": CAPTION, "pause_after": True}])
    run.sim_time_ms = 0
    published = _heartbeat(monkeypatch, runner, run)

    await asyncio.wait_for(runtime._heartbeat_loop(run.run_id), timeout=60)

    assert run._real_fired_scenario_event_indexes == {0} and run.tick_index == 1, (run._real_fired_scenario_event_indexes, run.tick_index)  # control
    require_target(published == [("paused", 1000)], f"statuses published (state, sim_time_ms): {published}")


@pytest.mark.asyncio
async def test_control_a_tick_without_a_pause_publishes_the_status_once_too(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B"], PAY_LINES, [], [{"time": 500, "type": "note", "caption": CAPTION}])
    run.sim_time_ms = 0
    published = _heartbeat(monkeypatch, runner, run)
    ticks_seen = {"n": 0}
    original = runner.tick_real_mode

    async def then_stop(run_id):
        await original(run_id)
        ticks_seen["n"] += 1
        if ticks_seen["n"] == 2:
            run.state = "stopping"

    monkeypatch.setattr(runner, "tick_real_mode", then_stop)

    await asyncio.wait_for(runtime._heartbeat_loop(run.run_id), timeout=60)

    assert [s for s, _t in published] == ["running", "stopping"] and ticks_seen["n"] == 2, (published, ticks_seen)
