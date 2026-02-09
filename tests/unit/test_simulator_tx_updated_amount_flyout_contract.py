from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter

from app.schemas.simulator import SimulatorEvent


def test_tx_updated_amount_flyout_requires_amount() -> None:
    payload = {
        "event_id": "evt_run_1_000001",
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "USD",
        "from": "alice",
        "to": "bob",
        "amount_flyout": True,
        # amount missing
        "edges": [{"from": "alice", "to": "bob"}],
    }

    with pytest.raises(Exception):
        _ = TypeAdapter(SimulatorEvent).validate_python(payload)


def test_tx_updated_amount_flyout_requires_endpoints() -> None:
    payload = {
        "event_id": "evt_run_1_000002",
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "USD",
        "amount": "1.00",
        "amount_flyout": True,
        # endpoints missing: no from/to and edges empty
        "edges": [],
    }

    with pytest.raises(Exception):
        _ = TypeAdapter(SimulatorEvent).validate_python(payload)


def test_tx_updated_amount_flyout_accepts_from_to() -> None:
    payload = {
        "event_id": "evt_run_1_000003",
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "USD",
        "from": "alice",
        "to": "bob",
        "amount": "1.00",
        "amount_flyout": True,
        "edges": [],
    }

    evt = TypeAdapter(SimulatorEvent).validate_python(payload)
    assert getattr(evt, "type") == "tx.updated"


def test_tx_updated_amount_flyout_accepts_edges() -> None:
    payload = {
        "event_id": "evt_run_1_000004",
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "tx.updated",
        "equivalent": "USD",
        "amount": "1.00",
        "amount_flyout": True,
        "edges": [{"from": "alice", "to": "bob"}],
    }

    evt = TypeAdapter(SimulatorEvent).validate_python(payload)
    assert getattr(evt, "type") == "tx.updated"
