"""042 S1 (`T4202`): the server run mode `fixtures` and the debug actions `tx-once` / `clearing-once` are gone.

The owner's decision of 2026-10-16 (spec 042, "вариант 3"): after this programme an event about a payment is born
only in the core. What the wire says now:

* `POST /simulator/runs` and `GET /scenarios/{id}/graph/preview` accept `mode=real` only; an explicit `fixtures` is
  REFUSED with 422 by validation, never coerced to `real` (spec, "Запрещено"). No run is created.
* Without `mode` the run is `real`, and the preview is the database-enriched one (it used to be `fixtures`, a
  topology-only picture).
* `POST /runs/{id}/actions/tx-once` and `.../clearing-once` - events with no payment and no clearing behind them -
  answer 404: the routes do not exist, and the canon does not list them.
* The database refuses a `simulator_runs` row with `mode = 'fixtures'` (CHECK `chk_simulator_runs_mode`, narrowed by
  migration 037; the migration itself is held by `test_p042_s1_migration_037_postgres.py`).

RED ON THE BASE (`1beb8670`): `fixtures` is accepted (200) on both routes, a request without `mode` is refused (422)
and a preview without one is not enriched, both debug actions answer 200, and the row is stored.
"""

from __future__ import annotations

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.simulator.runtime import runtime
from tests.conftest import MODE_B
from tests.migrated_schema import REPO_ROOT
from tests.p042_support import EQUIVALENT, SCENARIO, no_real_tick, start_real_run  # noqa: F401 - a fixture


async def _stop_if_created(client, auth_headers, response) -> None:
    if response.status_code == 200 and "run_id" in response.json():
        await client.post(f"/api/v1/simulator/runs/{response.json()['run_id']}/stop", headers=auth_headers)


@pytest.mark.asyncio
async def test_a_run_requested_in_fixtures_mode_is_refused_and_none_is_made(client, auth_headers, no_real_tick) -> None:  # noqa: F811
    runs_before = set(runtime._runs)
    response = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": SCENARIO, "mode": "fixtures", "intensity_percent": 0},
    )
    try:
        assert response.status_code == 422, f"{response.status_code}: {response.text[:300]}"
        assert set(runtime._runs) == runs_before
    finally:
        await _stop_if_created(client, auth_headers, response)


@pytest.mark.asyncio
async def test_a_preview_in_fixtures_mode_is_refused(client, auth_headers) -> None:
    response = await client.get(
        f"/api/v1/simulator/scenarios/{SCENARIO}/graph/preview",
        headers=auth_headers,
        params={"equivalent": EQUIVALENT, "mode": "fixtures"},
    )
    assert response.status_code == 422, f"{response.status_code}: {response.text[:300]}"


@pytest.mark.asyncio
async def test_a_run_requested_without_a_mode_is_a_real_run(client, auth_headers, no_real_tick) -> None:  # noqa: F811
    response = await client.post(
        "/api/v1/simulator/runs", headers=auth_headers, json={"scenario_id": SCENARIO, "intensity_percent": 0}
    )
    try:
        assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
        run_id = response.json()["run_id"]
        status = await client.get(f"/api/v1/simulator/runs/{run_id}", headers=auth_headers)
        assert status.status_code == 200, status.text
        assert (runtime.get_run(run_id).mode, status.json()["mode"]) == ("real", "real")
    finally:
        await _stop_if_created(client, auth_headers, response)


@pytest.mark.asyncio
async def test_a_preview_without_a_mode_is_the_database_enriched_one(client, auth_headers, monkeypatch) -> None:
    enriched: list[str] = []
    original = runtime._snapshot_builder.enrich_snapshot_from_db_if_enabled

    async def _recording(snap, *, equivalent, session=None):
        enriched.append(equivalent)
        return await original(snap, equivalent=equivalent, session=session)

    monkeypatch.setattr(runtime._snapshot_builder, "enrich_snapshot_from_db_if_enabled", _recording)
    response = await client.get(
        f"/api/v1/simulator/scenarios/{SCENARIO}/graph/preview", headers=auth_headers, params={"equivalent": EQUIVALENT}
    )
    assert response.status_code == 200, response.text
    assert enriched == [EQUIVALENT], f"the preview without `mode` asked the database enrichment {enriched!r} time(s)"
    # Control: the preview is still the scenario's picture.
    assert {node["id"] for node in response.json()["nodes"]} == {"p1", "p2"}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["tx-once", "clearing-once"])
async def test_the_debug_actions_are_gone(client, auth_headers, monkeypatch, no_real_tick, action) -> None:  # noqa: F811
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)  # they were behind this flag
    run_id = await start_real_run(client, auth_headers)
    try:
        response = await client.post(
            f"/api/v1/simulator/runs/{run_id}/actions/{action}", headers=auth_headers, json={"equivalent": EQUIVALENT}
        )
        assert response.status_code == 404, f"{action}: {response.status_code}: {response.text[:300]}"
    finally:
        await client.post(f"/api/v1/simulator/runs/{run_id}/stop", headers=auth_headers)

    canon = yaml.safe_load((REPO_ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))
    assert f"/simulator/runs/{{run_id}}/actions/{action}" not in canon["paths"]


@MODE_B
@pytest.mark.asyncio
async def test_the_database_refuses_a_fixtures_run_row(db_session) -> None:
    """On a clone of the MIGRATED template (mode B), so it is the migrations' CHECK that answers."""

    insert = text(
        "INSERT INTO simulator_runs (run_id, scenario_id, mode, state, owner_id) "
        "VALUES (:run_id, 's', :mode, 'stopped', 'test')"
    )
    with pytest.raises(IntegrityError, match="chk_simulator_runs_mode"):
        async with db_session.begin_nested():
            await db_session.execute(insert, {"run_id": "p042-s1-fixtures", "mode": "fixtures"})
    # Control: the same row in mode `real` is stored, so the refusal above is of the mode and nothing else.
    async with db_session.begin_nested():
        await db_session.execute(insert, {"run_id": "p042-s1-real", "mode": "real"})
    stored = (await db_session.execute(text("SELECT run_id, mode FROM simulator_runs WHERE run_id LIKE 'p042-s1-%'"))).all()
    assert [tuple(row) for row in stored] == [("p042-s1-real", "real")]
