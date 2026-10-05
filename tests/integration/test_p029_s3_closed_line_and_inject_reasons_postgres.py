"""029 S3: `F-029-11` (BACKLOG № 36, 37) and `F-029-13` (№ 276) - reproducers on a mode-B clone.

F-029-11: a line the ledger closed outside every patch of the run (another session, an API close) stayed in the
run's edge cache and scenario - counted by `active_trustlines` and offered to the payment planner - until some
operation of the run happened to rebuild a patch over that pair. A real tick now drops it, once.

F-029-13: a trust line an inject event could not create - a limit finer than the equivalent's step, or one the column
cannot hold - was only logged; the event's note now carries the reason, and `add_participant` counts its skipped lines.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.db.models.trustline import TrustLine
from tests.integration.test_p021_trust_drift_is_audited_postgres import (  # noqa: F401 - `factory` is a fixture
    factory,
    run_for,
    runner_for,
    scenario_for,
    ticks,
    world,
)
from tests.integration.test_p028_e3_freeze_boundary_postgres import _inject
from tests.simulator_tick_stand import install_tick_stand

LINES = [("A", "B", "100.00", "active"), ("A", "C", "100.00", "active")]


@pytest.mark.asyncio
async def test_a_tick_drops_a_line_closed_outside_the_run(factory, monkeypatch) -> None:  # noqa: F811
    eq, p = await world(factory, ["A", "B", "C"], LINES, [])
    scenario = scenario_for(eq, p, LINES, {"enabled": False})
    run = run_for(list(p.values()), eq.code)
    a, b, c = (p[k].pid for k in "ABC")
    run._scenario_raw, run._edges_by_equivalent = scenario, {eq.code: [(a, b), (a, c)]}
    runner = runner_for(run, scenario, clearing_every=10_000)
    install_tick_stand(monkeypatch, factory)
    async with factory() as s:  # closed by the ledger, past every patch of this run
        await s.execute(update(TrustLine).where(TrustLine.from_participant_id == p["A"].id,
                                                TrustLine.to_participant_id == p["B"].id).values(status="closed"))
        await s.commit()

    await ticks(runner, run, 2)

    assert run.state == "running" and run.errors_total == 0, (run.state, run.last_error)
    sample = run._real_last_tick_storage_payload["metric_values_by_eq"][eq.code]["active_trustlines"]
    candidates = {(t["receiver_pid"], t["sender_pid"])  # (creditor, debtor): a payment runs debtor -> creditor
                  for t in runner._real_payment_planner.candidates_from_scenario(run._scenario_raw)}
    removals = [e["payload"]["removed_edges"] for e in runner._sse.events
                if e.get("type") == "topology.changed" and e["payload"].get("removed_edges")]
    assert (sample, candidates, run._edges_by_equivalent[eq.code]) == (1.0, {(a, c)}, [(a, c)]), (
        f"after two ticks the closed {a} -> {b} is still in the run: active_trustlines {sample}, planner candidates "
        f"{sorted(candidates)}, cache {run._edges_by_equivalent[eq.code]}")  # and the live A -> C stays (control)
    assert [[(r["from_pid"], r["to_pid"]) for r in removed] for removed in removals] == [[(a, b)]], removals  # once


@pytest.mark.asyncio
async def test_the_inject_note_names_why_a_line_was_skipped(factory) -> None:  # noqa: F811
    eq, p = await world(factory, ["A", "B", "C", "D"], [], [])
    new = {"id": f"{p['A'].pid}_NEW", "name": "new"}
    effects = [
        {"op": "create_trustline", "from": p["D"].pid, "to": p["A"].pid, "equivalent": eq.code, "limit": "1.005"},
        {"op": "create_trustline", "from": p["D"].pid, "to": p["B"].pid, "equivalent": eq.code, "limit": "1e13"},
        {"op": "create_trustline", "from": p["D"].pid, "to": p["C"].pid, "equivalent": eq.code, "limit": "5.00"},
        {"op": "add_participant", "participant": new, "initial_trustlines": [
            {"sponsor": p["A"].pid, "equivalent": eq.code, "limit": "1.005"},
            {"sponsor": p["B"].pid, "equivalent": eq.code, "limit": "1e13"},
            {"sponsor": p["C"].pid, "equivalent": eq.code, "limit": "7.00"}]},
    ]
    runner, run, scenario, artifacts = _inject(eq, p, effects)
    async with factory() as s:
        await runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)
    stats = [e["scenario"]["stats"] for e in artifacts.events if e.get("type") == "note"]
    assert stats and stats[-1]["applied"] == 2, stats  # control: the storable line and the participant landed
    assert (stats[-1]["skipped"], stats[-1].get("skipped_reasons")) == (
        4, {"amount_precision_exceeded": 2, "money_magnitude": 2}), stats
