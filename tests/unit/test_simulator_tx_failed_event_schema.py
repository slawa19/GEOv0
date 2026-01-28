from datetime import datetime, timezone

from pydantic import TypeAdapter

from app.schemas.simulator import SimulatorEvent


def test_simulator_event_accepts_tx_failed() -> None:
    payload = {
        "event_id": "evt_run_123_000001",
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.failed",
        "equivalent": "USD",
        "from": "alice",
        "to": "bob",
        "error": {
            "code": "PAYMENT_TIMEOUT",
            "message": "PAYMENT_TIMEOUT",
            "at": datetime.now(timezone.utc).isoformat(),
        },
    }

    evt = TypeAdapter(SimulatorEvent).validate_python(payload)
    assert getattr(evt, "type") == "tx.failed"
