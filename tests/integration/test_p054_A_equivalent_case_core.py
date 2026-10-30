"""054-E-13 / 054-A-16: an equivalent code is matched case-insensitively in the simulator core.

Before: a subscription opened with `uah` kept that spelling and silently lost every `UAH` event (live dispatch and
replay), the scenario preview and the run snapshot echoed `uah`, and the metrics/bottlenecks reads matched the stored
upper-cased code literally, answering an empty picture. These tests enter the core where the API hands it the
caller's spelling - `runtime.subscribe`, `runtime.build_scenario_preview`, `runtime.build_graph_snapshot`, the
metrics reader - so they do not depend on the route normalising it first (054 wave A, developer 2, does that too).

Each lower-case read is compared with the upper-case control of the same state: the control is the anti-vacuum
check that there was something to lose.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

import app.db.session as db_session_module
from app.config import settings
from app.core.simulator import storage as simulator_storage
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B

_SCENARIO = "greenfield-village-100-realistic-v2"
_DOMAIN_TIMEOUT_S = 10.0


async def _next_domain_event(queue: asyncio.Queue, *, timeout: float) -> dict[str, Any]:
    async def _wait() -> dict[str, Any]:
        while True:
            event = await queue.get()
            if event.get("type") in {"tx.updated", "tx.failed", "clearing.done"}:
                return event

    return await asyncio.wait_for(_wait(), timeout=timeout)


def _drain(queue: asyncio.Queue) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    while True:
        try:
            out.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return out


def _snapshot_view(snap) -> dict[str, Any]:
    wire = snap.model_dump(mode="json")
    wire.pop("generated_at", None)
    return wire


@MODE_B
@pytest.mark.asyncio
async def test_a_lower_case_subscription_and_snapshot_see_the_runs_upper_case_equivalent(
    client: AsyncClient, auth_headers
):
    resp = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": _SCENARIO, "mode": "real", "intensity_percent": 90},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    control = await runtime.subscribe(run_id, equivalent="UAH")
    lower = await runtime.subscribe(run_id, equivalent=" uah ")
    try:
        try:
            event = await _next_domain_event(control.queue, timeout=_DOMAIN_TIMEOUT_S)
        finally:
            stop = await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)
            assert stop.status_code == 200, stop.text
        assert event.get("equivalent") == "UAH", event

        # Live dispatch: the same event reached the lower-case subscription (dispatch to every subscriber is one step
        # under the runtime lock, so it is already queued when the control has it).
        lower_ids = [e.get("event_id") for e in _drain(lower.queue)]
        assert event["event_id"] in lower_ids, (event["event_id"], lower_ids[:10])
    finally:
        await runtime.unsubscribe(run_id, control)
        await runtime.unsubscribe(run_id, lower)

    # Replay: a lower-case reconnect just before that event gets it back.
    prefix, seq = str(event["event_id"]).rsplit("_", 1)
    cursor = f"{prefix}_{int(seq) - 1:06d}"
    replayed = await runtime.subscribe(run_id, equivalent="uah", after_event_id=cursor)
    try:
        replay_ids = [e.get("event_id") for e in _drain(replayed.queue)]
    finally:
        await runtime.unsubscribe(run_id, replayed)
    assert replay_ids[:1] == [event["event_id"]], replay_ids[:10]

    # The run snapshot: same picture as the upper-case read, named by the code it was filtered by.
    upper_snap = await runtime.build_graph_snapshot(run_id=run_id, equivalent="UAH")
    lower_snap = await runtime.build_graph_snapshot(run_id=run_id, equivalent="uah")
    assert upper_snap.links, "premise: the run has UAH lines"
    assert lower_snap.equivalent == "UAH", lower_snap.equivalent
    assert _snapshot_view(lower_snap) == _snapshot_view(upper_snap)


@pytest.mark.asyncio
async def test_the_scenario_preview_names_the_upper_case_code(client: AsyncClient, auth_headers, monkeypatch):
    preview = await runtime.build_scenario_preview(scenario_id="minimal", equivalent="uah", mode="real")
    assert preview.links, "premise: the scenario has a UAH line"
    assert preview.equivalent == "UAH", preview.equivalent

    monkeypatch.setattr("app.config.settings.SIMULATOR_SCENARIO_ALLOWLIST", "minimal")
    response = await client.get(
        "/api/v1/simulator/scenarios/minimal/graph/preview",
        headers=auth_headers,
        params={"equivalent": "uah"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["equivalent"] == "UAH", response.json()


# --- metrics and bottlenecks: the real writers, then the reader with a lower-case code ----------------------------

_RUN_ID = "p054-a16-run"


class _SharedSession:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


def _reader() -> MetricsBottlenecks:
    run = SimpleNamespace(run_id=_RUN_ID, scenario_id="scn-1", mode="real", state="running")
    return MetricsBottlenecks(
        lock=threading.RLock(),
        runs={_RUN_ID: run},
        db_enabled=lambda: True,
        logger=logging.getLogger("tests.p054.a16"),
    )


async def test_metrics_and_bottlenecks_read_a_lower_case_code(db_session: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    wrote = await simulator_storage.write_tick_metrics(
        run_id=_RUN_ID,
        t_ms=1_000,
        per_equivalent={"UAH": {"committed": 1, "rejected": 1, "errors": 0, "timeouts": 0}},
        session=db_session,
        commit=False,
    )
    assert wrote is not False
    wrote = await simulator_storage.write_tick_bottlenecks(
        run_id=_RUN_ID,
        equivalent="UAH",
        computed_at=datetime.now(timezone.utc),
        edge_stats={("alice", "bob"): {"attempts": 2, "errors": 1}},
        session=db_session,
        commit=False,
    )
    assert wrote is not False
    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", lambda: _SharedSession(db_session), raising=False)

    async def _metrics(eq: str):
        return await _reader().build_metrics(run_id=_RUN_ID, equivalent=eq, from_ms=1_000, to_ms=1_000, step_ms=1_000)

    upper, lower = await _metrics("UAH"), await _metrics("uah")
    success = [s.points[0].v for s in upper.series if s.key == "success_rate"]
    assert success and success[0] is not None, upper.series  # premise: the control reads a measured point
    assert lower.model_dump(mode="json") == upper.model_dump(mode="json")

    async def _bottlenecks(eq: str):
        return await _reader().build_bottlenecks(run_id=_RUN_ID, equivalent=eq, limit=10, min_score=None)

    upper_b, lower_b = await _bottlenecks("UAH"), await _bottlenecks("uah")
    assert upper_b.items, "premise: the control reads the written bottleneck"
    assert lower_b.model_dump(mode="json") == upper_b.model_dump(mode="json")
