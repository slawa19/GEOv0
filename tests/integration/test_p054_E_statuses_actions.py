"""054-E-12: `actions/trustlines-list` requires `equivalent`, and its absence is the declared 400 INVALID_REQUEST.

Witness W15 of the review of slice E turned to the intended contract. Before: the canon called the parameter optional
and the route answered its absence with 404 `EQUIVALENT_NOT_FOUND` - a client following authority number one got a
"not found" for a request the canon allowed. Setup as in
`test_p024_orphan_scenario_edges_are_not_part_of_the_run.py::test_trustlines_list_does_not_read_an_orphan_edge`.
"""

from __future__ import annotations

import uuid

import pytest

import app.core.simulator.storage as simulator_storage
from app.core.simulator.models import RunRecord
from tests.integration.test_p024_orphan_scenario_edges_are_not_part_of_the_run import _world
from tests.integration.test_p054_E_statuses import _CANON, _assert_declared

_PATH = "/simulator/runs/{run_id}/actions/trustlines-list"


def test_e12_the_canon_requires_equivalent() -> None:
    parameters = {p["name"]: p for p in _CANON["paths"][_PATH]["get"]["parameters"] if "name" in p}
    assert parameters["equivalent"].get("required") is True, parameters["equivalent"]


@pytest.mark.asyncio
async def test_e12_trustlines_list_without_equivalent_is_a_declared_400(client, db_session, monkeypatch) -> None:
    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr(simulator_storage, "db_enabled", lambda: True)
    eq, s1, s2, r1, r2, scenario = await _world(db_session)
    client.cookies.clear()
    owner_id = (await client.post("/api/v1/simulator/session/ensure")).json()["owner_id"]
    run_id = f"p054-e-{uuid.uuid4().hex[:6]}"
    registered = RunRecord(run_id=run_id, scenario_id=run_id, mode="real", state="paused", owner_id=owner_id)
    registered._scenario_raw = scenario
    registered._real_seeded = True
    monkeypatch.setitem(simulator_module.runtime._runs, run_id, registered)

    url = f"/api/v1/simulator/runs/{run_id}/actions/trustlines-list"
    control = await client.get(url, params={"equivalent": eq.code})
    omitted = await client.get(url)
    _assert_declared("GET", _PATH, control, 200)
    _assert_declared("GET", _PATH, omitted, 400)
    body = omitted.json()
    assert body["code"] == "INVALID_REQUEST", body
    assert [e["loc"] for e in body["details"]["errors"]] == [["query", "equivalent"]], body
