"""036 B2: `pause_after`, `inject_enabled` and the run's episode progress - on a real tick, PostgreSQL clone.

Every target is RED on `7df35fcf`; controls are the same stand with the input in its good spelling. The stand is
`test_p036_b1_scripted_events_postgres.py` (a real `tick_real_mode`).

* `pause_after` (spec 036): after the tick on which an episode with `pause_after: true` was SPENT, the run is `paused`
  (after the money phase and the clearing of that tick). "Spent" is the B1 meaning: a clearing that did not complete
  (`incomplete`) is not spent and does not pause; a restart during the tick, a stop in progress and a tick of another epoch
  do not pause. A `stress` event is never "spent" (it is a time window, not an occurrence) and never pauses.
* `inject_enabled`: the scenario's opt-in is NEVER higher than the process flag; an absent setting leaves the flag alone;
  a skipped inject is said aloud (the events artifact and the run's episode progress), not silent.
* `RunStatus.episode_progress`: one typed entry per tracked event (a scripted payment or clearing, a skipped inject): the
  index, the epoch, a TRUE outcome (`done` / `incomplete` / `refused` and a reason), a clearing's exact cycles, a payment's
  outcome - the successful one too, which the sparse dictionary of B1 did not carry.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

import app.core.clearing.runner as clearing_runner
from app.core.simulator.runtime_utils import run_to_status
from app.db.models.participant import Participant
from app.utils.exceptions import TimeoutException
from tests.contract.openapi_response_conformance import load_canon, validate_body
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

CAPTION = {"ru": "р", "en": "e"}


def _note(time: int, *, pause_after: bool | None = None, **extra) -> dict:
    event = {"time": time, "type": "note", "caption": CAPTION, **extra}
    if pause_after is not None:
        event["pause_after"] = pause_after
    return event


def _record_publishes(runner) -> list[str]:
    published: list[str] = []
    runner._publish_run_status = lambda run_id: published.append(run_id)
    return published


# ----------------------------------------------------------------------------------------------------- pause_after


@pytest.mark.asyncio
async def test_a_tick_that_spends_an_episode_with_pause_after_pauses_the_run(factory, monkeypatch) -> None:  # noqa: F811
    """TARGET (red on 7df35fcf: the run stays `running`)."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], [_note(500, pause_after=True), _note(60_000)])
    published = _record_publishes(runner)

    await ticks(runner, run, 1)

    assert run._real_fired_scenario_event_indexes == {0}  # control: the episode was spent, the next one is not due
    require_target(run.state == "paused" and len(published) >= 1, f"state {run.state!r}, status published {len(published)}x")


@pytest.mark.asyncio
async def test_control_an_episode_without_pause_after_or_with_it_false_leaves_the_run_running(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], [_note(500), _note(600, pause_after=False)])

    await ticks(runner, run, 1)

    _healthy(run)
    assert run._real_fired_scenario_event_indexes == {0, 1}  # both spent, neither pauses


