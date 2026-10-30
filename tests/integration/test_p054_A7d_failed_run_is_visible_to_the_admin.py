"""054-A-7d: why a run failed or paused itself is visible to the administrator - in the run list and in the log.

Defects (before 054-A-7d): `GET /simulator/admin/runs` listed state and counters but neither `last_error` nor (since
054-A-7a) `pause_reason`, so the administrator's list could not say why a run stopped; and `fail_run`, the one way a
run is stopped as failed, wrote no log line at all (nor did the fallback of a failed heartbeat that stops the run
without it) - a failed run was found in the log only through whatever its cause had logged, if anything.

Target: the list's items carry `last_error` and `pause_reason` as `GET /runs/{id}` does, and every stop as failed writes
one ERROR line `simulator.run.failed run_id=<id> code=<code> errors_total=<n>` (no message, no exception text).

Stand: `POST /simulator/runs` (scenario `minimal`), real PostgreSQL (mode B), heartbeat held, tick driven by the test.
* The failed run is stopped by the REAL path of `test_p054_A7b_integrity_violation_is_an_error.py` (a wrong writer
  lowers the creditor's line inside the payment; the core's own trust-limit check refuses it; `fail_run`).
* The heartbeat-fallback line is reached by INJECTION: `fail_run` itself raises inside the runtime's real
  `_heartbeat_failed` (the branch exists for exactly that), and the handler is given an exception.
* The paused run is paused by the real transition of an exhausted scenario (`RealTick._pause_scenario_exhausted`),
  called directly; the path that leads to it through ticks is `test_p054_A7a_scenario_exhausted.py`.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy import text

import app.core.ledger.book as book_module
from app.config import settings
from app.core.payments.router import PaymentRouter
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


async def _run(client, monkeypatch) -> str:
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)

    async def held(_run_id: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime._run_lifecycle, "_heartbeat_loop", held)
    created = await client.post(
        "/api/v1/simulator/runs", headers=_admin(), json={"scenario_id": "minimal", "mode": "real", "intensity_percent": 100}
    )
    assert created.status_code == 200, created.text
    return str(created.json()["run_id"])


async def _listed(client, run_id: str) -> dict:
    listed = await client.get("/api/v1/simulator/admin/runs", headers=_admin(), params={"limit": 200})
    assert listed.status_code == 200, listed.text
    items = [item for item in listed.json()["items"] if item["run_id"] == run_id]
    assert len(items) == 1, listed.json()
    return items[0]


def _failed_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("simulator.run.failed ")]


@pytest.mark.asyncio
async def test_a_failed_run_is_listed_with_its_code_and_logged_once(client, db_session, monkeypatch, caplog):
    run_id = await _run(client, monkeypatch)
    run = runtime.get_run(run_id)
    try:
        ids = _ids(run_id)
        seeded = await client.post(
            f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
            headers=_admin(),
            json={"from_pid": ids["p2"], "to_pid": ids["p1"], "equivalent": EQ, "amount": "5"},
        )
        assert seeded.status_code == 200, seeded.text
        listed = await _listed(client, run_id)
        assert (listed["state"], listed["last_error"], listed["pause_reason"]) == ("running", None, None), listed

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

        monkeypatch.setattr(book_module, "_apply_payment_flow", apply_then_lower_the_creditors_line)
        PaymentRouter.invalidate_cache(EQ)
        run.tick_index, run.sim_time_ms = 1, 1000
        with caplog.at_level(logging.INFO):
            await runtime._real_runner.tick_real_mode(run_id)
        assert run.state == "error", (run.state, run.last_error)

        listed = await _listed(client, run_id)
        got = await client.get(f"/api/v1/simulator/runs/{run_id}", headers=_admin())
        assert listed["state"] == "error" and listed["last_error"]["code"] == "INTEGRITY_VIOLATION", listed
        assert listed["last_error"] == got.json()["last_error"], (listed, got.json())
        assert listed["pause_reason"] is None

        lines = _failed_lines(caplog)
        assert [r.levelno for r in lines] == [logging.ERROR], [r.getMessage() for r in lines]
        assert lines[0].getMessage() == (
            f"simulator.run.failed run_id={run_id} code=INTEGRITY_VIOLATION errors_total={lines[0].args[2]}"
        )
        assert int(lines[0].args[2]) >= 1
    finally:
        await runtime.stop(run_id)


@pytest.mark.asyncio
async def test_a_heartbeat_failure_that_stops_the_run_without_fail_run_is_logged_too(client, db_session, monkeypatch, caplog):
    run_id = await _run(client, monkeypatch)
    run = runtime.get_run(run_id)
    try:
        async def fail_run_raises(*_a, **_kw):
            raise RuntimeError("p054-A-7d: fail_run could not even start")

        monkeypatch.setattr(runtime._real_runner, "fail_run", fail_run_raises)
        with caplog.at_level(logging.INFO):
            await runtime._heartbeat_failed(run_id, RuntimeError("p054-A-7d: the iteration failed"))
        assert run.state == "error" and run.last_error["code"] == "HEARTBEAT_FAILED", (run.state, run.last_error)
        lines = _failed_lines(caplog)
        assert [(r.levelno, r.args[1]) for r in lines] == [(logging.ERROR, "HEARTBEAT_FAILED")], [
            r.getMessage() for r in lines
        ]
        assert "p054-A-7d" not in lines[0].getMessage()
        assert (await _listed(client, run_id))["last_error"]["code"] == "HEARTBEAT_FAILED"
    finally:
        await runtime.stop(run_id)


@pytest.mark.asyncio
async def test_a_run_that_paused_itself_is_listed_with_its_reason(client, db_session, monkeypatch):
    run_id = await _run(client, monkeypatch)
    run = runtime.get_run(run_id)
    try:
        await runtime._real_runner._tick._pause_scenario_exhausted(run_id, run)
        listed = await _listed(client, run_id)
        assert (listed["state"], listed["pause_reason"], listed["last_error"]) == (
            "paused", "scenario_exhausted", None), listed
    finally:
        await runtime.stop(run_id)
