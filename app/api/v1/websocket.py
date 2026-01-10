from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.utils.security import decode_token
from app.utils.event_bus import event_bus


router = APIRouter()


@router.websocket("/ws")
async def ws_events(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return

    payload = await decode_token(token, expected_type="access")
    if not payload or not payload.get("sub"):
        await websocket.close(code=1008)
        return

    pid = str(payload["sub"])

    await websocket.accept()
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