@pytest.mark.asyncio
async def test_a_paused_story_resumes_and_pauses_again_only_at_its_next_episode(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B"], PAY_LINES, [], [_note(500, pause_after=True), _note(3500, pause_after=True)])

    await ticks(runner, run, 1)
    first = run.state
    run.state = "running"  # `resume`
    await ticks(runner, run, 1)  # sim time 3000: the second episode (3500) is not due yet - nothing spent, nothing paused
    second = (run.state, sorted(run._real_fired_scenario_event_indexes))
    await ticks(runner, run, 1)  # sim time 4000: due

    assert second == ("running", [0]), second  # control: the spent episode does not pause again
    require_target(first == "paused" and run.state == "paused" and sorted(run._real_fired_scenario_event_indexes) == [0, 1],
                   f"first {first!r}, after the second episode {run.state!r}")


@pytest.mark.asyncio
async def test_two_episodes_with_pause_after_on_one_tick_pause_once(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B"], PAY_LINES, [], [_note(100, pause_after=True), _note(200, pause_after=True)])
    published = _record_publishes(runner)

    await ticks(runner, run, 1)

    require_target(run.state == "paused" and len(published) == 1 and run._real_fired_scenario_event_indexes == {0, 1},
                   f"state {run.state!r}, published {len(published)}, fired {sorted(run._real_fired_scenario_event_indexes)}")


@pytest.mark.asyncio
async def test_a_scripted_payment_with_pause_after_pauses_after_it_is_durable(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [{**_payment_event(e, q["A"], q["B"], "5.00"), "caption": CAPTION, "pause_after": True}])

    await ticks(runner, run, 1)

    assert await _debts(factory, eq, p) == {("A", "B"): Decimal("5.00")}  # control: the money moved
    require_target(run.state == "paused", f"state {run.state!r}")


@pytest.mark.asyncio
async def test_a_clearing_that_did_not_complete_does_not_pause_and_the_tick_that_completes_it_does(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [{**_clearing_event(e), "caption": CAPTION, "pause_after": True}])
    original = clearing_runner.run_clearing_pass
    calls = {"n": 0}

    async def failing_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("p036 b2: the pass fails once")
        return await original(*args, **kwargs)

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", failing_once)

    await ticks(runner, run, 1)
    after_failure = (run.state, sorted(run._real_fired_scenario_event_indexes))
    run.state, run.errors_total, run.last_error = "running", 0, None
    await ticks(runner, run, 1)

    assert after_failure == ("running", []), after_failure  # control: an incomplete episode is not spent and does not pause
    require_target(run.state == "paused" and sorted(run._real_fired_scenario_event_indexes) == [0], f"state {run.state!r}")


@pytest.mark.asyncio
async def test_a_restart_during_the_tick_does_not_pause_the_new_launch(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [{**_payment_event(e, q["A"], q["B"], "5.00"), "caption": CAPTION, "pause_after": True}])
    original = runner._real_payments_executor.execute_planned_payments
    state = {"restarted": False}

    async def restart_then_pay(**kwargs):
        if not state["restarted"]:
            state["restarted"] = True
            await _restart(run, runner._lock)
        return await original(**kwargs)

    monkeypatch.setattr(runner._real_payments_executor, "execute_planned_payments", restart_then_pay)

    await ticks(runner, run, 1)

    require_target(run.state == "running" and run._launch_epoch == 1, f"state {run.state!r}, epoch {run._launch_epoch}")


@pytest.mark.asyncio
async def test_a_run_that_is_being_stopped_is_not_turned_into_a_paused_one(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], [_note(500, pause_after=True)])
    original = runner._apply_due_scenario_events

    async def stopping(session, **kwargs):
        await original(session, **kwargs)
        run.state = "stopping"

    monkeypatch.setattr(runner, "_apply_due_scenario_events", stopping)

    await ticks(runner, run, 1)

    assert run.state == "stopping", run.state  # anti-vacuum: the pause rule must not override a stop in progress


@pytest.mark.asyncio
async def test_control_a_stress_event_with_pause_after_never_pauses(factory, monkeypatch) -> None:  # noqa: F811
    """Declared: a `stress` event is a time window with no 'spent' moment, so `pause_after` on it has no effect."""

    stress = {"time": 0, "type": "stress", "caption": CAPTION, "pause_after": True, "metadata": {"duration_ms": 60_000},
              "effects": [{"op": "mult", "field": "tx_rate", "scope": "all", "value": 2}]}
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], [stress])

    await ticks(runner, run, 2)

    _healthy(run)


# ----------------------------------------------------------------------------------------------------- inject_enabled


def _freeze(pid: str) -> dict:
    return {"time": 0, "type": "inject", "effects": [{"op": "freeze_participant", "participant_id": pid}]}


async def _status_of(factory, participant) -> str:  # noqa: F811
    async with factory() as s:
        return (await s.execute(select(Participant.status).where(Participant.id == participant.id))).scalar_one()


async def _inject_case(factory, monkeypatch, *, process_flag: bool, scenario_flag):  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_freeze(q["B"].pid)])
    runner._real_enable_inject = process_flag
    if scenario_flag is not None:
        run._scenario_raw = None
        scenario = runner._get_scenario_raw("")
        scenario["settings"]["playback"] = {"inject_enabled": scenario_flag}
    await ticks(runner, run, 1)
    return eq, p, run, runner


@pytest.mark.asyncio
async def test_control_the_process_flag_alone_decides_when_the_scenario_says_nothing(factory, monkeypatch) -> None:  # noqa: F811
    _eq, p, run, _runner = await _inject_case(factory, monkeypatch, process_flag=True, scenario_flag=None)
    assert await _status_of(factory, p["B"]) == "suspended"
    _eq2, p2, run2, _runner2 = await _inject_case(factory, monkeypatch, process_flag=False, scenario_flag=None)
    assert await _status_of(factory, p2["B"]) == "active"  # the process flag off: nothing applied (today's behaviour)


