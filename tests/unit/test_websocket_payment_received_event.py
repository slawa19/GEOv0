import json

from fastapi.testclient import TestClient

from app.main import app
from app.utils.security import create_access_token
from app.utils.event_bus import event_bus


def test_ws_receives_payment_received_event_for_subscribed_pid():
    pid = "PID_WS_TEST"
    token = create_access_token(pid)

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
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
