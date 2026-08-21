from __future__ import annotations

from datetime import datetime, timezone

from app.core.simulator.models import RunRecord
from app.core.simulator.runtime_utils import run_to_status


def test_run_status_serializes_stop_metadata_and_authoritative_counters() -> None:
    requested_at = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    run = RunRecord(
        run_id="run_schema",
        scenario_id="scenario_schema",
        mode="real",
        state="stopping",
        stop_requested_at=requested_at,
        stop_source="operator",
        stop_reason="maintenance",
        stop_client="admin-ui",
        attempts_total=11,
        committed_total=7,
        rejected_total=3,
        errors_total=1,
        timeouts_total=2,
    )
    run._real_consec_all_rejected_ticks = 4

    payload = run_to_status(run).model_dump(mode="json")

    assert payload["api_version"] == "simulator-api/1"
    assert payload["stop_requested_at"] == "2026-08-08T12:00:00Z"
    assert payload["stop_source"] == "operator"
    assert payload["stop_reason"] == "maintenance"
    assert payload["stop_client"] == "admin-ui"
    assert payload["attempts_total"] == 11
    assert payload["committed_total"] == 7
    assert payload["rejected_total"] == 3
    assert payload["errors_total"] == 1
    assert payload["timeouts_total"] == 2
    assert payload["errors_last_1m"] == 0
    assert payload["consec_all_rejected_ticks"] == 4
