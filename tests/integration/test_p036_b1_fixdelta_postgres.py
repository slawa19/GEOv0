"""036 B1 fix-delta (two reviews of `46330763`, class 2, both found the same things): what a scripted event REPORTS and SPENDS
must be what happened.

Each target test is RED on `46330763` and states the target; controls are the same stand with the input in its good
spelling. The stand is `test_p036_b1_scripted_events_postgres.py` (a real tick on a PostgreSQL clone).

1. A scripted `clearing` reported `done` and was spent although its pass did not complete - the equivalent stopped
   (`Equivalent.is_active=False`), the pass raised, the hard timeout fired. The outcome of the pass now comes back and
   only a COMPLETE pass (an empty one included) spends the event; anything else leaves it for the next tick, with a true
   status and reason, logged once per change of status.
2. The periodic clearing of a tick took the cycle of the episode (it ran first). On the tick where a scripted clearing is
   due for an equivalent, that equivalent's scripted pass runs first and no periodic pass of it runs on that tick.
3. The scripted pass's committed volume was not in the tick's `clearing_volume`.
4. A `restart` while a tick was running marked the event of the NEW epoch fired without executing it (and wrote the old
   epoch's clearing progress). Marking and progress are discarded when the run's epoch is no longer the one planned under.
5. A transient failure before admission (the payment timed out, no row exists) spent the event. Only a terminal outcome
   spends it: committed, a logical refusal, a durable ABORTED row.
6. `SENDER_NOT_FOUND` of a scripted payment spent the run's error budget; it is a refusal, not an error.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import pytest
from sqlalchemy import update

import app.core.clearing.runner as clearing_runner
from app.core.payments.service import PaymentService
from app.db.models.equivalent import Equivalent
from app.utils.exceptions import TimeoutException
from tests.integration.test_p034_s1_restart_repeats_the_idempotency_key_postgres import _restart
from tests.integration.test_p036_b1_scripted_events_postgres import (  # noqa: F401 - `factory` is a fixture
    CYCLE_DEBTS,
    CYCLE_LINES,
    PAY_LINES,
    _clearing_event,
    _debts,
    _healthy,
    _payment_event,
    _stand,
    factory,
    ticks,
)
from tests.p021_support import TargetMismatch, require_target  # noqa: F401


def _forget_failures(run) -> None:
    run.state, run.errors_total, run.last_error = "running", 0, None


def _status_log_records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "simulator.real.scripted_event_status" in r.getMessage()]


# --------------------------------------------------------------------------------------- 1. the outcome of the pass


@pytest.mark.asyncio
async def test_a_failed_pass_does_not_spend_the_clearing_event_and_says_so_once_per_status(factory, monkeypatch, caplog) -> None:  # noqa: F811
    """TARGET (red on 46330763): the pass raises twice and then works. The event is not spent while it fails, the
    progress says `incomplete` with the reason, the status is logged once per CHANGE (incomplete, then done), and the
    cycle is cleared by the tick that succeeds."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)])
    original = clearing_runner.run_clearing_pass
    calls = {"n": 0}

    async def failing_twice(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("p036 fixdelta: the pass fails")
        return await original(*args, **kwargs)

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", failing_twice)
    caplog.set_level(logging.INFO)

    await ticks(runner, run, 1)
    _forget_failures(run)
    first = (dict(getattr(run, "_real_story_progress", {}).get(0, {})), 0 in run._real_fired_scenario_event_indexes, len(await _debts(factory, eq, p)))
    await ticks(runner, run, 1)
    _forget_failures(run)
    await ticks(runner, run, 1)

    progress = getattr(run, "_real_story_progress", {}).get(0, {})
    messages = _status_log_records(caplog)
    require_target(
        first[0].get("status") == "incomplete" and first[1] is False and first[2] == 3
        and progress.get("status") == "done" and 0 in run._real_fired_scenario_event_indexes
        and await _debts(factory, eq, p) == {}
        and len(messages) == 2 and "incomplete" in messages[0] and "done" in messages[1],
        f"after the first failing tick: progress {first[0]}, fired {first[1]}, debts {first[2]}; end: progress "
        f"{ {k: v for k, v in progress.items() if k != 'cycles'} }, status log {messages}",
    )


@pytest.mark.asyncio
async def test_a_stopped_equivalent_does_not_spend_the_clearing_event(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)])
    async with factory() as s:
        await s.execute(update(Equivalent).where(Equivalent.id == eq.id).values(is_active=False))
        await s.commit()

    await ticks(runner, run, 1)

    progress = getattr(run, "_real_story_progress", {}).get(0, {})
    assert len(await _debts(factory, eq, p)) == 3  # control: nothing was cleared
    require_target(
        progress.get("status") == "incomplete" and 0 not in run._real_fired_scenario_event_indexes,
        f"a stopped equivalent: progress {progress}, fired {sorted(run._real_fired_scenario_event_indexes)}",
    )
    async with factory() as s:
        await s.execute(update(Equivalent).where(Equivalent.id == eq.id).values(is_active=True))
        await s.commit()
    _forget_failures(run)
    await ticks(runner, run, 1)
    assert await _debts(factory, eq, p) == {}
    assert getattr(run, "_real_story_progress", {}).get(0, {}).get("status") == "done"


