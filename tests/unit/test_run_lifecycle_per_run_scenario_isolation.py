from __future__ import annotations

import threading
from datetime import datetime, timezone
import logging

import pytest

from app.core.simulator.run_lifecycle import RunLifecycle
from app.core.simulator.runtime_utils import edges_by_equivalent
from app.core.simulator.models import RunRecord


class _NoopArtifacts:
    def init_run_artifacts(self, run: RunRecord) -> None:  # pragma: no cover
        return

    def start_events_writer(self, run_id: str) -> None:  # pragma: no cover
        return


class _NoopSse:  # pragma: no cover
    pass


@pytest.mark.asyncio
async def test_create_run_deep_copies_scenario_raw_per_run() -> None:
    lock = threading.RLock()
    runs: dict[str, RunRecord] = {}

    scenario_template = {
        "participants": [{"id": "A", "name": "A"}],
        "trustlines": [{"from": "A", "to": "B", "equivalent": "EUR", "status": "active"}],
        "equivalents": ["EUR"],
    }

    def get_scenario_raw(_: str):
        # Return the SAME dict each time to ensure the lifecycle deep-copies it.
        return scenario_template

    async def heartbeat_loop(_: str) -> None:
        return

    lifecycle = RunLifecycle(
        lock=lock,
        runs=runs,
        set_active_run_id=lambda _: None,
        utc_now=lambda: datetime.now(timezone.utc),
        new_run_id=(lambda it=iter(["run1", "run2"]): next(it)),
        get_scenario_raw=get_scenario_raw,
        edges_by_equivalent=edges_by_equivalent,
        artifacts=_NoopArtifacts(),
        sse=_NoopSse(),
        heartbeat_loop=heartbeat_loop,
        publish_run_status=lambda _: None,
        run_to_status=lambda r: None,  # not used in this test
        get_run_status_payload_json=lambda _: {},
        real_max_in_flight_default=1,
        get_max_active_runs=lambda: 0,
        get_max_run_records=lambda: 0,
        logger=logging.getLogger(__name__),
    )

    run_id1 = await lifecycle.create_run(scenario_id="s1", mode="real", intensity_percent=50)
    run_id2 = await lifecycle.create_run(scenario_id="s1", mode="real", intensity_percent=50)

    r1 = runs[run_id1]
    r2 = runs[run_id2]

    assert r1._scenario_raw is not None
    assert r2._scenario_raw is not None

    assert r1._scenario_raw is not scenario_template
    assert r2._scenario_raw is not scenario_template
    assert r1._scenario_raw is not r2._scenario_raw

    # Deep copy: nested lists must not be shared.
    r1._scenario_raw["participants"].append({"id": "X"})
    assert len(r2._scenario_raw["participants"]) == 1
