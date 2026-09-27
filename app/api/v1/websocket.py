from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.deps import get_current_participant
from app.db import session as db_session
from app.utils.event_bus import event_bus
from app.utils.exceptions import ForbiddenException, UnauthorizedException


router = APIRouter()
_BEARER_SUBPROTOCOL = "bearer"


def _access_token_from_subprotocols(websocket: WebSocket) -> str | None:
    protocols = websocket.scope.get("subprotocols", [])
    if len(protocols) != 2 or protocols[0] != _BEARER_SUBPROTOCOL:
        return None
    token = protocols[1]
    return token if token else None


@router.websocket("/ws")
async def ws_events(websocket: WebSocket):
    token = _access_token_from_subprotocols(websocket)
    if not token:
        await websocket.close(code=1008)
        return

    # The HTTP chain "token -> participant -> active" (programme 024, F-024-4c): a suspended
    # participant or a `sub` naming nobody is closed before `accept`. A short session of its own,
    # not `Depends(get_db)`, which would hold a pooled connection for the socket's whole life.
    try:
        async with db_session.AsyncSessionLocal() as session:
            participant = await get_current_participant(db=session, token=token)
            pid = str(participant.pid)
    except (UnauthorizedException, ForbiddenException):
        await websocket.close(code=1008)
        return

    await websocket.accept(subprotocol=_BEARER_SUBPROTOCOL)
    await websocket.send_json({"type": "hello", "pid": pid, "ts": datetime.now(timezone.utc).isoformat()})

    sub = None
    writer_task: asyncio.Task | None = None

    async def _writer(queue: asyncio.Queue):
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
                continue

            try:
                obj = json.loads(data)
            except Exception:
                await websocket.send_json({"type": "error", "error": "invalid_json"})
                continue

            if obj.get("type") == "subscribe":
                events = obj.get("events") or []
                if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
                    await websocket.send_json({"type": "error", "error": "invalid_events"})
                    continue

                if sub is not None:
                    await event_bus.unsubscribe(sub)
                    sub = None
                sub = await event_bus.subscribe(pid=pid, events=events)

                if writer_task is not None:
                    writer_task.cancel()
                    await asyncio.gather(writer_task, return_exceptions=True)
                writer_task = asyncio.create_task(_writer(sub.queue))

                await websocket.send_json({"type": "subscribed", "events": events})
                continue

            await websocket.send_json({"type": "error", "error": "unknown_message"})

    except WebSocketDisconnect:
        pass
    finally:
        if writer_task is not None:
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
        if sub is not None:
            await event_bus.unsubscribe(sub)
        try:
            await websocket.close()
        except Exception:
            pass
