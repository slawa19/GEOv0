"""044 `T4405`: a run owns fresh participants; admission of scenarios; the template preview reads no run's state.

The stage-0 file (`test_p044_s0_two_visitors_runs_are_isolated.py`) holds the two-visitor reproducer. These are the
paths around it that the decision of 2026-10-17 names: a restart of the SAME run keeps its participants and debts;
a new run does not adopt rows an older run left under the same template pids; the seeder refuses a run's scenario
that is not bound to it; admission refuses a visitor's start/read of a scenario outside the allowlist (counter-checks:
an admitted one starts; the administrator starts any); an anonymous upload is refused with no registry or disk effect
(counter-check: the administrator's lands); a reference to an undeclared participant refuses the run before it exists.

Real HTTP through the app on the tier's PostgreSQL (mode B); the background tick is a recorder (`no_real_tick`).
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder, RunIdentityUnbound
from app.core.simulator.runtime import runtime
from app.core.simulator.scenario_registry import ScenarioRegistry
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from tests.conftest import MODE_B, sessionmaker_of
from tests.p042_support import no_real_tick  # noqa: F401 - a fixture

pytestmark = MODE_B

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = {"Origin": "http://p044.test"}
ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN, **ORIGIN}
API = "/api/v1/simulator"


@pytest.fixture
def sim(monkeypatch, no_real_tick):  # noqa: F811
    monkeypatch.setattr(runtime, "_max_active_runs", 4)
    monkeypatch.setattr(settings, "SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", "minimal")  # the visitors' one admitted scenario
    return no_real_tick


@pytest_asyncio.fixture
async def started(sim):
    run_ids: list[str] = []
    yield run_ids
    for run_id in run_ids:
        await runtime.stop(run_id)


@pytest.fixture
def registry(monkeypatch, tmp_path: Path):
    """Uploads land in an isolated store on disk, registered into the runtime's own map (the routes find them)."""

    reg = ScenarioRegistry(
        lock=threading.RLock(),
        scenarios=runtime._scenarios,
        fixtures_dir=tmp_path / "fixtures",
        schema_path=REPO_ROOT / "fixtures" / "simulator" / "scenario.schema.json",
        local_state_dir=tmp_path / "state",
        utc_now=lambda: datetime.now(timezone.utc),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(runtime, "_scenario_registry", reg)
    added: list[str] = []
    yield reg, added, tmp_path
    for scenario_id in added:
        runtime._scenarios.pop(scenario_id, None)


async def _anon(client: AsyncClient) -> dict[str, str]:
    client.cookies.clear()
    r = await client.post(f"{API}/session/ensure")
    assert r.status_code == 200 and r.json()["actor_kind"] == "anon", r.text
    return ORIGIN


async def _start(client: AsyncClient, headers, started: list[str], scenario_id: str = "minimal"):
    r = await client.post(f"{API}/runs", headers=headers, json={"scenario_id": scenario_id, "intensity_percent": 0})
    if r.status_code == 200:
        started.append(r.json()["run_id"])
    return r


async def _ids(client: AsyncClient, headers, run_id: str) -> dict[str, str]:
    r = await client.get(f"{API}/runs/{run_id}/actions/participants-list", headers=headers)
    assert r.status_code == 200, r.text
    return {i["name"]: i["pid"] for i in r.json()["items"]}


async def _pay(client: AsyncClient, headers, run_id: str, amount: str) -> None:
    ids = await _ids(client, headers, run_id)
    r = await client.post(f"{API}/runs/{run_id}/actions/payment-real", headers=headers, json={
        "from_pid": ids["Coffee Shop"], "to_pid": ids["Ivan"], "equivalent": "UAH", "amount": amount})
    assert r.status_code == 200 and str(r.json().get("status")).upper() == "COMMITTED", r.text


async def _used(client: AsyncClient, headers, run_id: str) -> dict[tuple[str, str], Decimal]:
    r = await client.get(f"{API}/runs/{run_id}/actions/trustlines-list", headers=headers, params={"equivalent": "UAH"})
    assert r.status_code == 200, r.text
    return {(i["from_pid"], i["to_pid"]): Decimal(i["used"]) for i in r.json()["items"]}


async def _debts(db_session, pids) -> dict[tuple[str, str], Decimal]:
    async with sessionmaker_of(db_session)() as s:
        rows = (await s.execute(select(Participant.id, Participant.pid).where(Participant.pid.in_(set(pids))))).all()
        pid_of = {r.id: r.pid for r in rows}
        debts = (await s.execute(select(Debt).where(Debt.debtor_id.in_(pid_of), Debt.creditor_id.in_(pid_of)))).scalars()
        return {(pid_of[d.debtor_id], pid_of[d.creditor_id]): Decimal(d.amount) for d in debts if d.amount}


# ------------------------------------------------------------------------------------------------ identities


@pytest.mark.asyncio
async def test_a_restart_of_the_same_run_keeps_its_participants_and_debts(client: AsyncClient, db_session, started):
    headers = await _anon(client)
    run_id = (await _start(client, headers, started)).json()["run_id"]
    await _pay(client, headers, run_id, "3")
    ids_before, used_before = await _ids(client, headers, run_id), await _used(client, headers, run_id)
    assert set(used_before.values()) == {Decimal("3")}, used_before  # premise: the run has a debt to keep

    r = await client.post(f"{API}/runs/{run_id}/restart", headers=headers)
    assert r.status_code == 200, r.text

    assert await _ids(client, headers, run_id) == ids_before
    assert await _used(client, headers, run_id) == used_before
    await _pay(client, headers, run_id, "2")  # the same rows carry on: the debt grows, no second participant appears
    assert set((await _used(client, headers, run_id)).values()) == {Decimal("5")}
    assert await _debts(db_session, ids_before.values()) == {(ids_before["Coffee Shop"], ids_before["Ivan"]): Decimal("5")}


@pytest.mark.asyncio
async def test_two_runs_of_one_scenario_cannot_name_each_other_s_participants(client: AsyncClient, db_session, started):
    headers = await _anon(client)
    run_a = (await _start(client, headers, started)).json()["run_id"]
    await _pay(client, headers, run_a, "4")
    ids_a = await _ids(client, headers, run_a)
    run_b = (await _start(client, ADMIN, started)).json()["run_id"]  # another owner, the same scenario
    ids_b = await _ids(client, ADMIN, run_b)
    assert set(ids_a.values()).isdisjoint(ids_b.values()) and set(ids_a) == set(ids_b), (ids_a, ids_b)

    # B names A's participants in B's own run: they are not in B's perimeter.
    pay = await client.post(f"{API}/runs/{run_b}/actions/payment-real", headers=ADMIN, json={
        "from_pid": ids_a["Coffee Shop"], "to_pid": ids_a["Ivan"], "equivalent": "UAH", "amount": "1"})
    targets = await client.get(f"{API}/runs/{run_b}/payment-targets", headers=ADMIN,
                               params={"from_pid": ids_a["Coffee Shop"], "equivalent": "UAH"})
    lines = await client.get(f"{API}/runs/{run_b}/actions/trustlines-list", headers=ADMIN,
                             params={"equivalent": "UAH", "participant_pid": ids_a["Ivan"]})
    assert (pay.status_code, targets.status_code, lines.status_code) == (404, 404, 404), (pay.text, targets.text, lines.text)
    clearing = await client.post(f"{API}/runs/{run_b}/actions/clearing-real", headers=ADMIN, json={"equivalent": "UAH"})
    assert clearing.status_code == 200, clearing.text

    assert await _debts(db_session, ids_a.values()) == {(ids_a["Coffee Shop"], ids_a["Ivan"]): Decimal("4")}
    assert await _debts(db_session, ids_b.values()) == {}
    # Counter-check: B's own participants are found by the same routes.
    own = await client.get(f"{API}/runs/{run_b}/actions/trustlines-list", headers=ADMIN,
                           params={"equivalent": "UAH", "participant_pid": ids_b["Ivan"]})
    assert own.status_code == 200 and len(own.json()["items"]) == 1, own.text


@pytest.mark.asyncio
async def test_a_new_run_does_not_adopt_the_rows_an_unbound_seeding_left(client: AsyncClient, db_session, started):
    """Before T4405 every run of `minimal` adopted the rows `p1`/`p2` (pseudo key = "the simulator made it") with
    their debts. Seed them as an older run did, with a debt, and start a run: it starts from nothing."""

    template = runtime.get_scenario("minimal").raw
    async with sessionmaker_of(db_session)() as s:
        await RealScenarioSeeder().seed_scenario_into_db(session=s, scenario=template)
        await s.commit()
    headers = await _anon(client)
    run_id = (await _start(client, headers, started)).json()["run_id"]
    ids = await _ids(client, headers, run_id)

    # The run declares its binding, and the seeding paths hand it to the seeder (`RunIdentityUnbound` otherwise).
    assert runtime.get_run(run_id)._identity_run_id == run_id
    assert set(ids.values()).isdisjoint({"p1", "p2"}), ids
    assert set((await _used(client, headers, run_id)).values()) == {Decimal("0")}
    await _pay(client, headers, run_id, "1")
    assert await _debts(db_session, {"p1", "p2"}) == {}  # the legacy rows are not this run's


@pytest.mark.asyncio
async def test_the_seeder_refuses_a_run_scenario_that_is_not_bound_to_the_run(db_session) -> None:
    template = runtime.get_scenario("minimal").raw
    run_id = f"run_t4405_{uuid.uuid4().hex[:8]}"
    with pytest.raises(RunIdentityUnbound) as refused:
        await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=template, run_id=run_id)
    assert refused.value.pid == "p1"
    with pytest.raises(RunIdentityUnbound):
        await RealScenarioSeeder().load_real_participants(session=db_session, scenario=template, run_id=run_id)
    # Counter-check: the run's own bound copy seeds and loads.
    from app.core.simulator.run_perimeter import bind_run_identities

    bound = bind_run_identities(template, run_id=run_id)
    await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=bound, run_id=run_id)
    loaded = await RealScenarioSeeder().load_real_participants(session=db_session, scenario=bound, run_id=run_id)
    assert sorted(pid for _id, pid in loaded) == sorted(p["id"] for p in bound["participants"])


