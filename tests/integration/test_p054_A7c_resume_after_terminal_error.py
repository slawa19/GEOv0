"""054-A-7c: "no second chance" - `resume` refuses a run that failed terminally (owner, 2026-10-30).

Defect (before 054-A-7c): `resume` returned ANY run in `error` to `running`, a run its own error budget had stopped
included - it then ran the same thing again until the budget stopped it again.

Target: `POST /runs/{id}/resume` of a run in `error` whose `last_error.code` is `REAL_MODE_TOO_MANY_ERRORS`,
`REAL_MODE_TOO_MANY_TIMEOUTS`, `REAL_MODE_TICK_FAILED_REPEATED`, `REAL_MODE_MONEY_NO_PROGRESS` or `INTEGRITY_VIOLATION`
answers 409 (`details.reason = run_failed_terminal`, `details.code` = that code) and leaves the run as it was;
`restart` still works. `HEARTBEAT_FAILED` (and any other code) is resumed as before.

Stand: `POST /simulator/runs` (scenario `minimal`), real PostgreSQL (mode B), the runtime's own real-mode tick with the
heartbeat held. How each run fails:
* `INTEGRITY_VIOLATION` - REAL path, as in `test_p054_A7b_integrity_violation_is_an_error.py`: a wrong writer lowers the
  creditor's line inside the payment, the core's own trust-limit check refuses it, the executor stops the run.
* `REAL_MODE_TOO_MANY_ERRORS` - the run's REAL error budget (`run_payments_phase`, limit set to 1 on the runner, which
  reads `SIMULATOR_REAL_MAX_ERRORS_TOTAL` once at construction) stops it on real classified payment errors; the errors
  themselves are INJECTED: `PaymentService.create_payment_internal_staged` raises a non-application exception, the
  `INTERNAL_ERROR` class. No real internal failure of the money path is reachable without injecting one (054-C-5).
* `HEARTBEAT_FAILED` - INJECTED: the runtime's real failure handler of a heartbeat iteration (`_heartbeat_failed`) is
  given an exception, as `tests/unit/test_p034_s1b_heartbeat_failure_is_not_silent.py` does through the loop.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

import app.core.ledger.book as book_module
from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B

pytestmark = MODE_B

EQ = "UAH"


def _admin() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


def _ids(run_id: str) -> dict[str, str]:
    raw = runtime.get_run(run_id)._scenario_raw or {}
    template = runtime.get_scenario(runtime.get_run(run_id).scenario_id).raw
    return {str(src["id"]): str(bound["id"]) for src, bound in zip(template["participants"], raw["participants"])}


async def _seeded_run(client, monkeypatch) -> str:
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)

    async def held(_run_id: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime._run_lifecycle, "_heartbeat_loop", held)
    created = await client.post(
        "/api/v1/simulator/runs", headers=_admin(), json={"scenario_id": "minimal", "mode": "real", "intensity_percent": 100}
    )
    assert created.status_code == 200, created.text
    run_id = str(created.json()["run_id"])
    ids = _ids(run_id)
    seeded = await client.post(
        f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
        headers=_admin(),
        json={"from_pid": ids["p2"], "to_pid": ids["p1"], "equivalent": EQ, "amount": "5"},
    )
    assert seeded.status_code == 200, seeded.text
    return run_id


async def _tick(run_id: str) -> None:
    run = runtime.get_run(run_id)
    run.tick_index, run.sim_time_ms = 1, 1000
    await runtime._real_runner.tick_real_mode(run_id)


async def _fail_by_the_error_budget(run_id: str, monkeypatch) -> None:
    monkeypatch.setattr(runtime._real_runner, "_real_max_errors_total_limit", 1)

    async def internal_failure(self, *args, **kwargs):
        raise RuntimeError("p054-A-7c: an internal failure of the payment")

    with monkeypatch.context() as mp:
        mp.setattr(PaymentService, "create_payment_internal_staged", internal_failure)
        await _tick(run_id)


async def _fail_by_a_violated_invariant(run_id: str, monkeypatch) -> None:
    original = book_module._apply_payment_flow

    async def apply_then_lower_the_creditors_line(session, flow):
        applied = await original(session, flow)
        await session.execute(
            text(
                'UPDATE trust_lines SET "limit" = 1 WHERE from_participant_id = :creditor '
                "AND to_participant_id = :debtor AND equivalent_id = :eq"
            ),
            {"creditor": flow.to_id, "debtor": flow.from_id, "eq": flow.equivalent_id},
        )
        return applied

    with monkeypatch.context() as mp:
        mp.setattr(book_module, "_apply_payment_flow", apply_then_lower_the_creditors_line)
        PaymentRouter.invalidate_cache(EQ)
        await _tick(run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "fail"),
    [
        ("REAL_MODE_TOO_MANY_ERRORS", _fail_by_the_error_budget),
        ("INTEGRITY_VIOLATION", _fail_by_a_violated_invariant),
    ],
    ids=["error-budget", "integrity-violation"],
)
async def test_resume_refuses_a_run_that_failed_terminally_and_restart_still_runs_it(
    client, db_session, monkeypatch, code, fail
):
    run_id = await _seeded_run(client, monkeypatch)
    run = runtime.get_run(run_id)
    try:
        await fail(run_id, monkeypatch)
        assert run.state == "error" and (run.last_error or {}).get("code") == code, (run.state, run.last_error)
        before = (run.state, dict(run.last_error), run.errors_total, run.stopped_at, run._heartbeat_task)

        refused = await client.post(f"/api/v1/simulator/runs/{run_id}/resume", headers=_admin())
        assert refused.status_code == 409, refused.text
        details = refused.json()["error"]["details"]
        assert (details["reason"], details["code"]) == ("run_failed_terminal", code), refused.json()
        assert (run.state, dict(run.last_error), run.errors_total, run.stopped_at, run._heartbeat_task) == before, (
            "a refused resume must leave the run as it was"
        )

        restarted = await client.post(f"/api/v1/simulator/runs/{run_id}/restart", headers=_admin())
        assert restarted.status_code == 200, restarted.text
        assert restarted.json()["state"] == "running" and restarted.json()["last_error"] is None, restarted.json()
    finally:
        await runtime.stop(run_id)


@pytest.mark.asyncio
async def test_resume_still_returns_a_run_whose_heartbeat_failed(client, db_session, monkeypatch):
    run_id = await _seeded_run(client, monkeypatch)
    run = runtime.get_run(run_id)
    try:
        await runtime._heartbeat_failed(run_id, RuntimeError("p054-A-7c: the heartbeat iteration failed"))
        assert run.state == "error" and run.last_error["code"] == "HEARTBEAT_FAILED", (run.state, run.last_error)

        resumed = await client.post(f"/api/v1/simulator/runs/{run_id}/resume", headers=_admin())
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "running", resumed.json()
    finally:
        await runtime.stop(run_id)
