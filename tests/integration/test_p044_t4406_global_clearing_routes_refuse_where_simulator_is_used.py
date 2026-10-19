"""044 `T4406` (closing §19.5 review of 044, class 1): the hub-wide clearing routes refuse where simulator use is detected.

`GET /api/v1/clearing/cycles` and `POST /api/v1/clearing/auto` authenticate ANY participant, and a participant token
is free (self-registration `POST /participants` + challenge/sign). Neither is limited to a run: the diagnostics
answer with the plan of the WHOLE equivalent (participant ids and debt amounts), the pass clears it hub-wide
(`allowed_participant_pids=None`). On a server with per-visitor simulator runs, visitor B therefore read visitor A's
scoped participants and debts and cleared A's cycle - the cross-run hub-wide clearing that 023 decision 9 forbids for
the PERIODIC pass (`check_periodic_isolation`), which these routes did not ask. Protocol §7.2: a simulator clears
"в периметре рана".

The fix applies decision 9's detection (a `simulator_runs` row, or `SIMULATOR_DB_ENABLED = false`) to both routes:
409 E008 with `details.reason` = the periodic refusal's reason. The simulator's own in-run clearing (perimeter-bound)
is not asked and keeps working.

* Reproducer: A's run holds a clearable cycle, B self-registers and calls both routes - RED before the fix (B sees
  A's ids and amounts; A's cycle is cleared), refused after, A's debts unchanged.
* Anti-vacuum: A's own `clearing-real` then clears that very cycle - the debts B could not touch WERE clearable.
* Counter-checks: a standalone hub (no simulator use detected) - both routes answer and the pass clears its cycle;
  `SIMULATOR_DB_ENABLED = false` - refused with `simulator_persistence_disabled`.

Real HTTP through the app on the tier's PostgreSQL (mode B); the background tick is a recorder (`no_real_tick`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.core.simulator.runtime import runtime
from app.core.simulator.session import COOKIE_NAME
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import debt_uuid, ring, seed_graph
from tests.p042_support import no_real_tick  # noqa: F401 - a fixture

pytestmark = MODE_B

ORIGIN = {"Origin": "http://p044.test"}
API = "/api/v1/simulator"
CYCLE_SCENARIO = "clearing-demo-10"
#: Three payments of `clearing-demo-10` that close a cycle, by participant NAME: (payer, payee). Each goes over the
#: payee's direct trust line to the payer (shop->alice, alice->farmer, farmer->shop), so the debts are
#: alice owes shop, farmer owes alice, shop owes farmer - a cycle of three.
CYCLE_PAYMENTS = (("Алиса", "Магазин"), ("Фермер", "Алиса"), ("Магазин", "Фермер"))


@pytest.fixture
def two_visitors(monkeypatch, no_real_tick):  # noqa: F811
    monkeypatch.setattr(runtime, "_max_active_runs", 2)
    monkeypatch.setattr(settings, "SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr(settings, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    monkeypatch.setattr(settings, "SIMULATOR_SCENARIO_ALLOWLIST", f"{CYCLE_SCENARIO},minimal")
    return no_real_tick


@pytest_asyncio.fixture
async def started(two_visitors):
    run_ids: list[str] = []
    yield run_ids
    for run_id in run_ids:
        await runtime.stop(run_id)


async def _anon(client: AsyncClient) -> str:
    client.cookies.clear()
    r = await client.post(f"{API}/session/ensure")
    assert r.status_code == 200 and r.json()["actor_kind"] == "anon", r.text
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie, "session/ensure set no cookie"
    return cookie


def _wear(client: AsyncClient, cookie: str) -> None:
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, cookie)


async def _start(client: AsyncClient, cookie: str, started: list[str], scenario_id: str) -> str:
    _wear(client, cookie)
    r = await client.post(f"{API}/runs", headers=ORIGIN, json={"scenario_id": scenario_id, "intensity_percent": 0})
    assert r.status_code == 200, r.text
    started.append(r.json()["run_id"])
    return started[-1]


async def _ids(client: AsyncClient, cookie: str, run_id: str) -> dict[str, str]:
    _wear(client, cookie)
    r = await client.get(f"{API}/runs/{run_id}/actions/participants-list")
    assert r.status_code == 200, r.text
    return {i["name"]: i["pid"] for i in r.json()["items"]}


async def _pay(client: AsyncClient, cookie: str, run_id: str, payer: str, payee: str, amount: str) -> None:
    _wear(client, cookie)
    r = await client.post(
        f"{API}/runs/{run_id}/actions/payment-real",
        headers=ORIGIN,
        json={"from_pid": payer, "to_pid": payee, "equivalent": "UAH", "amount": amount},
    )
    assert r.status_code == 200 and str(r.json().get("status")).upper() == "COMMITTED", r.text


async def _debts(db_session, pids) -> dict[tuple[str, str], Decimal]:
    """(debtor pid, creditor pid) -> amount for every non-zero stored debt with both sides in `pids`."""

    async with sessionmaker_of(db_session)() as s:
        rows = (await s.execute(select(Participant.id, Participant.pid).where(Participant.pid.in_(set(pids))))).all()
        pid_of = {r.id: r.pid for r in rows}
        debts = (await s.execute(select(Debt).where(Debt.debtor_id.in_(pid_of), Debt.creditor_id.in_(pid_of)))).scalars()
        return {(pid_of[d.debtor_id], pid_of[d.creditor_id]): Decimal(d.amount) for d in debts if d.amount}


async def _a_run_with_a_clearable_cycle(client: AsyncClient, db_session, started: list[str]):
    cookie = await _anon(client)
    run_id = await _start(client, cookie, started, CYCLE_SCENARIO)
    ids = await _ids(client, cookie, run_id)
    for payer, payee in CYCLE_PAYMENTS:
        await _pay(client, cookie, run_id, ids[payer], ids[payee], "5")
    cycle = {(ids[payer], ids[payee]): Decimal("5") for payer, payee in CYCLE_PAYMENTS}
    assert await _debts(db_session, ids.values()) == cycle  # premise: A's run holds exactly this cycle
    return cookie, run_id, ids, cycle


def _reason(response) -> str | None:
    try:
        return ((response.json().get("error") or {}).get("details") or {}).get("reason")
    except ValueError:
        return None


@pytest.mark.asyncio
async def test_another_participant_cannot_read_or_clear_a_visitor_runs_cycle_through_the_global_routes(
    client: AsyncClient, db_session, started, auth_user
) -> None:
    cookie_a, run_a, ids_a, cycle = await _a_run_with_a_clearable_cycle(client, db_session, started)
    cookie_b = await _anon(client)
    await _start(client, cookie_b, started, "minimal")  # visitor B has a run of their own on the same server
    client.cookies.clear()
    b_token = auth_user["headers"]  # B's participant JWT: self-registration + challenge/sign, nothing else

    cycles = await client.get("/api/v1/clearing/cycles", params={"equivalent": "UAH"}, headers=b_token)
    auto = await client.post("/api/v1/clearing/auto", params={"equivalent": "UAH"}, headers=b_token)

    assert {
        "cycles": (cycles.status_code, _reason(cycles)),
        "a_participants_shown_to_b": sorted(pid for pid in ids_a.values() if pid in cycles.text),
        "auto": (auto.status_code, _reason(auto)),
        "a_debts_after_b": await _debts(db_session, ids_a.values()),
    } == {
        "cycles": (409, "simulator_real_runs_in_database"),
        "a_participants_shown_to_b": [],
        "auto": (409, "simulator_real_runs_in_database"),
        "a_debts_after_b": cycle,
    }
    assert cycles.json()["error"]["code"] == auto.json()["error"]["code"] == "E008"

    # Anti-vacuum, and the simulator's own clearing still works: A's in-run pass clears that very cycle.
    _wear(client, cookie_a)
    own = await client.post(f"{API}/runs/{run_a}/actions/clearing-real", headers=ORIGIN, json={"equivalent": "UAH"})
    assert own.status_code == 200, own.text
    assert await _debts(db_session, ids_a.values()) == {}


# ------------------------------------------------------------------------------------------------ counter-checks

HUB_CODE = "PQT"
HUB = ring(["p4406a", "p4406b", "p4406c"], ["4", "4", "4"], [debt_uuid(0x4406, k) for k in range(3)])


async def _hub_debts(db_session) -> dict[str, Decimal]:
    async with sessionmaker_of(db_session)() as s:
        rows = (await s.execute(select(Debt.id, Debt.amount).where(Debt.id.in_([e.debt_id for e in HUB]))))
        return {str(row.id): Decimal(row.amount) for row in rows if row.amount}


@pytest.mark.asyncio
async def test_on_a_standalone_hub_both_routes_answer_and_the_pass_clears_its_cycle(
    client: AsyncClient, db_session, auth_user
) -> None:
    await seed_graph(db_session, HUB_CODE, HUB)
    assert len(await _hub_debts(db_session)) == 3  # premise: a cycle to show and clear

    cycles = await client.get("/api/v1/clearing/cycles", params={"equivalent": HUB_CODE}, headers=auth_user["headers"])
    assert cycles.status_code == 200, cycles.text
    assert sorted(edge["debt_id"] for cycle in cycles.json()["cycles"] for edge in cycle) == sorted(
        str(e.debt_id) for e in HUB
    )
    auto = await client.post("/api/v1/clearing/auto", params={"equivalent": HUB_CODE}, headers=auth_user["headers"])
    assert auto.status_code == 200 and auto.json()["cleared_cycles"] == 1, auto.text
    assert await _hub_debts(db_session) == {}


@pytest.mark.asyncio
async def test_without_simulator_persistence_both_routes_are_refused(
    client: AsyncClient, db_session, auth_user, monkeypatch
) -> None:
    await seed_graph(db_session, HUB_CODE, HUB)
    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", False)

    cycles = await client.get("/api/v1/clearing/cycles", params={"equivalent": HUB_CODE}, headers=auth_user["headers"])
    auto = await client.post("/api/v1/clearing/auto", params={"equivalent": HUB_CODE}, headers=auth_user["headers"])

    assert (cycles.status_code, _reason(cycles), auto.status_code, _reason(auto)) == (
        409,
        "simulator_persistence_disabled",
        409,
        "simulator_persistence_disabled",
    )
    assert len(await _hub_debts(db_session)) == 3
