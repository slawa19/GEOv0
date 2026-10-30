"""054-A-7b: a payment refused for a violated integrity invariant is an error that stops the run, not a refusal.

Defect (before 054-A-7b): `IntegrityViolationException` answers 409, and the real-mode executor classified it by that
status - a REJECTED payment with the generic code `PAYMENT_REJECTED`, counted in `rejected_total` like any refusal of the
scenario. The run kept ticking, `last_error` said nothing, and the log had no line naming the invariant.

Target: `tx.failed.error.code = INTEGRITY_VIOLATION`, counted in `errors`, `last_error.code = INTEGRITY_VIOLATION`, one
ERROR line with the run, the payment and the invariant, and the run stopped at once (`fail_run`, state `error`) - the
tick's money phase rolled back whole.

Real path: `POST /simulator/runs` (scenario `minimal`, line p1 -> p2), real PostgreSQL (mode B), the runtime's own
real-mode tick, its planner, executor and payment core, and the core's REAL trust-limit check
(`InvariantChecker.check_debt_growth`, `TRUST_LIMIT_VIOLATION`). The violation is made the way
`test_p019_direct_execution_effects_postgres.py` makes it: the book's flow writer is wrapped so that, inside the
payment's own transaction and after the flow is applied, the creditor's line is lowered below the debt it now secures
- a wrong writer, not an injected exception; the check that refuses it is the production one. The heartbeat is held and
the test drives the tick itself.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import pytest
from sqlalchemy import text

import app.core.ledger.book as book_module
from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B, sessionmaker_of

pytestmark = MODE_B

EQ = "UAH"


def _admin() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


def _ids(run_id: str) -> dict[str, str]:
    raw = runtime.get_run(run_id)._scenario_raw or {}
    template = runtime.get_scenario(runtime.get_run(run_id).scenario_id).raw
    return {str(src["id"]): str(bound["id"]) for src, bound in zip(template["participants"], raw["participants"])}


async def _line_and_debt(db_session, run_id: str) -> tuple[Decimal, Decimal]:
    ids = _ids(run_id)
    async with sessionmaker_of(db_session)() as s:
        params = {"creditor": ids["p1"], "debtor": ids["p2"]}
        limit = await s.scalar(
            text(
                'SELECT "limit" FROM trust_lines WHERE from_participant_id = (SELECT id FROM participants WHERE pid = '
                ":creditor) AND to_participant_id = (SELECT id FROM participants WHERE pid = :debtor)"
            ),
            params,
        )
        debt = await s.scalar(
            text(
                "SELECT amount FROM debts WHERE creditor_id = (SELECT id FROM participants WHERE pid = :creditor) "
                "AND debtor_id = (SELECT id FROM participants WHERE pid = :debtor)"
            ),
            params,
        )
    return Decimal(str(limit)), Decimal(str(debt or 0))


@pytest.mark.asyncio
async def test_a_violated_invariant_in_a_tick_payment_stops_the_run_as_an_error(client, db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)

    async def held(_run_id: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime._run_lifecycle, "_heartbeat_loop", held)
    created = await client.post(
        "/api/v1/simulator/runs", headers=_admin(), json={"scenario_id": "minimal", "mode": "real", "intensity_percent": 100}
    )
    assert created.status_code == 200, created.text
    run_id = str(created.json()["run_id"])
    run = runtime.get_run(run_id)
    try:
        ids = _ids(run_id)
        seeded = await client.post(
            f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
            headers=_admin(),
            json={"from_pid": ids["p2"], "to_pid": ids["p1"], "equivalent": EQ, "amount": "5"},
        )
        assert seeded.status_code == 200, seeded.text
        before = await _line_and_debt(db_session, run_id)
        assert before == (Decimal("1000"), Decimal("5")), before

        original = book_module._apply_payment_flow
        corrupted: list[int] = []

        async def apply_then_lower_the_creditors_line(session, flow):
            applied = await original(session, flow)
            await session.execute(
                text(
                    'UPDATE trust_lines SET "limit" = 1 WHERE from_participant_id = :creditor '
                    "AND to_participant_id = :debtor AND equivalent_id = :eq"
                ),
                {"creditor": flow.to_id, "debtor": flow.from_id, "eq": flow.equivalent_id},
            )
            corrupted.append(1)
            return applied

        monkeypatch.setattr(book_module, "_apply_payment_flow", apply_then_lower_the_creditors_line)
        PaymentRouter.invalidate_cache(EQ)
        run.tick_index, run.sim_time_ms = 1, 1000
        with caplog.at_level(logging.INFO):
            await runtime._real_runner.tick_real_mode(run_id)

        # NON-VACUITY: the corruption ran inside a payment of the tick, so the core's check had something to refuse.
        assert corrupted, "premise: no payment of the tick reached the book"
        failed = [e[3] for e in run._event_buffer if e[3].get("type") == "tx.failed"]
        assert failed, "the refused payment published no tx.failed"
        codes = [f["error"]["code"] for f in failed]
        assert codes[0] == "INTEGRITY_VIOLATION", f"tx.failed codes actual={codes} expected INTEGRITY_VIOLATION first"
        assert (failed[0]["error"].get("details") or {}).get("details", {}).get("invariant") == "TRUST_LIMIT_VIOLATION"
        assert run.state == "error", (run.state, run.last_error)
        assert (run.last_error or {}).get("code") == "INTEGRITY_VIOLATION", run.last_error
        assert run.errors_total >= 1 and run.rejected_total == 0, (run.errors_total, run.rejected_total)

        # One ERROR line per payment refused for the invariant (the tick's other planned payments ran too - each one
        # met the same corruption - and the whole phase is rolled back below), each naming run, payment and invariant.
        lines = [r.getMessage() for r in caplog.records if "simulator.real.payment_integrity_violation" in r.getMessage()]
        assert lines and len(lines) == codes.count("INTEGRITY_VIOLATION"), (len(lines), codes)
        assert {r.levelno for r in caplog.records if "simulator.real.payment_integrity_violation" in r.getMessage()} == {
            logging.ERROR
        }
        for message in lines:
            assert f"run_id={run_id}" in message and "invariant=TRUST_LIMIT_VIOLATION" in message, message
            assert "tx_id=sim:" in message, message

        # The tick's money phase is rolled back whole: neither the corrupted line nor any payment of the tick survives.
        assert await _line_and_debt(db_session, run_id) == before

        got = await client.get(f"/api/v1/simulator/runs/{run_id}", headers=_admin())
        assert got.status_code == 200, got.text
        assert (got.json()["state"], got.json()["last_error"]["code"]) == ("error", "INTEGRITY_VIOLATION"), got.json()
    finally:
        await runtime.stop(run_id)


async def _read_run_stream(run_id: str, leave: asyncio.Event) -> str:
    """The run's live HTTP SSE stream through the ASGI app, until it returns by itself (as in
    `test_p054_A_stop_publication_order.py`: the recording `client` would put a `text/event-stream` body under the
    session-wide conformance check)."""

    from app.main import app

    path = f"/api/v1/simulator/runs/{run_id}/events"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": f"equivalent={EQ}".encode(),
        "root_path": "",
        "headers": [(b"x-admin-token", settings.ADMIN_TOKEN.encode()), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 50004),
        "server": ("test", 80),
    }
    body = bytearray()
    status: list[int] = []

    async def receive() -> dict:
        await leave.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            status.append(int(message["status"]))
        elif message["type"] == "http.response.body":
            body.extend(message.get("body") or b"")

    await app(scope, receive, send)
    assert status == [200], status
    return body.decode("utf-8")


@pytest.mark.asyncio
async def test_the_live_stream_gets_the_ticks_tx_failed_before_the_runs_error(client, db_session, monkeypatch):
    """054-A review FD3: `fail_run` was called INSIDE the money phase, so `run_status(error)` was published before the
    rollback resolved the phase's observations: the live stream (which returns on `error`) never got the
    `tx.failed INTEGRITY_VIOLATION` of its own tick, and `errors_total` in the `error` event lacked the tick's errors.
    Target: the stop's `run_status(error)` follows every `tx.failed` of the tick and counts its errors; `last_error`
    keeps the stop's code (054-A-7c)."""

    import json

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)

    async def held(_run_id: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime._run_lifecycle, "_heartbeat_loop", held)
    created = await client.post(
        "/api/v1/simulator/runs", headers=_admin(), json={"scenario_id": "minimal", "mode": "real", "intensity_percent": 100}
    )
    assert created.status_code == 200, created.text
    run_id = str(created.json()["run_id"])
    run = runtime.get_run(run_id)
    leave = asyncio.Event()
    try:
        ids = _ids(run_id)
        seeded = await client.post(
            f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
            headers=_admin(),
            json={"from_pid": ids["p2"], "to_pid": ids["p1"], "equivalent": EQ, "amount": "5"},
        )
        assert seeded.status_code == 200, seeded.text
        errors_before = int(run.errors_total)

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

        stream = asyncio.create_task(_read_run_stream(run_id, leave))
        async with asyncio.timeout(30):
            while not run._subs:
                await asyncio.sleep(0.01)
        run.tick_index, run.sim_time_ms = 1, 1000
        await runtime._real_runner.tick_real_mode(run_id)
        body = await asyncio.wait_for(stream, timeout=30)
        state_after_tick, last_error_after_tick = run.state, dict(run.last_error or {})
    finally:
        leave.set()
        await runtime.stop(run_id)

    streamed = []
    for block in body.split("\n\n"):
        data = [ln[len("data:"):].strip() for ln in block.splitlines() if ln.startswith("data:")]
        if data:
            streamed.append(json.loads("\n".join(data)))
    kinds = [e.get("type") + ("/" + str(e.get("state")) if e.get("type") == "run_status" else "") for e in streamed]
    print("054-A FD3 streamed:", kinds)
    failed = [e for e in streamed if e.get("type") == "tx.failed"]
    tick_errors = [e for e in failed if e["error"]["code"] == "INTEGRITY_VIOLATION"]
    assert tick_errors, f"the live stream got no tx.failed INTEGRITY_VIOLATION before it ended: {kinds}"
    assert streamed[-1].get("type") == "run_status" and streamed[-1].get("state") == "error", kinds
    error_event = streamed[-1]
    # Every error the tick resolved is in the `error` event's count, plus the stop itself (`fail_run` counts it).
    assert error_event["errors_total"] == errors_before + len(failed) + 1, (error_event["errors_total"], errors_before, kinds)
    assert error_event["last_error"]["code"] == "INTEGRITY_VIOLATION", error_event["last_error"]
    assert (state_after_tick, last_error_after_tick.get("code")) == ("error", "INTEGRITY_VIOLATION"), last_error_after_tick
