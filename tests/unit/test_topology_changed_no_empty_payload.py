"""Regression tests: topology.changed events must NEVER have empty payload.

The frontend calls refreshSnapshot() when it receives a topology.changed event
with an empty payload (no added_nodes/edges/patches). This causes visible
jitter / "sticking" in the UI after full-stack restart.

These tests verify the contract at every backend emission point.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.sse_broadcast import SseBroadcast
from app.core.simulator.trust_drift_engine import broadcast_trust_drift_changed
from app.schemas.simulator import (
    SimulatorTopologyChangedEvent,
    TopologyChangedPayload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now():
    return datetime.now(tz=timezone.utc)


def _make_run(**overrides) -> RunRecord:
    run = RunRecord(
        run_id=overrides.pop("run_id", "run-test-1"),
        scenario_id=overrides.pop("scenario_id", "sc-1"),
        mode=overrides.pop("mode", "real"),
        state=overrides.pop("state", "running"),
    )
    for k, v in overrides.items():
        setattr(run, k, v)
    return run


class FakeSseBroadcast:
    """Captures all broadcast calls for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self._event_counter = 0

    def next_event_id(self, run: RunRecord) -> str:
        self._event_counter += 1
        return f"evt_{self._event_counter}"

    def broadcast(self, run_id: str, evt: dict) -> None:
        self.events.append((run_id, evt))


# ---------------------------------------------------------------------------
# Test: broadcast_trust_drift_changed skips empty edge_patch
# ---------------------------------------------------------------------------

class TestBroadcastTrustDriftChangedSkipsEmpty:
    """trust_drift_engine.broadcast_trust_drift_changed must NEVER emit
    a topology.changed event when edge_patches_by_eq is empty or missing
    for a given equivalent.
    """

    def test_no_event_when_edge_patches_missing(self):
        """No event emitted when edge_patches_by_eq is None."""
        sse = FakeSseBroadcast()
        run = _make_run()
        broadcast_trust_drift_changed(
            sse=sse,
            utc_now=_utc_now,
            logger=logging.getLogger("test"),
            run_id="run-1",
            run=run,
            reason="trust_drift_decay",
            equivalents=["UAH", "EUR"],
            edge_patches_by_eq=None,
        )
        assert len(sse.events) == 0, (
            "Must not emit topology.changed when edge_patches_by_eq is None"
        )

    def test_no_event_when_edge_patches_empty_dict(self):
        """No event emitted when edge_patches_by_eq is an empty dict."""
        sse = FakeSseBroadcast()
        run = _make_run()
        broadcast_trust_drift_changed(
            sse=sse,
            utc_now=_utc_now,
            logger=logging.getLogger("test"),
            run_id="run-1",
            run=run,
            reason="trust_drift_decay",
            equivalents=["UAH"],
            edge_patches_by_eq={},
        )
        assert len(sse.events) == 0

    def test_no_event_when_edge_patches_empty_list_for_eq(self):
        """No event when the specific equivalent has an empty patch list."""
        sse = FakeSseBroadcast()
        run = _make_run()
        broadcast_trust_drift_changed(
            sse=sse,
            utc_now=_utc_now,
            logger=logging.getLogger("test"),
            run_id="run-1",
            run=run,
            reason="trust_drift_decay",
            equivalents=["UAH"],
            edge_patches_by_eq={"UAH": []},
        )
        assert len(sse.events) == 0

    def test_event_emitted_with_nonempty_edge_patch(self):
        """Event IS emitted when edge_patch is non-empty."""
        sse = FakeSseBroadcast()
        run = _make_run()
        patch = [{"source": "A", "target": "B", "used": "10.00", "available": "90.00"}]
        broadcast_trust_drift_changed(
            sse=sse,
            utc_now=_utc_now,
            logger=logging.getLogger("test"),
            run_id="run-1",
            run=run,
            reason="trust_drift_decay",
            equivalents=["UAH"],
            edge_patches_by_eq={"UAH": patch},
        )
        assert len(sse.events) == 1
        _, evt = sse.events[0]
        assert evt["type"] == "topology.changed"
        assert evt["equivalent"] == "UAH"
        assert evt["reason"] == "trust_drift_decay"
        # payload must have edge_patch
        payload = evt.get("payload", {})
        assert isinstance(payload.get("edge_patch"), list)
        assert len(payload["edge_patch"]) > 0

    def test_mixed_equivalents_only_nonempty_emitted(self):
        """Only equivalents with non-empty patches get events."""
        sse = FakeSseBroadcast()
        run = _make_run()
        broadcast_trust_drift_changed(
            sse=sse,
            utc_now=_utc_now,
            logger=logging.getLogger("test"),
            run_id="run-1",
            run=run,
            reason="trust_drift_decay",
            equivalents=["UAH", "EUR", "HOUR"],
            edge_patches_by_eq={
                "UAH": [{"source": "A", "target": "B"}],
                "EUR": [],  # empty → skip
                # HOUR missing → skip
            },
        )
        assert len(sse.events) == 1
        _, evt = sse.events[0]
        assert evt["equivalent"] == "UAH"


# ---------------------------------------------------------------------------
# Test: topology.changed payload serialization has correct keys
# ---------------------------------------------------------------------------

