from __future__ import annotations

from app.schemas.simulator import (
    SimulatorActionClearingCycle,
    SimulatorActionClearingRealResponse,
    SimulatorActionEdgeRef,
)


def test_clearing_real_response_serializes_edge_ref_with_from_alias() -> None:
    resp = SimulatorActionClearingRealResponse(
        equivalent="UAH",
        cleared_cycles=1,
        total_cleared_amount="10",
        cycles=[
            SimulatorActionClearingCycle(
                cleared_amount="10",
                edges=[SimulatorActionEdgeRef(from_="alice", to="bob")],
            )
        ],
        client_action_id="client_1",
    )

    payload = resp.model_dump(mode="json")
    edge = payload["cycles"][0]["edges"][0]

    assert edge["from"] == "alice"
    assert edge["to"] == "bob"
    assert "from_" not in edge
