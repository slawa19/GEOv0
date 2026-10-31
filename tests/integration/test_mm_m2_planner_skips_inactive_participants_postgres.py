"""MM2-SOL-1 (BACKLOG, MiniMax pilot M2, 2026-10-31), the real path: a participant frozen by an inject event DURING a
run is no longer planned by the run's own ticks.

The stand is `RealRunnerImpl.tick_real_mode` on a mode-B clone (the p021 stand): the scenario's `freeze_participant`
inject at `time` 2500 is applied by the tick that reaches it, commits `suspended`, writes `suspended` into the run's
scenario dict in place (`cache_invalidator.py`, step 7), and every later tick plans from that dict. Before the fix the
frozen sender kept being planned and each of its payments came back `tx.failed` - on this path the ROUTER refuses
first (`ROUTING_NO_ROUTE`, `reason: no_route`: the frozen participant is out of its graph), before the money
boundary's 409 `participant_suspended` is reached; `ROUTING_NO_ROUTE` is a scenario refusal for the 054-A-7a stall
counter. After the fix the frozen sender is planned no more, and the active pair still pays (anti-vacuum).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.participant import Participant
from tests.integration.test_p021_trust_drift_is_audited_postgres import (  # noqa: F401 - `factory` is a fixture
    factory,
    run_for,
    runner_for,
    scenario_for,
    ticks,
    world,
)
from tests.simulator_tick_stand import install_tick_stand

# (creditor, debtor, limit, status): A pays B and C; B pays C (the control pair, A not on it).
LINES = [("B", "A", "1000.00", "active"), ("C", "A", "1000.00", "active"), ("C", "B", "1000.00", "active")]
FREEZE_AT_MS = 2_500
TICKS = 12


def _payments(events: list[dict], pid_by_role: dict[str, str]) -> list[tuple[str, str, str]]:
    """(event type, sender role, receiver role) of every payment the tick published, in order."""
    role = {pid: r for r, pid in pid_by_role.items()}
    out = []
    for e in events:
        if e.get("type") not in ("tx.updated", "tx.failed"):
            continue
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else e
        frm, to = payload.get("from"), payload.get("to")
        if frm in role and to in role:
            out.append((str(e.get("type")), role[frm], role[to]))
    return out


@pytest.mark.asyncio
async def test_a_participant_frozen_mid_run_is_planned_no_more(factory, monkeypatch) -> None:  # noqa: F811
    eq, people = await world(factory, ["A", "B", "C"], LINES, [])
    pid = {r: p.pid for r, p in people.items()}
    scenario = scenario_for(eq, people, LINES, {"enabled": False})
    scenario["events"] = [{"type": "inject", "time": FREEZE_AT_MS,
                           "effects": [{"op": "freeze_participant", "participant_id": pid["A"]}]}]
    run = run_for(list(people.values()), eq.code)
    run.intensity_percent = 100
    run._scenario_raw = scenario
    run._edges_by_equivalent = {eq.code: [(pid[c], pid[d]) for c, d, _l, _s in LINES]}
    runner = runner_for(run, scenario, clearing_every=10_000)
    runner._real_enable_inject = True  # the process flag `SIMULATOR_REAL_ENABLE_INJECT`, as p036 B1 sets it
    install_tick_stand(monkeypatch, factory)

    await ticks(runner, run, 1)  # sim time 2000: before the freeze
    before = _payments(runner._sse.events, pid)
    seen = len(runner._sse.events)
    await ticks(runner, run, TICKS)  # the first of these reaches 3000 and applies the freeze
    after = _payments(runner._sse.events[seen:], pid)

    async with factory() as s:
        status_a = await s.scalar(select(Participant.status).where(Participant.id == people["A"].id))
    in_memory = next(p.get("status") for p in scenario["participants"] if p["id"] == pid["A"])
    assert (status_a, in_memory) == ("suspended", "suspended"), (status_a, in_memory)  # the freeze landed
    assert run.state == "running" and run.errors_total == 0, (run.state, run.last_error)
    assert before, "control: some payment is planned before the freeze"

    # The freeze tick itself may already have planned from the pre-freeze dict; every LATER tick plans from the new one.
    from_a = [p for p in after if "A" in p[1:]]
    assert not from_a, (
        f"actual: {len(from_a)} of {len(after)} payments published after the freeze have frozen A at one end "
        f"({sorted(set(from_a))}); each is refused at the money boundary"
    )
    # anti-vacuum: the pair without A keeps paying
    assert any(p == ("tx.updated", "B", "C") for p in after), after


@pytest.mark.asyncio
async def test_a_freeze_that_leaves_no_candidate_does_not_pause_the_run(factory, monkeypatch) -> None:  # noqa: F811
    """054-A-7a, the behaviour change: every line has the frozen participant at one end.

    Before the fix the frozen sender's payments were planned and refused by the router as `ROUTING_NO_ROUTE` - a
    scenario refusal (`rejection_codes.py`, `SCENARIO_REFUSAL_CODES`) - so the stall counter grew and the run paused
    itself `scenario_exhausted` (measured on 4197b3cd: `('paused', 'scenario_exhausted', 4)`). After the fix the plan
    is empty from the tick after the freeze; an empty plan at intensity above 0 is an empty share
    (`real_payments_executor.py`, `is_empty_share`): the counter neither grows nor resets, and `_stall_step` decides
    nothing (`tick.py`, `if not phase.planned`). The run keeps running with nothing to plan - as a scenario with no
    line at all does."""
    lines = [("B", "A", "1000.00", "active"), ("C", "A", "1000.00", "active")]  # only A pays
    eq, people = await world(factory, ["A", "B", "C"], lines, [])
    pid = {r: p.pid for r, p in people.items()}
    scenario = scenario_for(eq, people, lines, {"enabled": False})
    scenario["events"] = [{"type": "inject", "time": FREEZE_AT_MS,
                           "effects": [{"op": "freeze_participant", "participant_id": pid["A"]}]}]
    run = run_for(list(people.values()), eq.code)
    run.intensity_percent = 100
    run._scenario_raw = scenario
    run._edges_by_equivalent = {eq.code: [(pid[c], pid[d]) for c, d, _l, _s in lines]}
    runner = runner_for(run, scenario, clearing_every=10_000)
    runner._real_enable_inject = True
    install_tick_stand(monkeypatch, factory)

    await ticks(runner, run, 2)  # the second reaches 3000 and applies the freeze
    seen = len(runner._sse.events)
    await ticks(runner, run, 8)  # far past STALL_CLEARING_AFTER_TICKS + 1
    assert (run.state, run.pause_reason, run._real_consec_all_rejected_ticks) == ("running", None, 0), (
        run.state, run.pause_reason, run._real_consec_all_rejected_ticks)
    after = _payments(runner._sse.events[seen:], pid)
    assert after == [], f"actual: {len(after)} payments of frozen A published after the freeze: {sorted(set(after))}"
    assert runner._real_payment_planner.candidates_from_scenario(scenario) == []
