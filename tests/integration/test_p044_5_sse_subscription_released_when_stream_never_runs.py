"""BACKLOG 044-5: an SSE subscription the route opened is released when the response ends, even when the stream's
own `finally` never ran - otherwise the run keeps a subscriber nobody reads and is never stopped as idle.

WHY THE ROUTE SUBSCRIBES BEFORE STREAMING. `runtime.subscribe_with_status` validates the `Last-Event-ID` cursor and
queues replay + the status snapshot atomically; a cursor it cannot serve must be answered 410 before any byte of the
stream (`test_simulator_sse_replay_410.py`). So the subscription exists before the `StreamingResponse` runs, and its
release lived only in the `finally` of the generator `_run_events_stream`.

THE PATH (measured 2026-10-21 through the full ASGI app: CORS and both `@app.middleware("http")` layers, which are
Starlette `BaseHTTPMiddleware`). A client that disconnects one event-loop step after the request reached the app
makes the response's own disconnect listener cancel the body task at the `send` of the first chunk: the generator is
left suspended at its first `yield`, its `finally` does not run, and Starlette never closes the body iterator. The
subscription then stayed in `run._subs` until a cyclic garbage collection finalized the generator (refcount alone
does not free it; nothing deterministic does), and `_idle_expired_locked` - the heartbeat's idle predicate - read the
run as watched. Other delays of the disconnect (0, 2..7 loop steps) cancel the generator at an `await` inside its
`try`, and its `finally` releases the subscription itself.

HOW. The ASGI app is called directly, with a `receive` that answers `http.disconnect` after `delay` loop steps - the
only scripted part is WHEN the client leaves, which in production is arbitrary. The spy holds the stream's generator
by WEAK reference only, and the cyclic collector is disabled for the sweep: after the app call returns and
`SETTLE_STEPS` further loop steps (time for asyncio's async-generator finalizer to `aclose` anything refcount freed),
the test measures the run's subscriber list, whether the generator is still alive and unfinished (left open = the
window was hit, and it lasts; anti-vacuum: the sweep must hit it at least once) and the idle predicate on the
runtime's idle clock (replaced by a test clock; no sleep).

WHAT IT DOES NOT SEE: uvicorn's own protocol objects (its `receive` answers `http.disconnect` without suspending once
the connection is lost, like the scripted one here; its `send` returns silently after a disconnect); a response
that is never called at all (FastAPI 0.109 runs dependency teardown before the response is sent - a teardown that
raises would skip the response; not observed, no path built); the heartbeat's stop itself
(`tests/unit/test_p044_s2_idle_stop.py`).
"""

from __future__ import annotations

import gc
import weakref

import anyio
import pytest
from httpx import AsyncClient

import app.api.v1.simulator as simulator_api
from app.core.simulator.runtime import runtime
from app.main import app
from tests.p042_support import EQUIVALENT, no_real_tick, start_real_run  # noqa: F401 - `no_real_tick` is a fixture

IDLE_STOP_SEC = 5
DELAYS = range(0, 8)
SETTLE_STEPS = 20


def _scope(path: str, headers: dict[str, str]) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": f"equivalent={EQUIVALENT}".encode(),
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }


def _disconnecting_after(delay: int):
    async def receive() -> dict:
        for _ in range(delay):
            await anyio.sleep(0)
        return {"type": "http.disconnect"}

    return receive


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["run", "active"])
async def test_a_client_gone_before_the_stream_runs_leaves_no_subscriber(
    client: AsyncClient, auth_headers, no_real_tick, monkeypatch, endpoint  # noqa: F811
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(runtime, "_idle_now", lambda: clock["now"])
    monkeypatch.setattr(runtime, "_run_idle_stop_sec", IDLE_STOP_SEC)
    run_id = await start_real_run(client, auth_headers)

    subscribed: list[object] = []
    real_subscribe = runtime.subscribe_with_status

    async def subscribe_spy(*args, **kwargs):
        sub, status_event_id = await real_subscribe(*args, **kwargs)
        subscribed.append(sub)
        return sub, status_event_id

    generators: list[weakref.ref] = []
    real_stream = simulator_api._run_events_stream

    def stream_spy(**kwargs):
        gen = real_stream(**kwargs)  # the same generator object: no wrapper, no extra await
        generators.append(weakref.ref(gen))  # weak: the spy must not keep the generator alive
        return gen

    monkeypatch.setattr(runtime, "subscribe_with_status", subscribe_spy)
    monkeypatch.setattr(simulator_api, "_run_events_stream", stream_spy)
    path = f"/api/v1/simulator/runs/{run_id}/events" if endpoint == "run" else "/api/v1/simulator/events"
    run = runtime.get_run(run_id)
    left_open: list[int] = []
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    gc_was_enabled = gc.isenabled()
    gc.disable()  # only refcount frees objects during the sweep: a cyclic collection would hide the leak
    try:
        for delay in DELAYS:
            subscribed_before = len(subscribed)
            with anyio.fail_after(10):
                await app(_scope(path, dict(auth_headers)), _disconnecting_after(delay), send)
            for _ in range(SETTLE_STEPS):
                await anyio.sleep(0)
            assert len(subscribed) == subscribed_before + 1, "the route subscribed for this request (non-vacuity)"
            gen = generators[-1]()
            if gen is not None and gen.ag_frame is not None and not gen.ag_running:
                left_open.append(delay)  # alive, and its `finally` has not run: the window of 044-5
            assert run._subs == [], (
                f"delay={delay}: the subscription outlived the response by {SETTLE_STEPS} loop steps "
                f"(generator left open at line {gen.ag_frame.f_lineno if gen is not None and gen.ag_frame else None})"
            )
            with runtime._lock:
                watched_since = run._no_subscribers_since
                clock["now"] += IDLE_STOP_SEC
                assert runtime._idle_expired_locked(run), (
                    f"delay={delay}: the heartbeat's idle predicate reads the run as watched "
                    f"(no_subscribers_since={watched_since})"
                )
                clock["now"] -= IDLE_STOP_SEC  # the heartbeat beats on: stay inside the window

        assert left_open, (
            "anti-vacuum: no delay left the stream's generator open, so this stand did not reach the state 044-5 "
            f"describes (delays {list(DELAYS)}); widen DELAYS"
        )
        assert all(m.get("status") == 200 for m in sent if m["type"] == "http.response.start"), sent
    finally:
        if gc_was_enabled:
            gc.enable()
        for ref in generators:
            gen = ref()
            if gen is not None:
                await gen.aclose()
        await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)
