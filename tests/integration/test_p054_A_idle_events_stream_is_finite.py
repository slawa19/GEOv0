"""054-C-3: `GET /simulator/events` without an active run answers a FINITE stream.

The route used to answer a caller with no active run (and no `Last-Event-ID`) with an endless keep-alive stream. It
registered no subscription, so `SIMULATOR_SSE_MAX_CONNECTIONS` never bounded it: any number of such streams could be
held open. The answer is now the same `200 text/event-stream` carrying one `retry:` record (when to reconnect) that
then ENDS - nothing is held, so there is nothing for the limit to count.

Real path: the ASGI application itself, a real signed session cookie of a visitor with no run. The client never
disconnects: `receive` answers `http.disconnect` only after every call has returned on its own (or on the time bound),
so a call returns only if the server ends the stream.
"""

from __future__ import annotations

import anyio
import pytest
from httpx import AsyncClient

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.session import COOKIE_NAME, create_session
from app.main import app

API = "/api/v1/simulator"
SSE_LIMIT = 2
HELD = SSE_LIMIT + 1


def _sse_scope(cookie: str) -> dict:
    path = f"{API}/events"
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"equivalent=UAH",
        "root_path": "",
        "headers": [(b"cookie", f"{COOKIE_NAME}={cookie}".encode()), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 50001),
        "server": ("test", 80),
    }


@pytest.mark.asyncio
async def test_p054_C_3_idle_events_stream_ends_on_its_own(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(runtime._sse, "_max_subs_total", SSE_LIMIT)
    cookie, _ = create_session(settings.SIMULATOR_SESSION_SECRET)
    leave = anyio.Event()
    statuses: list[int] = []
    bodies: list[bytes] = [b"" for _ in range(HELD)]
    ended = [False for _ in range(HELD)]

    async def one(i: int) -> None:
        async def receive() -> dict:
            await leave.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])
            elif message["type"] == "http.response.body":
                bodies[i] += message.get("body", b"")
                if not message.get("more_body", False):
                    ended[i] = True

        await app(_sse_scope(cookie), receive, send)

    try:
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tg:
                for i in range(HELD):
                    tg.start_soon(one, i)
    except TimeoutError:
        pytest.fail(f"idle /simulator/events streams did not end on their own within 10 s: statuses={statuses} "
                    f"ended={ended} bodies={bodies}")
    finally:
        leave.set()
    print(f"054-C-3 statuses={statuses} ended={ended} bodies={bodies}")
    assert statuses == [200] * HELD, statuses
    assert all(ended), ended
    for body in bodies:
        assert b"retry:" in body, body
