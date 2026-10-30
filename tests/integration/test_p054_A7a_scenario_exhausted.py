"""054-A-7a: a run whose automatic payments are all refused clears at once, then pauses itself (owner, 2026-10-30).

A refused payment is an outcome of the scenario, not an error of the run. Before 054-A-7a such a run kept ticking and
only grew `consec_all_rejected_ticks` (a warning every fifth tick, a UI plaque "try clearing"). Now: at three attempted
all-refused ticks in a row the tick runs the clearing pass out of its cadence, and when the next attempted tick is
refused whole again the run goes to `paused` with `pause_reason = "scenario_exhausted"` - in `GET /runs/{id}` and in the
SSE `run_status`. Interact actions still work on it; `resume` clears the counter and the reason.

Real path: `POST /simulator/runs` (scenario `minimal`, line p1 -> p2, limit 1000), real PostgreSQL (mode B), the
runtime's own real-mode tick (`tick_real_mode`), its planner, executor and payment core, the clearing runner and the
HTTP routes. Two interventions, neither substitutes a result:
* the heartbeat is held (`_heartbeat_loop`), and the test drives the ticks itself with chosen tick numbers - none of
  them on the clearing cadence (25), so a clearing pass seen at tick 3 is the stall's, not the schedule's;
* the creditor lowers the line in the database to the debt it already secures, OUTSIDE the run (an UPDATE of the row,
  as any writer other than the run would). The run's scenario still says 1000, so its capacity-aware planner keeps
  planning payments p2 -> p1, and the core refuses every one of them for capacity - the stall this is about.
`run_clearing_pass` is wrapped by a recorder that calls the real pass and returns its result unchanged.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy import text

import app.core.clearing.runner as clearing_runner
from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B, sessionmaker_of

pytestmark = MODE_B

SCENARIO = "minimal"
EQ = "UAH"


def _admin() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


def _ids(run_id: str) -> dict[str, str]:
    raw = runtime.get_run(run_id)._scenario_raw or {}
    template = runtime.get_scenario(runtime.get_run(run_id).scenario_id).raw
    return {str(src["id"]): str(bound["id"]) for src, bound in zip(template["participants"], raw["participants"])}


async def _start(client, monkeypatch) -> str:
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr("app.config.settings.CLEARING_ENABLED", True)

    async def held(_run_id: str) -> None:  # the test ticks; nothing ticks behind it
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime._run_lifecycle, "_heartbeat_loop", held)
    r = await client.post(
        "/api/v1/simulator/runs",
        headers=_admin(),
        json={"scenario_id": SCENARIO, "mode": "real", "intensity_percent": 100},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["run_id"])


async def _pay(client, run_id: str, frm: str, to: str, amount: str):
    ids = _ids(run_id)
    return await client.post(
        f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
        headers=_admin(),
        json={"from_pid": ids[frm], "to_pid": ids[to], "equivalent": EQ, "amount": amount},
    )


async def _lower_the_line_to_its_debt(db_session, run_id: str) -> None:
    ids = _ids(run_id)
    async with sessionmaker_of(db_session)() as s:
        changed = await s.execute(
            text(
                'UPDATE trust_lines SET "limit" = 1 WHERE from_participant_id = '
                "(SELECT id FROM participants WHERE pid = :creditor) AND to_participant_id = "
                "(SELECT id FROM participants WHERE pid = :debtor)"
            ),
            {"creditor": ids["p1"], "debtor": ids["p2"]},
        )
        assert changed.rowcount == 1, "premise: the scenario's line p1 -> p2 is in the database"
        await s.commit()
    PaymentRouter.invalidate_cache(EQ)


def _record_clearing_passes(monkeypatch, run_id: str) -> list[int]:
    run = runtime.get_run(run_id)
    seen: list[int] = []
    original = clearing_runner.run_clearing_pass

    async def recorded(*args, **kwargs):
        seen.append(int(run.tick_index))
        return await original(*args, **kwargs)

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", recorded)
    return seen


async def _tick(run_id: str, index: int) -> None:
    run = runtime.get_run(run_id)
    run.tick_index = index
    run.sim_time_ms = index * 1000
    await runtime._real_runner.tick_real_mode(run_id)


def _last_run_status(run_id: str) -> dict:
    statuses = [e[3] for e in runtime.get_run(run_id)._event_buffer if e[3].get("type") == "run_status"]
    assert statuses, "no run_status was published"
    return statuses[-1]


@pytest.mark.asyncio
async def test_all_refused_ticks_clear_at_once_and_then_pause_the_run(client, db_session, monkeypatch, caplog):
    run_id = await _start(client, monkeypatch)
    try:
        seeded = await _pay(client, run_id, "p2", "p1", "1")  # seeds the scenario; p2 owes p1 1.00
        assert seeded.status_code == 200, seeded.text
        await _lower_the_line_to_its_debt(db_session, run_id)
        passes = _record_clearing_passes(monkeypatch, run_id)
        run = runtime.get_run(run_id)
        # The ticks below are off the cadence: a pass at tick 3 can only be the stall's.
        assert int(runtime._real_runner._tick._clearing_every_n_ticks) == 25

        with caplog.at_level(logging.INFO):
            for index in (1, 2):
                await _tick(run_id, index)
            # NON-VACUITY: the planner planned and the core refused every one of them, nothing failed.
            assert run.attempts_total > 0 and run.committed_total == 0 and run.errors_total == 0, (
                run.attempts_total, run.committed_total, run.errors_total, run.last_error)
            assert run.rejected_total == run.attempts_total
            assert run._real_consec_all_rejected_ticks == 2 and passes == [] and run.state == "running"

            await _tick(run_id, 3)  # the third all-refused tick: the clearing runs now, out of the cadence (25)
            assert passes == [3], f"clearing passes by tick actual={passes} expected=[3]"
            assert run.state == "running" and run.pause_reason is None

            await _tick(run_id, 4)  # refused whole again after the clearing: the scenario is exhausted
        assert run.state == "paused", (run.state, run._real_consec_all_rejected_ticks, run.last_error)
        assert run.pause_reason == "scenario_exhausted"
        assert passes == [3], passes
        assert [r.levelno for r in caplog.records if "simulator.run.scenario_exhausted" in r.getMessage()] == [
            logging.WARNING
        ]

        got = await client.get(f"/api/v1/simulator/runs/{run_id}", headers=_admin())
        assert got.status_code == 200, got.text
        assert (got.json()["state"], got.json()["pause_reason"]) == ("paused", "scenario_exhausted"), got.json()
        status = _last_run_status(run_id)
        assert (status["state"], status["pause_reason"]) == ("paused", "scenario_exhausted"), status
        assert status["consec_all_rejected_ticks"] == 4, status

        # Manual payments still work on the paused run: p1 pays p2 back along the debt.
        manual = await _pay(client, run_id, "p1", "p2", "1")
        assert manual.status_code == 200, manual.text
        assert str(manual.json()["status"]).upper() == "COMMITTED", manual.json()

        resumed = await client.post(f"/api/v1/simulator/runs/{run_id}/resume", headers=_admin())
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "running" and resumed.json()["pause_reason"] is None, resumed.json()
        assert resumed.json()["consec_all_rejected_ticks"] is None, resumed.json()
        assert run._real_consec_all_rejected_ticks == 0 and run._real_stall_clearing_at is None
        status = _last_run_status(run_id)
        assert (status["state"], status["pause_reason"]) == ("running", None), status
    finally:
        await runtime.stop(run_id)
    assert runtime.get_run(run_id).pause_reason is None


@pytest.mark.asyncio
async def test_a_user_pause_has_no_reason_and_stop_clears_the_reason(client, db_session, monkeypatch):
    run_id = await _start(client, monkeypatch)
    try:
        paused = await client.post(f"/api/v1/simulator/runs/{run_id}/pause", headers=_admin())
        assert paused.status_code == 200, paused.text
        assert (paused.json()["state"], paused.json()["pause_reason"]) == ("paused", None), paused.json()
        run = runtime.get_run(run_id)
        run._real_consec_all_rejected_ticks = 2  # a run of refusals a user's resume must not forget
        resumed = await client.post(f"/api/v1/simulator/runs/{run_id}/resume", headers=_admin())
        assert resumed.status_code == 200, resumed.text
        assert run._real_consec_all_rejected_ticks == 2, "only a pause for an exhausted scenario resets the counter"
        run.state, run.pause_reason = "paused", "scenario_exhausted"
        stopped = await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=_admin())
        assert stopped.status_code == 200, stopped.text
        assert (stopped.json()["state"], stopped.json()["pause_reason"]) == ("stopped", None), stopped.json()
    finally:
        await runtime.stop(run_id)


@pytest.mark.asyncio
async def test_control_a_run_whose_payments_go_through_does_not_pause(client, db_session, monkeypatch):
    """Anti-vacuum: the same stand without the lowered line - the payments commit, no stall clearing, no pause."""

    run_id = await _start(client, monkeypatch)
    try:
        seeded = await _pay(client, run_id, "p2", "p1", "1")
        assert seeded.status_code == 200, seeded.text
        passes = _record_clearing_passes(monkeypatch, run_id)
        run = runtime.get_run(run_id)
        for index in (1, 2, 3, 4, 5):
            await _tick(run_id, index)
        assert run.committed_total > 1, (run.committed_total, run.rejected_total, run.last_error)
        assert run._real_consec_all_rejected_ticks == 0
        assert (run.state, run.pause_reason, passes) == ("running", None, [])
    finally:
        await runtime.stop(run_id)