@pytest.mark.asyncio
async def test_a_scenario_that_disables_inject_is_obeyed_under_a_process_flag_that_allows_it(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _inject_case(factory, monkeypatch, process_flag=True, scenario_flag=False)

    _healthy(run)
    progress = getattr(run, "_real_story_progress", {}).get(0, {})
    require_target(
        await _status_of(factory, p["B"]) == "active" and 0 in run._real_fired_scenario_event_indexes
        and progress.get("status") == "refused" and progress.get("reason") == "inject_disabled_by_scenario",
        f"B is {await _status_of(factory, p['B'])!r}, progress {progress}",
    )


@pytest.mark.asyncio
async def test_a_scenario_never_enables_inject_above_the_process_flag(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _inject_case(factory, monkeypatch, process_flag=False, scenario_flag=True)

    _healthy(run)
    progress = getattr(run, "_real_story_progress", {}).get(0, {})
    assert await _status_of(factory, p["B"]) == "active"  # control: the opt-in did not raise the ceiling
    require_target(progress.get("status") == "refused" and progress.get("reason") == "inject_disabled_by_process", f"progress {progress}")


@pytest.mark.asyncio
async def test_a_scenario_opt_in_under_a_process_flag_that_allows_it_applies_the_inject(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _inject_case(factory, monkeypatch, process_flag=True, scenario_flag=True)

    assert await _status_of(factory, p["B"]) == "suspended"  # control: both on - applied
    assert getattr(run, "_real_story_progress", {}).get(0) is None or getattr(run, "_real_story_progress", {}).get(0, {}).get("status") != "refused"


# ------------------------------------------------------------------------------------------- RunStatus.episode_progress


def _progress(run) -> list:
    status = run_to_status(run)
    return getattr(status, "episode_progress", "absent")


@pytest.mark.asyncio
async def test_the_progress_of_a_successful_scripted_payment_is_reported_with_its_outcome(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])

    await ticks(runner, run, 1)

    items = _progress(run)
    require_target(
        isinstance(items, list) and len(items) == 1 and items[0].index == 0 and items[0].epoch == 0
        and items[0].kind == "payment" and items[0].status == "done" and items[0].reason is None
        and items[0].payment is not None and (items[0].payment.amount, items[0].payment.equivalent) == ("5.00", eq.code)
        and (items[0].payment.from_, items[0].payment.to) == (p["A"].pid, p["B"].pid),
        f"episode_progress {items!r}",
    )


@pytest.mark.asyncio
async def test_a_refused_scripted_payment_is_reported_refused_with_the_code_of_its_tx_failed(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["C"], "5.00")])

    await ticks(runner, run, 1)

    failed = [e for e in runner._sse.events if e.get("type") == "tx.failed"]
    items = _progress(run)
    assert len(failed) == 1  # control
    code = failed[0]["error"]["code"]
    require_target(isinstance(items, list) and len(items) == 1 and items[0].status == "refused" and items[0].reason == code,
                   f"episode_progress {items!r}, tx.failed code {code!r}")


@pytest.mark.asyncio
async def test_a_payment_whose_outcome_is_unresolved_is_incomplete_and_becomes_done_when_it_lands(factory, monkeypatch) -> None:  # noqa: F811
    from app.core.payments.service import PaymentService

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    original = PaymentService.create_payment_internal_staged
    state = {"n": 0}

    async def timing_out_once(self_, *args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutException("p036 b2: routing timed out")
        return await original(self_, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", timing_out_once)

    await ticks(runner, run, 1)
    first = _progress(run)
    run.state, run.errors_total, run.last_error = "running", 0, None
    await ticks(runner, run, 1)
    last = _progress(run)

    require_target(
        isinstance(first, list) and first[0].status == "incomplete" and first[0].reason == "PAYMENT_TIMEOUT"
        and isinstance(last, list) and len(last) == 1 and last[0].status == "done",
        f"after the timeout {first!r}; after the next tick {last!r}",
    )


@pytest.mark.asyncio
async def test_the_progress_of_a_scripted_clearing_carries_its_exact_cycles_on_the_wire_aliases(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)])

    await ticks(runner, run, 1)

    items = _progress(run)
    require_target(
        isinstance(items, list) and len(items) == 1 and items[0].kind == "clearing" and items[0].status == "done"
        and items[0].cleared_cycles == 1 and items[0].cycles[0].cleared_amount == "10.00",
        f"episode_progress {items!r}",
    )
    wire = run_to_status(run).model_dump(mode="json", by_alias=True)["episode_progress"][0]
    edges = wire["cycles"][0]["edges"]
    assert {frozenset(e) for e in edges} == {frozenset({"from", "to"})}  # the wire keys are `from`/`to`, never `from_`
    assert validate_body(load_canon(), "/components/schemas/RunStatus", run_to_status(run).model_dump(mode="json", by_alias=True)) == []


@pytest.mark.asyncio
async def test_a_skipped_inject_is_in_the_progress_and_a_note_event_is_not(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_note(0), _freeze(q["B"].pid)])
    runner._real_enable_inject = False

    await ticks(runner, run, 1)

    items = _progress(run)
    require_target(
        isinstance(items, list) and [(i.index, i.kind, i.status, i.reason) for i in items] == [(1, "inject", "refused", "inject_disabled_by_process")],
        f"episode_progress {items!r}",
    )


@pytest.mark.asyncio
async def test_a_run_without_a_tracked_event_has_no_progress(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], [_note(0)])

    await ticks(runner, run, 1)

    require_target(_progress(run) is None, f"episode_progress {_progress(run)!r} (expected null: nothing is tracked)")
