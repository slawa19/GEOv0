"""054-E-13 / 054-A-16 (the API's half): the simulator's `equivalent` query is one code whatever its case.

The routes passed the query on as typed. `uah` was echoed in snapshot and preview bodies (the canon's
`EquivalentCode` is `^[A-Z0-9_]{1,16}$`), and an SSE subscription opened with `uah` never received a domain event:
the events carry `UAH`. The routes now normalise the code on the way in (`.strip().upper()`).

Real path: real HTTP through the app on PostgreSQL; the SSE stream is the ASGI application itself, a real run, a
real `payment-real`. The body is checked against the canon with `tests/contract/openapi_response_conformance.py`.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest

from app.config import settings
from app.core.simulator.runtime import runtime
from app.main import app
from tests.conftest import MODE_B
from tests.contract import openapi_response_conformance as oc
from tests.p042_support import EQUIVALENT, SCENARIO

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
_CANON = oc.load_canon()
RUN_SCENARIO = "riverside-town-50-realistic-v2"


def _schema_errors(method, path, status, response):
    verdict, pointer = oc.classify_response_declaration(
        _CANON, method, path, status, response.headers.get("content-type")
    )
    assert verdict == oc.DECL_SCHEMA, verdict
    return oc.validate_body(_CANON, pointer, response.json())


@pytest.mark.asyncio
async def test_p054_E_13_preview_with_a_lowercase_equivalent_answers_the_canonical_code(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.SIMULATOR_SCENARIO_ALLOWLIST", SCENARIO)
    response = await client.get(
        f"/api/v1/simulator/scenarios/{SCENARIO}/graph/preview",
        headers=ADMIN,
        params={"equivalent": f" {EQUIVALENT.lower()} "},
    )
    assert response.status_code == 200, response.text
    errors = _schema_errors("GET", "/simulator/scenarios/{scenario_id}/graph/preview", 200, response)
    print("054-E-13 preview equivalent:", response.json().get("equivalent"), "canon errors:", errors[:3])
    assert not errors, errors
    assert response.json()["equivalent"] == EQUIVALENT


@pytest.mark.asyncio
async def test_p054_E_13_active_snapshot_without_a_run_answers_the_canonical_code(client):
    response = await client.get("/api/v1/simulator/graph/snapshot", headers=ADMIN, params={"equivalent": "uah"})
    assert response.status_code == 200, response.text
    errors = _schema_errors("GET", "/simulator/graph/snapshot", 200, response)
    assert not errors, errors
    assert response.json()["equivalent"] == "UAH"


async def _start_paused(client) -> str:
    r = await client.post(
        "/api/v1/simulator/runs",
        headers=ADMIN,
        json={"scenario_id": RUN_SCENARIO, "mode": "real", "intensity_percent": 0},
    )
    assert r.status_code == 200, r.text
    run_id = str(r.json()["run_id"])
    observer = await runtime.subscribe(run_id, equivalent="UAH")
    try:
        async with asyncio.timeout(120):
            while True:
                event = await observer.queue.get()
                if event.get("type") == "run_status" and event.get("state") == "running" and event.get("sim_time_ms", 0) >= 1000:
                    await runtime.pause(run_id)
                    break
    finally:
        await runtime.unsubscribe(run_id, observer)
    return run_id


def _scope(path: str, query: bytes) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": [(b"x-admin-token", settings.ADMIN_TOKEN.encode()), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 50002),
        "server": ("test", 80),
    }


@MODE_B
@pytest.mark.asyncio
async def test_p054_A_16_run_stream_opened_with_a_lowercase_equivalent_receives_domain_events(
    client, db_session, monkeypatch
):
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    run_id = await _start_paused(client)
    p1, p6 = f"{run_id}:p1", f"{run_id}:p6"  # line p6 -> p1 (creditor p6) with room: p1 can pay p6

    leave = anyio.Event()
    first_byte, got_tx = anyio.Event(), anyio.Event()
    body = bytearray()

    async def receive() -> dict:
        await leave.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            body.extend(message["body"])
            first_byte.set()
            if b'"type":"tx.updated"' in body:  # not run_status's `last_event_type`
                got_tx.set()

    stream_path = f"/api/v1/simulator/runs/{run_id}/events"
    pay_status: list[int] = []
    try:
        with anyio.fail_after(30):
            async with anyio.create_task_group() as tg:
                tg.start_soon(app, _scope(stream_path, b"equivalent=uah"), receive, send)
                await first_byte.wait()  # subscribed: the bootstrap frame arrived
                r = await client.post(
                    f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
                    headers=ADMIN,
                    json={"from_pid": p1, "to_pid": p6, "equivalent": "UAH", "amount": "1.00"},
                )
                pay_status.append(r.status_code)
                assert r.status_code == 200, r.text
                with anyio.move_on_after(10):
                    await got_tx.wait()
                leave.set()
    finally:
        leave.set()
    print("054-A-16 payment:", pay_status, "tx.updated seen:", got_tx.is_set(), "stream:", bytes(body)[:1200])
    assert got_tx.is_set(), f"a stream opened with equivalent=uah got no tx.updated within 10 s: {bytes(body)[:400]!r}"

    # The run's snapshot read with the same lowercase code answers the canonical one.
    snap = await client.get(f"/api/v1/simulator/runs/{run_id}/graph/snapshot", headers=ADMIN,
                            params={"equivalent": "uah"})
    assert snap.status_code == 200, snap.text
    assert not _schema_errors("GET", "/simulator/runs/{run_id}/graph/snapshot", 200, snap)
    assert snap.json()["equivalent"] == "UAH"
