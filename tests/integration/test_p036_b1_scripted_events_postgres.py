"""036 slice B1 (`T3620`): a scripted `payment` / `clearing` event of a scenario is EXECUTED, once per launch, by the core.

Until now the runner marked both types fired without doing anything (`real_runner_impl.py`, "Unknown / unsupported event
types are ignored, but we still mark them fired once due" - F-036-2): a scenario promised a purchase or a clearing and the
graph never moved. Each target test below is RED on `24b09d83`; the controls (a stand that sees a debt move, a clearing
that runs on the periodic cadence, a freeze that publishes) are green there and stay green.

THE KEY. A scripted payment is one planned payment of the tick's money phase (the path of every simulated payment: staged
under a savepoint, published after the commit), with its OWN idempotency key: `run_id | launch epoch | event index` -
`scripted_event_idempotency_key`. Not the tick's key (`run_id|tick|sender|receiver|equivalent|amount|seq|epoch`): a scripted
event is not tied to the tick it happens to run at, so a tick that failed before the event was marked fired and ran it
again one tick later repeats the SAME key and the payment service answers with the stored payment (no double debt); and a
restart bumps the epoch, so the same event is a NEW operation that really moves money again (the class of F-034-1: a
repeated key must never report as paid a payment that moved nothing).

THE OUTCOMES. Whatever the core says is the answer - a refusal is the ordinary `tx.failed` (the caption of the episode
explains it), never a silent skip: a line missing, an amount finer than the equivalent's step (the core's own
`require_money_step`, at the point of use - upload reads no database), an equivalent or a participant the database does not
know, a suspended participant. Slice A's list of what B owes is this file.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.db.models.debt import Debt
from app.db.models.participant import Participant
from tests.integration.test_p021_trust_drift_is_audited_postgres import (  # noqa: F401 - `factory` is a fixture
    factory,
    run_for,
    runner_for,
    ticks,
    world,
)
from tests.integration.test_p034_s1_restart_repeats_the_idempotency_key_postgres import (
    _record_staged,
    _restart,
)
from tests.p021_support import TargetMismatch, require_target  # noqa: F401
from tests.simulator_tick_stand import install_tick_stand

NO_DRIFT = {"enabled": False}
PAY_LINES = [("B", "A", "100.00", "active")]  # B extends credit to A: A can pay B
CYCLE_LINES = [("B", "A", "100.00", "active"), ("C", "B", "100.00", "active"), ("A", "C", "100.00", "active")]
CYCLE_DEBTS = [("A", "B", "10.00"), ("B", "C", "10.00"), ("C", "A", "10.00")]  # A owes B owes C owes A


def _scenario(eq, people: dict, lines, events: list[dict], *, line_status: str | None = None) -> dict:
    """A fixture-shaped scenario: the trust lines carry NO `status` unless asked (the schema has none for a line)."""

    return {
        "equivalents": [eq.code],
        "participants": [{"id": p.pid} for p in people.values()],
        "trustlines": [
            {"from": people[c].pid, "to": people[d].pid, "equivalent": eq.code, "limit": limit,
             **({"status": line_status} if line_status else {})}
            for c, d, limit, _status in lines
        ],
        "behaviorProfiles": [],
        "settings": {"trust_drift": NO_DRIFT},
        "events": events,
    }


def _payment_event(eq, sender, receiver, amount: str, **extra) -> dict:
    return {"time": 0, "type": "payment", "from": sender.pid, "to": receiver.pid, "amount": amount,
            "equivalent": eq.code, **extra}


def _clearing_event(eq, **extra) -> dict:
    return {"time": 0, "type": "clearing", "equivalent": eq.code, **extra}


async def _debts(factory, eq, people: dict) -> dict[tuple[str, str], Decimal]:
    """{(debtor, creditor): amount} in role letters, positive rows only."""

    names = {p.id: role for role, p in people.items()}
    async with factory() as s:
        rows = (await s.execute(select(Debt).where(Debt.equivalent_id == eq.id))).scalars().all()
    return {(names[r.debtor_id], names[r.creditor_id]): Decimal(str(r.amount)) for r in rows if Decimal(str(r.amount)) > 0}


async def _stand(factory, monkeypatch, roles, lines, debts, events, *, clearing_every=10_000, line_status=None):
    eq, p = await world(factory, roles, lines, debts)
    scenario = _scenario(eq, p, lines, events(eq, p) if callable(events) else events, line_status=line_status)
    run = run_for(list(p.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=clearing_every)
    install_tick_stand(monkeypatch, factory)
    return eq, p, run, runner


def _healthy(run) -> None:
    assert run.state == "running" and run.errors_total == 0, (run.state, run.errors_total, run.last_error)


# ---------------------------------------------------------------------------------------------------- the payment


@pytest.mark.asyncio
async def test_control_the_stand_sees_a_payment_move_a_debt(factory) -> None:  # noqa: F811
    from app.core.payments.service import PaymentService

    eq, p = await world(factory, ["A", "B"], PAY_LINES, [])
    assert await _debts(factory, eq, p) == {}
    async with factory() as s:
        await PaymentService(s).create_payment_internal(p["A"].id, to_pid=p["B"].pid, equivalent=eq.code, amount="5.00")
    assert await _debts(factory, eq, p) == {("A", "B"): Decimal("5.00")}


@pytest.mark.asyncio
async def test_a_scripted_payment_moves_the_debt_by_the_core_and_publishes_tx_updated(factory, monkeypatch) -> None:  # noqa: F811
    """TARGET F-036-2 (red on 24b09d83): at `intensity_percent=0` the event pays - the debt of the sender to the receiver
    grows by `amount`, a `tx.updated` is published and the event is spent. Before: debts `{}`, no `tx.updated`."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])

    await ticks(runner, run, 1)

    _healthy(run)
    assert run.intensity_percent == 0  # the tick plans no payment of its own
    debts = await _debts(factory, eq, p)
    published = runner._sse.published("tx.updated")
    require_target(
        debts == {("A", "B"): Decimal("5.00")} and published >= 1,
        f"the scripted payment A -> B 5.00 left debts {debts} and {published} tx.updated "
        f"(expected {{('A', 'B'): Decimal('5.00')}} and >= 1)",
    )


