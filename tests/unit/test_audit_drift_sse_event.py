import pytest
from pydantic import ValidationError

from app.schemas.simulator import SimulatorAuditDriftEvent


def test_audit_drift_event_serializes_with_required_fields() -> None:
    evt = SimulatorAuditDriftEvent(
        event_id="evt_test_000001",
        ts="2026-02-13T15:30:00Z",
        type="audit.drift",
        equivalent="USD",
        tick_index=42,
        severity="warning",
        total_drift="30.00",
        drifts=[
            {
                "participant_id": "p_alice",
                "expected_delta": "-50.00",
                "actual_delta": "-20.00",
                "drift": "30.00",
            }
        ],
        source="post_tick_audit",
    )

    payload = evt.model_dump(mode="json", by_alias=True)
    assert payload["type"] == "audit.drift"
    assert payload["severity"] in {"warning", "critical"}
    assert isinstance(payload["total_drift"], str)
    assert isinstance(payload["drifts"], list)
    assert payload["source"] in {"post_tick_audit", "delta_check"}


def test_audit_drift_event_rejects_invalid_severity() -> None:
    with pytest.raises(ValidationError):
        SimulatorAuditDriftEvent(
            event_id="evt_test_000001",
            ts="2026-02-13T15:30:00Z",
            type="audit.drift",
            equivalent="USD",
            tick_index=1,
            severity="info",  # invalid
            total_drift="0.01",
            drifts=[],
            source="post_tick_audit",
        )
