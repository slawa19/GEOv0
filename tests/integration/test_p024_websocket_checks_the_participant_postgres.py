"""Programme 024, stage 0, F-024-4c (N-6 / AL-9): `/api/v1/ws` admits only an ACTIVE participant.

The WebSocket accepted any well-signed access token by its `sub`, without loading the participant,
while every HTTP route runs "token -> participant -> active" (`app/api/deps.py::get_current_participant`).
A suspended participant, or a `sub` that names nobody, kept receiving its events. The endpoint now
calls that same function and closes with 1008 before `accept`.

Stand: a mode-B clone (`committed_database`) installed as `app.db.session.AsyncSessionLocal`, which
the endpoint looks up at call time; the socket goes through Starlette's `TestClient` without the
application lifespan.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db.models.participant import Participant
from app.main import app
from app.utils.security import create_access_token


@pytest_asyncio.fixture
async def ws_db(committed_database, monkeypatch):
    import app.db.session as app_db_session

    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", committed_database.sessionmaker)
    return committed_database


async def _participant(ws_db, status: str) -> str:
    pid = f"P024_WS_{status}_{uuid.uuid4().hex[:8]}"
    async with ws_db.sessionmaker() as s:
        s.add(
            Participant(
                pid=pid,
                display_name=pid,
                public_key=uuid.uuid4().hex * 2,
                type="person",
                status=status,
                profile={},
            )
        )
        await s.commit()
    return pid


def _connect_code(pid: str) -> int:
    token = create_access_token(pid)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws", subprotocols=["bearer", token]) as ws:
            ws.receive_json()
    return exc_info.value.code


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["suspended", "left", "deleted"])
async def test_a_participant_that_is_not_active_is_closed_with_1008(ws_db, status: str) -> None:
    pid = await _participant(ws_db, status)

    assert _connect_code(pid) == 1008


@pytest.mark.asyncio
async def test_a_token_whose_sub_names_nobody_is_closed_with_1008(ws_db) -> None:
    assert _connect_code(f"P024_WS_NOBODY_{uuid.uuid4().hex[:8]}") == 1008


@pytest.mark.asyncio
async def test_an_active_participant_is_accepted_as_before(ws_db) -> None:
    # Counter-check (anti-vacuum): the gate must still admit the participant it exists for.
    pid = await _participant(ws_db, "active")
    token = create_access_token(pid)

    with TestClient(app).websocket_connect("/api/v1/ws", subprotocols=["bearer", token]) as ws:
        assert ws.accepted_subprotocol == "bearer"
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["pid"] == pid