@pytest.mark.asyncio
async def test_a_scripted_payment_waits_for_its_time(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B"], PAY_LINES, [],
        lambda e, q: [{**_payment_event(e, q["A"], q["B"], "5.00"), "time": 3000}],
    )

    await ticks(runner, run, 1)  # sim time 2000: not yet
    before = await _debts(factory, eq, p)
    await ticks(runner, run, 1)  # sim time 3000: due
    after = await _debts(factory, eq, p)

    _healthy(run)
    assert before == {}, before  # control: nothing paid early
    require_target(after == {("A", "B"): Decimal("5.00")}, f"at its time the event left debts {after}")


@pytest.mark.asyncio
async def test_a_scripted_payment_runs_once_and_its_key_is_the_events_not_the_ticks(factory, monkeypatch) -> None:  # noqa: F811
    """The same event offered again within ONE launch (its fired mark lost - a failed tick, a reconnect) repeats the key
    and moves nothing. Key material: run id, launch epoch, event index (and nothing of the tick)."""

    from app.core.simulator.real_runner_impl import scripted_event_idempotency_key  # red: absent on 24b09d83

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    calls = _record_staged(monkeypatch)

    await ticks(runner, run, 1)
    first = await _debts(factory, eq, p)
    run._real_fired_scenario_event_indexes.clear()  # the same launch, the event is due again
    await ticks(runner, run, 1)  # another tick number
    second = await _debts(factory, eq, p)

    _healthy(run)
    scripted = [c for c in calls if c["key"] == scripted_event_idempotency_key(run.run_id, 0, 0)]
    require_target(
        first == second == {("A", "B"): Decimal("5.00")} and len(scripted) == 2
        and [c["written_here"] for c in scripted] == [True, False] and len({c["tx_id"] for c in scripted}) == 1,
        f"first {first}, replay {second}; calls with the event's key: {scripted}",
    )