class TestTopologyChangedPayloadSerialization:
    """Verify that TopologyChangedPayload serializes edge_patch correctly
    so the frontend normalizer can parse it.
    """

    def test_edge_patch_present_in_model_dump(self):
        """edge_patch should appear in serialized payload."""
        payload = TopologyChangedPayload(
            edge_patch=[{"source": "A", "target": "B", "used": "5.00"}]
        )
        dumped = payload.model_dump(mode="json")
        assert "edge_patch" in dumped
        assert len(dumped["edge_patch"]) == 1
        assert dumped["edge_patch"][0]["source"] == "A"

    def test_empty_lists_serialized(self):
        """Default empty lists should serialize as empty arrays (not None)."""
        payload = TopologyChangedPayload()
        dumped = payload.model_dump(mode="json")
        assert dumped["added_nodes"] == []
        assert dumped["removed_nodes"] == []
        assert dumped["added_edges"] == []
        assert dumped["removed_edges"] == []
        assert dumped.get("edge_patch") is None
        assert dumped.get("node_patch") is None

    def test_event_by_alias_true(self):
        """SimulatorTopologyChangedEvent.model_dump(by_alias=True) must
        produce correct keys for the frontend normalizer.
        """
        payload = TopologyChangedPayload(
            edge_patch=[{"source": "X", "target": "Y"}]
        )
        evt = SimulatorTopologyChangedEvent(
            event_id="evt_1",
            ts=_utc_now(),
            type="topology.changed",
            equivalent="UAH",
            payload=payload,
            reason="trust_drift_decay",
        )
        dumped = evt.model_dump(mode="json", by_alias=True)
        assert dumped["type"] == "topology.changed"
        assert dumped["equivalent"] == "UAH"
        assert dumped["reason"] == "trust_drift_decay"
        p = dumped["payload"]
        assert isinstance(p["edge_patch"], list)
        assert len(p["edge_patch"]) == 1


# ---------------------------------------------------------------------------
# Test: frontend contract — empty payload triggers refreshSnapshot
# ---------------------------------------------------------------------------

class TestFrontendContractEmptyPayload:
    """Simulate the frontend's isEmptyPayload / hasPatches logic to verify
    that our backend events would NOT trigger refreshSnapshot().
    """

    @staticmethod
    def _frontend_would_refresh(evt_payload: dict) -> bool:
        """Replicate the frontend's topology.changed handler logic."""
        payload = evt_payload
        has_payload = bool(payload) and isinstance(payload, dict)

        has_patches = has_payload and (
            len(payload.get("node_patch") or []) > 0
            or len(payload.get("edge_patch") or []) > 0
        )

        is_empty_payload = (
            not has_payload
            or (
                len(payload.get("added_nodes") or []) == 0
                and len(payload.get("removed_nodes") or []) == 0
                and len(payload.get("frozen_nodes") or []) == 0
                and len(payload.get("added_edges") or []) == 0
                and len(payload.get("removed_edges") or []) == 0
                and len(payload.get("frozen_edges") or []) == 0
                and not has_patches
            )
        )

        # Frontend: if ((isEmptyPayload && !hasPatches) || !state.snapshot)
        # We assume state.snapshot exists.
        return is_empty_payload and not has_patches

    def test_empty_payload_triggers_refresh(self):
        """An empty payload WOULD trigger refreshSnapshot — this is the bug."""
        assert self._frontend_would_refresh({}) is True
        assert self._frontend_would_refresh({"added_nodes": []}) is True

    def test_payload_with_edge_patch_does_not_trigger_refresh(self):
        """A payload with edge_patch should NOT trigger refreshSnapshot."""
        payload = {
            "added_nodes": [],
            "removed_nodes": [],
            "added_edges": [],
            "removed_edges": [],
            "edge_patch": [{"source": "A", "target": "B", "used": "10.00"}],
        }
        assert self._frontend_would_refresh(payload) is False

    def test_payload_with_added_nodes_does_not_trigger_refresh(self):
        """A payload with added_nodes should NOT trigger refreshSnapshot."""
        payload = {
            "added_nodes": [{"pid": "new-1", "name": "New"}],
            "removed_nodes": [],
            "added_edges": [],
            "removed_edges": [],
        }
        assert self._frontend_would_refresh(payload) is False

    def test_trust_drift_decay_event_does_not_trigger_refresh(self):
        """Simulate a complete trust_drift_decay event and verify it would
        NOT trigger refreshSnapshot on the frontend.
        """
        # Build the event as the backend would
        edge_patch = [
            {"source": "alice", "target": "bob", "used": "50.00", "available": "40.00",
             "trust_limit": "90.00", "viz_alpha_key": "active", "viz_width_key": "medium"},
        ]
        payload = TopologyChangedPayload(edge_patch=edge_patch)
        evt = SimulatorTopologyChangedEvent(
            event_id="evt_99",
            ts=_utc_now(),
            type="topology.changed",
            equivalent="UAH",
            payload=payload,
            reason="trust_drift_decay",
        )
        dumped = evt.model_dump(mode="json", by_alias=True)
        evt_payload = dumped.get("payload", {})

        assert self._frontend_would_refresh(evt_payload) is False, (
            "trust_drift_decay event with edge_patch must NOT trigger refreshSnapshot"
        )