@pytest.mark.asyncio
async def test_a_hard_timeout_does_not_spend_the_clearing_event(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)])
    never = asyncio.Event()  # never set: the pass waits until the hard timeout cancels it

    async def stuck(*_a, **_kw):
        await never.wait()

    # THE SEAM: the tick waits for its clearing task with `asyncio.wait_for(task, timeout=hard_timeout)`. In the tick module
    # only, that wait expires at once (the task is cancelled by the tick's own timeout branch, as a real expiry does) - no
    # timer runs, no sleeping. Everything else of `asyncio` is the real one.
    real_asyncio = asyncio

    class _TickAsyncio:
        def __getattr__(self, name):
            return getattr(real_asyncio, name)

        @staticmethod
        async def wait_for(awaitable, timeout=None):
            await real_asyncio.sleep(0)  # let the pass start and block on `never`
            raise real_asyncio.TimeoutError()

    import app.core.simulator.tick as tick_module

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", stuck)
    monkeypatch.setattr(tick_module, "asyncio", _TickAsyncio())

    await ticks(runner, run, 1)

    progress = getattr(run, "_real_story_progress", {}).get(0, {})
    require_target(
        progress.get("status") == "incomplete" and 0 not in run._real_fired_scenario_event_indexes and len(await _debts(factory, eq, p)) == 3,
        f"a hard timeout: progress {progress}, fired {sorted(run._real_fired_scenario_event_indexes)}",
    )


@pytest.mark.asyncio
async def test_control_an_empty_complete_pass_spends_the_clearing_event(factory, monkeypatch) -> None:  # noqa: F811
    """Nothing to clear is a COMPLETE pass: `done` with no cycles, spent."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_clearing_event(e)])

    await ticks(runner, run, 1)

    _healthy(run)
    progress = getattr(run, "_real_story_progress", {}).get(0, {})
    require_target(progress.get("status") == "done" and progress.get("cleared_cycles") == 0
                   and 0 in run._real_fired_scenario_event_indexes, f"progress {progress}")


# ----------------------------------------------------------------------- 2. the periodic clearing does not take it


@pytest.mark.asyncio
async def test_the_scripted_pass_runs_first_and_the_periodic_pass_of_that_equivalent_does_not_run_on_that_tick(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)], clearing_every=1)
    original = clearing_runner.run_clearing_pass
    passes: list[str] = []

    async def spy(session_factory, equivalent, **kwargs):
        passes.append(str(equivalent))
        return await original(session_factory, equivalent, **kwargs)

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", spy)

    await ticks(runner, run, 1)

    _healthy(run)
    progress = getattr(run, "_real_story_progress", {}).get(0, {})
    require_target(
        passes == [eq.code] and progress.get("cleared_cycles") == 1 and runner._sse.published("clearing.done") == 1,
        f"passes {passes}, progress cycles {progress.get('cleared_cycles')}, clearing.done {runner._sse.published('clearing.done')} "
        "(expected ONE pass - the episode's - which took the cycle)",
    )


@pytest.mark.asyncio
async def test_control_the_periodic_pass_still_runs_on_a_tick_with_no_due_clearing_event(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [{**_clearing_event(e), "time": 99_000}], clearing_every=1)

    await ticks(runner, run, 1)

    _healthy(run)
    assert await _debts(factory, eq, p) == {}  # the periodic clearing cleared it
    assert 0 not in run._real_fired_scenario_event_indexes  # the event is not due yet


# ------------------------------------------------------------------------------------------- 3. the tick's metrics


@pytest.mark.asyncio
async def test_the_scripted_clearing_volume_is_in_the_tick_metrics(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)])

    await ticks(runner, run, 1)

    _healthy(run)
    payload = run._real_last_tick_storage_payload
    volume = payload["metric_values_by_eq"][eq.code]["clearing_volume"]
    require_target(Decimal(str(volume)) == Decimal("10"), f"the tick's clearing_volume is {volume!r} (expected the 10.00 the episode cleared)")


# ------------------------------------------------------------------------------------ 4. a restart during the tick


@pytest.mark.asyncio
async def test_a_restart_during_the_money_phase_does_not_spend_the_new_epochs_event(factory, monkeypatch) -> None:  # noqa: F811
    """The tick planned under epoch 0; a restart (epoch 1) lands while its money phase runs. The payment goes under the
    old epoch's key and commits; marking the event fired afterwards would spend the NEW epoch's event unexecuted."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    original = runner._real_payments_executor.execute_planned_payments
    state = {"restarted": False}

    async def restart_then_pay(**kwargs):
        if not state["restarted"]:
            state["restarted"] = True
            await _restart(run, runner._lock)
        return await original(**kwargs)

    monkeypatch.setattr(runner._real_payments_executor, "execute_planned_payments", restart_then_pay)

    await ticks(runner, run, 1)
    spent_after_the_old_tick = sorted(run._real_fired_scenario_event_indexes)
    debts_after_the_old_tick = await _debts(factory, eq, p)
    await ticks(runner, run, 1)  # a tick of the new epoch

    require_target(
        spent_after_the_old_tick == [] and debts_after_the_old_tick == {("A", "B"): Decimal("5.00")}
        and await _debts(factory, eq, p) == {("A", "B"): Decimal("10.00")} and run._launch_epoch == 1,
        f"after the tick that straddled the restart: fired {spent_after_the_old_tick}, debts {debts_after_the_old_tick}; "
        f"after the new epoch's tick: {await _debts(factory, eq, p)}",
    )


