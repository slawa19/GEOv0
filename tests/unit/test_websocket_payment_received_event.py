import asyncio
import json
import logging
import socket

import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.utils.security import create_access_token
from app.utils.event_bus import event_bus


def test_ws_receives_payment_received_event_for_subscribed_pid():
    pid = "PID_WS_TEST"
    token = create_access_token(pid)

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/ws",
            subprotocols=["bearer", token],
        ) as ws:
            assert ws.accepted_subprotocol == "bearer"
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["pid"] == pid

            ws.send_text(json.dumps({"type": "subscribe", "events": ["payment.received"]}))
            ack = ws.receive_json()
            assert ack["type"] == "subscribed"

            # Publish a message and ensure the websocket receives it.
            event_bus.publish(
                recipient_pid=pid,
                event="payment.received",
                payload={"tx_id": "tx123", "from": "A", "to": pid, "equivalent": "USD", "amount": "1.00"},
            )

            msg = ws.receive_json()
            assert msg["event"] == "payment.received"
            assert msg["payload"]["tx_id"] == "tx123"


def test_ws_rejects_legacy_query_string_token():
    token = create_access_token("PID_WS_LEGACY_QUERY")

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/api/v1/ws?token={token}"):
                pass

    assert exc_info.value.code == 1008


class _LogCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.mark.asyncio
async def test_ws_subprotocol_auth_keeps_token_out_of_uvicorn_logs():
    token = create_access_token("PID_WS_UVICORN_LOG")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        access_log=True,
        lifespan="off",
        log_level="info",
    )
    server = uvicorn.Server(config)
    collector = _LogCollector()
    uvicorn_loggers = [logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access")]
    for logger in uvicorn_loggers:
        logger.addHandler(collector)

    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        while not server.started:
            await asyncio.sleep(0)

        async with websockets.connect(
            f"ws://127.0.0.1:{port}/api/v1/ws",
            subprotocols=["bearer", token],
        ) as websocket:
            hello = json.loads(await websocket.recv())
            assert hello["pid"] == "PID_WS_UVICORN_LOG"
    finally:
        server.should_exit = True
        await server_task
        listener.close()
        for logger in uvicorn_loggers:
            logger.removeHandler(collector)

    assert collector.messages
    assert token not in "\n".join(collector.messages)