@pytest.mark.asyncio
async def test_after_a_restart_the_event_pays_again_as_a_new_operation(factory, monkeypatch) -> None:  # noqa: F811
    """TARGET F-036-2 (b): `restart` starts the story over - the fired set is cleared, the launch epoch is part of the key,
    so the same episode is a NEW payment that really moves money (debt 5.00 -> 10.00), not the stored one of the first
    launch reported as paid (F-034-1). A new run is how a story is replayed from scratch; a restart does not undo the
    debts of the launch before it (spec 036, 'Verification plan')."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    calls = _record_staged(monkeypatch)

    await ticks(runner, run, 1)
    first = await _debts(factory, eq, p)
    await _restart(run, runner._lock)
    assert run._launch_epoch == 1  # control: a new launch
    await ticks(runner, run, 1)
    second = await _debts(factory, eq, p)

    _healthy(run)
    paid = [c for c in calls if c["amount"] == "5.00"]
    require_target(
        first == {("A", "B"): Decimal("5.00")} and second == {("A", "B"): Decimal("10.00")}
        and len({c["key"] for c in paid}) == 2 and all(c["written_here"] for c in paid) and len(paid) == 2,
        f"first launch {first}, after the restart {second}; payments {paid}",
    )


@pytest.mark.asyncio
async def test_a_tick_whose_money_phase_failed_does_not_spend_the_event(factory, monkeypatch) -> None:  # noqa: F811
    """The event is marked fired only after the money phase COMMITTED. A tick that fails inside it leaves the event
    pending; the next tick runs it - once."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["A"], q["B"], "5.00")])
    original = runner._real_payments_executor.execute_planned_payments
    state = {"calls": 0}

    async def failing_once(**kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("p036 b1: the money phase fails before anything commits")
        return await original(**kwargs)

    monkeypatch.setattr(runner._real_payments_executor, "execute_planned_payments", failing_once)

    await ticks(runner, run, 1)  # the failing tick
    assert await _debts(factory, eq, p) == {}  # control: nothing moved
    run.state, run.errors_total, run.last_error = "running", 0, None  # the run survives one failed tick
    await ticks(runner, run, 1)
    debts = await _debts(factory, eq, p)

    require_target(debts == {("A", "B"): Decimal("5.00")},
                   f"after a failed tick and one good tick the debts are {debts} (state {run.state}, fired "
                   f"{sorted(run._real_fired_scenario_event_indexes)})")


# ------------------------------------------------------------------------------------------ refusals are not silence


async def _refusal_case(factory, monkeypatch, events, *, roles=("A", "B", "C"), lines=PAY_LINES, prep=None):
    eq, p, run, runner = await _stand(factory, monkeypatch, list(roles), lines, [], events)
    if prep is not None:
        await prep(eq, p)
    await ticks(runner, run, 1)
    return eq, p, run, runner


@pytest.mark.asyncio
async def test_a_payment_without_a_line_is_a_tx_failed_and_is_not_retried(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _refusal_case(
        factory, monkeypatch, lambda e, q: [_payment_event(e, q["A"], q["C"], "5.00")])  # no line between A and C
    await ticks(runner, run, 1)  # a second tick must not repeat a refused event

    _healthy(run)
    assert await _debts(factory, eq, p) == {}  # control
    failed = runner._sse.published("tx.failed")
    require_target(failed == 1, f"a refused scripted payment published {failed} tx.failed over two ticks (expected exactly 1)")


@pytest.mark.asyncio
async def test_a_payment_finer_than_the_equivalents_step_is_refused_by_the_core_at_execution(factory, monkeypatch) -> None:  # noqa: F811
    """The step check upload does not make (slice A, N2): the core's `require_money_step`, at the point of use. At
    precision 2 `"1.505"` is refused without rounding and `"1.500"` is paid as 1.50."""

    eq, p, run, runner = await _refusal_case(
        factory, monkeypatch,
        lambda e, q: [{**_payment_event(e, q["A"], q["B"], "1.505"), "time": 0},
                      {**_payment_event(e, q["A"], q["B"], "1.500"), "time": 0}])

    _healthy(run)
    debts = await _debts(factory, eq, p)
    failed = runner._sse.published("tx.failed")
    require_target(debts == {("A", "B"): Decimal("1.50")} and failed == 1,
                   f"debts {debts}, tx.failed {failed} (expected 1.50 paid, exactly the finer one refused)")


@pytest.mark.asyncio
async def test_a_payment_in_an_equivalent_the_database_does_not_know_is_refused_not_dropped(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _refusal_case(
        factory, monkeypatch,
        lambda e, q: [{**_payment_event(e, q["A"], q["B"], "1.00"), "equivalent": "NOSUCHEQ"}])

    assert run.state == "running", (run.state, run.last_error)  # control: the tick survived
    failed = runner._sse.published("tx.failed")
    require_target(failed == 1 and 0 in run._real_fired_scenario_event_indexes,
                   f"tx.failed {failed}, fired {sorted(run._real_fired_scenario_event_indexes)}")


@pytest.mark.asyncio
async def test_a_payment_of_a_participant_the_run_does_not_hold_is_refused_not_dropped(factory, monkeypatch) -> None:  # noqa: F811
    """C exists in the database and in the scenario but is not one of the run's participants (not yet introduced): the
    sender is unknown to the run, and the answer is an explicit refusal, the event spent - not a silent skip."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], PAY_LINES, [], lambda e, q: [_payment_event(e, q["C"], q["B"], "1.00")])
    run._real_participants = [(x.id, x.pid) for x in (p["A"], p["B"])]

    await ticks(runner, run, 1)

    assert run.state == "running", (run.state, run.last_error)  # control
    assert await _debts(factory, eq, p) == {}
    failed = runner._sse.published("tx.failed")
    require_target(failed == 1 and 0 in run._real_fired_scenario_event_indexes,
                   f"tx.failed {failed}, fired {sorted(run._real_fired_scenario_event_indexes)}")


@pytest.mark.asyncio
async def test_a_payment_of_a_suspended_participant_is_refused_by_the_core(factory, monkeypatch) -> None:  # noqa: F811
    async def suspend(eq, p):
        async with factory() as s:
            await s.execute(update(Participant).where(Participant.id == p["A"].id).values(status="suspended"))
            await s.commit()

    eq, p, run, runner = await _refusal_case(
        factory, monkeypatch, lambda e, q: [_payment_event(e, q["A"], q["B"], "1.00")], prep=suspend)

    assert await _debts(factory, eq, p) == {}  # control: a suspended participant moves nothing
    failed = runner._sse.published("tx.failed")
    require_target(failed == 1, f"a suspended sender's scripted payment published {failed} tx.failed (expected 1)")


# --------------------------------------------------------------------------------------------------- the clearing


@pytest.mark.asyncio
async def test_control_a_clearing_tick_closes_the_cycle_and_publishes_clearing_done(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, [], clearing_every=1)
    assert len(await _debts(factory, eq, p)) == 3

    await ticks(runner, run, 1)

    _healthy(run)
    assert await _debts(factory, eq, p) == {}
    assert runner._sse.published("clearing.done") >= 1


@pytest.mark.asyncio
async def test_a_scripted_clearing_closes_the_cycle_publishes_clearing_done_and_keeps_its_cycles(factory, monkeypatch) -> None:  # noqa: F811
    """TARGET F-036-2: with the periodic clearing out of the way, the `clearing` event runs the common clearing runner for
    its equivalent - the cycle closes, `clearing.done` is published once - and the exact cycles (as `clearing-real`
    reports them: amount, edges creditor -> debtor by pid) are kept in the run's story progress under the event's index."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS, lambda e, q: [_clearing_event(e)])

    await ticks(runner, run, 1)
    await ticks(runner, run, 1)  # a second tick must not clear again

    _healthy(run)
    debts = await _debts(factory, eq, p)
    done = runner._sse.published("clearing.done")
    progress = getattr(run, "_real_story_progress", None)
    expected_edges = {(p["B"].pid, p["A"].pid), (p["C"].pid, p["B"].pid), (p["A"].pid, p["C"].pid)}  # creditor -> debtor
    cycles = (progress or {}).get(0, {}).get("cycles")
    got_edges = {(e["from"], e["to"]) for c in (cycles or []) for e in c["edges"]}
    require_target(
        debts == {} and done == 1 and cycles is not None and len(cycles) == 1
        and cycles[0]["cleared_amount"] == "10.00" and got_edges == expected_edges and 0 in run._real_fired_scenario_event_indexes,
        f"debts {debts}, clearing.done {done}, progress {progress}",
    )


@pytest.mark.asyncio
async def test_a_clearing_in_an_equivalent_the_run_does_not_have_is_refused_not_dropped(factory, monkeypatch) -> None:  # noqa: F811
    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS,
                                      lambda e, q: [{"time": 0, "type": "clearing", "equivalent": "NOSUCHEQ"}])

    await ticks(runner, run, 1)

    assert run.state == "running", (run.state, run.last_error)  # control
    assert len(await _debts(factory, eq, p)) == 3  # control: the cycle was not touched
    progress = getattr(run, "_real_story_progress", None)
    require_target(0 in run._real_fired_scenario_event_indexes and (progress or {}).get(0, {}).get("status") == "refused",
                   f"fired {sorted(run._real_fired_scenario_event_indexes)}, progress {progress}")


# ------------------------------------------------------------------------------------------------ restart, F-036-6


@pytest.mark.asyncio
async def test_a_restart_clears_the_fired_events_and_the_story_progress(factory, monkeypatch) -> None:  # noqa: F811
    """BACKLOG 034-3 (g): after `restart` the set of fired scenario events was not reset. The story starts over."""

    eq, p, run, runner = await _stand(factory, monkeypatch, ["A", "B"], PAY_LINES, [], [{"time": 0, "type": "note", "description": "n"}])
    await ticks(runner, run, 1)
    assert run._real_fired_scenario_event_indexes == {0}  # control: the note fired

    await _restart(run, runner._lock)

    require_target(run._real_fired_scenario_event_indexes == set() and not getattr(run, "_real_story_progress", {}),
                   f"after the restart fired is {sorted(run._real_fired_scenario_event_indexes)}")


FREEZE_LINES = [("A", "B", "100.00", "active"), ("B", "C", "100.00", "active"), ("A", "C", "100.00", "active")]


async def _freeze_b(factory, monkeypatch, *, line_status):  # noqa: F811
    eq, p = await world(factory, ["A", "B", "C"], FREEZE_LINES, [])
    freeze = {"time": 0, "type": "inject", "effects": [{"op": "freeze_participant", "participant_id": p["B"].pid}]}
    scenario = _scenario(eq, p, FREEZE_LINES, [freeze], line_status=line_status)
    run = run_for(list(p.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=10_000)
    runner._real_enable_inject = True
    install_tick_stand(monkeypatch, factory)

    await ticks(runner, run, 1)

    _healthy(run)
    async with factory() as s:
        status = (await s.execute(select(Participant.status).where(Participant.id == p["B"].id))).scalar_one()
    assert status == "suspended", status  # control: the freeze happened
    changes = [e["payload"] for e in runner._sse.events if e.get("type") == "topology.changed"]
    assert [c["frozen_nodes"] for c in changes] == [[p["B"].pid]], changes  # control: it was announced, once
    got = sorted((e["from_pid"], e["to_pid"]) for c in changes for e in c["frozen_edges"])
    return got, sorted([(p["A"].pid, p["B"].pid), (p["B"].pid, p["C"].pid)])


@pytest.mark.asyncio
async def test_control_a_line_with_status_active_is_dimmed_when_its_participant_is_frozen(factory, monkeypatch) -> None:  # noqa: F811
    got, lines_of_b = await _freeze_b(factory, monkeypatch, line_status="active")

    assert got == lines_of_b, (got, lines_of_b)


@pytest.mark.asyncio
async def test_freezing_a_participant_of_a_fixture_dims_its_lines(factory, monkeypatch) -> None:  # noqa: F811
    """TARGET F-036-6 (confirmed on 75dafc82): fixture lines carry no `status`; the publication read only `active`."""

    got, lines_of_b = await _freeze_b(factory, monkeypatch, line_status=None)

    require_target(got == lines_of_b, f"freezing B published frozen_edges {got}, expected the lines of B {lines_of_b}")