@pytest.mark.asyncio
async def test_a_restart_during_the_clearing_pass_writes_no_progress_of_the_old_epoch(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)])
    original = clearing_runner.run_clearing_pass
    state = {"restarted": False}

    async def restart_then_clear(*args, **kwargs):
        if not state["restarted"]:
            state["restarted"] = True
            await _restart(run, runner._lock)
        return await original(*args, **kwargs)

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", restart_then_clear)

    await ticks(runner, run, 1)

    require_target(
        getattr(run, "_real_story_progress", {}) == {} and 0 not in run._real_fired_scenario_event_indexes and run._launch_epoch == 1,
        f"progress {getattr(run, '_real_story_progress', None)}, fired {sorted(run._real_fired_scenario_event_indexes)}",
    )


# ------------------------------------------------------------------------- 5. a transient failure before admission


@pytest.mark.asyncio
async def test_a_payment_that_timed_out_before_admission_does_not_spend_the_event(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    original = PaymentService.create_payment_internal_staged
    state = {"calls": 0}

    async def timing_out_once(self_, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise TimeoutException("p036 fixdelta: routing timed out before the payment was admitted")
        return await original(self_, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", timing_out_once)

    await ticks(runner, run, 1)
    after_timeout = (sorted(run._real_fired_scenario_event_indexes), await _debts(factory, eq, p))
    _forget_failures(run)
    await ticks(runner, run, 1)

    require_target(
        after_timeout == ([], {}) and await _debts(factory, eq, p) == {("A", "B"): Decimal("5.00")}
        and 0 in run._real_fired_scenario_event_indexes,
        f"after the timeout: fired/debts {after_timeout}; after the next tick: {await _debts(factory, eq, p)}",
    )


@pytest.mark.asyncio
async def test_control_a_logical_refusal_spends_the_event(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["C"], "5.00")])

    await ticks(runner, run, 1)
    await ticks(runner, run, 1)

    assert runner._sse.published("tx.failed") == 1 and 0 in run._real_fired_scenario_event_indexes


# ---------------------------------------------------------------------------------- 6. SENDER_NOT_FOUND is a refusal


@pytest.mark.asyncio
async def test_a_scripted_payment_of_a_sender_the_run_does_not_hold_does_not_spend_the_error_budget(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["C"], q["B"], "1.00")])
    run._real_participants = [(x.id, x.pid) for x in (p["A"], p["B"])]

    await ticks(runner, run, 1)

    assert runner._sse.published("tx.failed") == 1 and 0 in run._real_fired_scenario_event_indexes  # control
    require_target(run.errors_total == 0 and run.state == "running", f"errors_total {run.errors_total}, state {run.state}")