# ------------------------------------------------------------------------------------------------ admission


@pytest.mark.asyncio
async def test_a_visitor_reaches_only_admitted_scenarios_and_the_administrator_any(client: AsyncClient, started):
    headers = await _anon(client)
    runs_before = set(runtime._runs)
    refused = {
        "start": (await _start(client, headers, started, "trust-drift-decay-minimal")).status_code,
        "detail": (await client.get(f"{API}/scenarios/trust-drift-decay-minimal")).status_code,
        "preview": (await client.get(f"{API}/scenarios/trust-drift-decay-minimal/graph/preview",
                                     params={"equivalent": "UAH"})).status_code,
    }
    assert refused == {"start": 404, "detail": 404, "preview": 404}
    assert set(runtime._runs) == runs_before  # nothing created

    # Counter-checks: the admitted scenario starts and reads for the visitor; the administrator reaches the other.
    admitted = {
        "start": (await _start(client, headers, started, "minimal")).status_code,
        "detail": (await client.get(f"{API}/scenarios/minimal")).status_code,
        "preview": (await client.get(f"{API}/scenarios/minimal/graph/preview", params={"equivalent": "UAH"})).status_code,
    }
    assert admitted == {"start": 200, "detail": 200, "preview": 200}
    client.cookies.clear()
    assert (await _start(client, ADMIN, started, "trust-drift-decay-minimal")).status_code == 200
    assert (await client.get(f"{API}/scenarios/trust-drift-decay-minimal", headers=ADMIN)).status_code == 200


def _upload_body(scenario_id: str, *, stranger: bool = False) -> dict:
    return {"scenario": {
        "schema_version": "scenario/1", "scenario_id": scenario_id, "equivalents": ["UAH"],
        "participants": [{"id": "a", "name": "A", "type": "person"}, {"id": "b", "name": "B", "type": "person"}],
        "trustlines": [{"from": "a", "to": "stranger" if stranger else "b", "limit": "10", "equivalent": "UAH"}],
    }}


@pytest.mark.asyncio
async def test_only_the_administrator_uploads_a_scenario(client: AsyncClient, sim, registry) -> None:
    _reg, added, tmp_path = registry
    scenario_id = f"p044-upload-{uuid.uuid4().hex[:8]}"
    headers = await _anon(client)
    refused = await client.post(f"{API}/scenarios", headers=headers, json=_upload_body(scenario_id))
    assert refused.status_code == 403, refused.text
    assert scenario_id not in runtime._scenarios
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []  # nothing written

    client.cookies.clear()
    landed = await client.post(f"{API}/scenarios", headers=ADMIN, json=_upload_body(scenario_id))
    added.append(scenario_id)
    assert landed.status_code == 200, landed.text
    assert scenario_id in runtime._scenarios


@pytest.mark.asyncio
async def test_a_reference_to_an_undeclared_participant_refuses_the_run_before_it_exists(
    client: AsyncClient, started, registry
) -> None:
    _reg, added, _tmp = registry
    bad, good = f"p044-stranger-{uuid.uuid4().hex[:8]}", f"p044-declared-{uuid.uuid4().hex[:8]}"
    for scenario_id, stranger in ((bad, True), (good, False)):
        r = await client.post(f"{API}/scenarios", headers=ADMIN, json=_upload_body(scenario_id, stranger=stranger))
        added.append(scenario_id)
        assert r.status_code == 200, r.text

    runs_before = set(runtime._runs)
    r = await _start(client, ADMIN, started, bad)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["details"]["errors"][0]["path"] == "trustlines/0/to", r.text
    assert set(runtime._runs) == runs_before
    assert (await _start(client, ADMIN, started, good)).status_code == 200  # counter-check


# ------------------------------------------------------------------------------------------------ preview


@pytest.mark.asyncio
async def test_the_preview_is_the_template_and_shows_no_run_s_debt(client: AsyncClient, started) -> None:
    headers = await _anon(client)
    run_id = (await _start(client, headers, started)).json()["run_id"]
    await _pay(client, headers, run_id, "7")
    snap = await client.get(f"{API}/runs/{run_id}/graph/snapshot", headers=headers, params={"equivalent": "UAH"})
    assert [Decimal(link["used"]) for link in snap.json()["links"]] == [Decimal("7")], snap.text  # the run's own view

    preview = await client.get(f"{API}/scenarios/minimal/graph/preview", headers=headers, params={"equivalent": "UAH"})
    assert preview.status_code == 200, preview.text
    assert {n["id"] for n in preview.json()["nodes"]} == {"p1", "p2"}  # the template's own pids
    assert all(Decimal(str(link.get("used") or 0)) == 0 for link in preview.json()["links"]), preview.text
    assert all(n.get("net_balance") is None for n in preview.json()["nodes"]), preview.text
