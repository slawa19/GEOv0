"""044 stage 0 (`T4401` (b)): two visitors' runs on one server - what keeps them apart.

The public demo gives every visitor (an anonymous cookie actor) a run of their own on the same server (044, owner's
decision 1). Two things are asked of two such runs side by side:

1. ACCESS. One visitor cannot read, steer, subscribe to or act on the other's run (`_check_run_access`,
   `app/api/v1/simulator.py`, deny-by-default). Measured here route by route.
2. PERIMETER. A run's money - its payments, trust lines, clearing and snapshot - stays within ITS participants
   and does not touch the other run's debts. What the perimeter IS in the code was read before these assertions:
   it is the run scenario's participant list (`run_perimeter_pids`, `app/core/simulator/run_perimeter.py`; the
   Interact routes' `_run_perimeter`, `app/api/v1/simulator.py`), resolved BY PID against the shared
   `participants` table (`RealScenarioSeeder.load_real_participants`, `real_scenario_seeder.py`). A pid already in
   the table with the simulator's pseudo key is ADOPTED with its lines and debts (`real_scenario_seeder.py`, the
   comment above `SIMULATOR_PID_TAKEN`). Nothing in the perimeter names the run or its owner.

So two runs are apart exactly when their scenarios name disjoint pids. The demo starts every visitor on the SAME
scenario (`INTERACT_AUTOSTART_SCENARIO_ID = 'clearing-demo-10'`, `simulator-ui/v2/src/composables/
useInteractAutoBootstrapRun.ts`), and two shipped scenarios share pids (`clearing-demo-10` and
`trust-drift-decay-minimal` both name `alice` and `bob`). `test_two_visitors_runs_have_disjoint_perimeters` states
the precondition 044 sets for a public demo and is RED on the code it was written against (c99306c5): CLASS 1 for
stage 2, not fixed here (stage 0 changes no product code).

Real HTTP through the app; real payments through `payment-real` on the tier's PostgreSQL (mode B: seeding and
payments commit to a clone dropped after the test); the background tick is a recorder (`no_real_tick`); no sleep.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.simulator.runtime import runtime
from app.core.simulator.session import COOKIE_NAME
from app.db.models.debt import Debt
from app.db.models.participant import Participant
from tests.conftest import MODE_B, sessionmaker_of
from tests.p042_support import no_real_tick  # noqa: F401 - a fixture

pytestmark = MODE_B

ORIGIN = {"Origin": "http://p044.test"}
API = "/api/v1/simulator"

#: scenario -> (debtor, creditor) of its first trust line, the payment a test makes on it (the creditor is `from`).
FIRST_LINE = {
    "minimal": ("p2", "p1"),
    "trust-drift-decay-minimal": ("bob", "alice"),
    "clearing-demo-10": ("alice", "shop"),
}


@pytest.fixture
def two_visitors(monkeypatch, no_real_tick):  # noqa: F811
    # Two concurrent runs need a global limit of 2 (default 1, `app/config.py` SIMULATOR_MAX_ACTIVE_RUNS); the
    # runtime copies the setting at construction (`runtime_impl.py` `_max_active_runs`), so the copy is patched.
    monkeypatch.setattr(runtime, "_max_active_runs", 2)
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr("app.config.settings.SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN["Origin"])
    return no_real_tick


@pytest_asyncio.fixture
async def started(two_visitors):
    """Run ids a test started; stopped in this loop at teardown, before the tick stub is undone."""

    run_ids: list[str] = []
    yield run_ids
    for run_id in run_ids:
        await runtime.stop(run_id)


class Visitor:
    """One anonymous cookie actor. The client's cookie jar is shared, so each request installs this visitor's cookie."""

    def __init__(self, client: AsyncClient, cookie: str, owner_id: str, started: list[str]) -> None:
        self.client, self.cookie, self.owner_id, self.started = client, cookie, owner_id, started

    def _wear(self) -> None:
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE_NAME, self.cookie)

    async def get(self, path: str, **kw):
        self._wear()
        return await self.client.get(API + path, **kw)

    async def post(self, path: str, json=None):
        self._wear()
        return await self.client.post(API + path, headers=ORIGIN, json=json)

    async def start(self, scenario_id: str) -> str:
        r = await self.post("/runs", json={"scenario_id": scenario_id, "mode": "real", "intensity_percent": 0})
        assert r.status_code == 200, r.text
        self.started.append(str(r.json()["run_id"]))
        return self.started[-1]

    async def pay(self, run_id: str, scenario_id: str, amount: str = "1") -> None:
        debtor, creditor = FIRST_LINE[scenario_id]
        r = await self.post(
            f"/runs/{run_id}/actions/payment-real",
            json={"from_pid": debtor, "to_pid": creditor, "equivalent": "UAH", "amount": amount},
        )
        assert r.status_code == 200, r.text
        assert str(r.json().get("status") or "").upper() == "COMMITTED", r.text

    async def perimeter(self, run_id: str) -> set[str]:
        r = await self.get(f"/runs/{run_id}/actions/participants-list")
        assert r.status_code == 200, r.text
        return {item["pid"] for item in r.json()["items"]}

    async def used_by_line(self, run_id: str) -> dict[tuple[str, str], Decimal]:
        r = await self.get(f"/runs/{run_id}/actions/trustlines-list", params={"equivalent": "UAH"})
        assert r.status_code == 200, r.text
        return {(i["from_pid"], i["to_pid"]): Decimal(i["used"]) for i in r.json()["items"]}


async def _visitor(client: AsyncClient, started: list[str]) -> Visitor:
    client.cookies.clear()
    r = await client.post(f"{API}/session/ensure")
    assert r.status_code == 200, r.text
    assert r.json()["actor_kind"] == "anon", r.text
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie, "session/ensure set no cookie"
    return Visitor(client, cookie, str(r.json()["owner_id"]), started)


async def _debts_among(db_session, pids: set[str]) -> dict[tuple[str, str], Decimal]:
    """(debtor pid, creditor pid) -> amount, for every stored debt with BOTH sides in `pids` (a fresh session)."""

    async with sessionmaker_of(db_session)() as s:
        rows = (await s.execute(select(Participant.id, Participant.pid).where(Participant.pid.in_(pids)))).all()
        pid_of = {row.id: row.pid for row in rows}
        debts = (await s.execute(select(Debt).where(Debt.debtor_id.in_(pid_of), Debt.creditor_id.in_(pid_of)))).scalars()
        return {(pid_of[d.debtor_id], pid_of[d.creditor_id]): Decimal(d.amount) for d in debts if d.amount}


@pytest.mark.asyncio
async def test_one_visitor_cannot_reach_the_other_visitors_run(client: AsyncClient, started) -> None:
    a, b = await _visitor(client, started), await _visitor(client, started)
    assert a.owner_id != b.owner_id
    run_a = await a.start("minimal")
    run_b = await b.start("trust-drift-decay-minimal")
    await a.pay(run_a, "minimal")  # A's run has a domain event, artifacts and a seeded perimeter to be reached

    before = runtime.get_run_status(run_a).model_dump(mode="json")

    reads = [
        f"/runs/{run_a}",
        f"/runs/{run_a}/graph/snapshot?equivalent=UAH",
        f"/runs/{run_a}/metrics?equivalent=UAH&from_ms=0&to_ms=1000&step_ms=1000",
        f"/runs/{run_a}/bottlenecks?equivalent=UAH",
        f"/runs/{run_a}/artifacts",
        f"/runs/{run_a}/artifacts/events.ndjson",
        f"/runs/{run_a}/events?equivalent=UAH",
        f"/runs/{run_a}/actions/participants-list",
        f"/runs/{run_a}/actions/trustlines-list?equivalent=UAH",
        f"/runs/{run_a}/payment-targets?from_pid=p2&equivalent=UAH",
    ]
    writes = [
        (f"/runs/{run_a}/pause", None),
        (f"/runs/{run_a}/resume", None),
        (f"/runs/{run_a}/intensity", {"intensity_percent": 50}),
        (f"/runs/{run_a}/restart", None),
        (f"/runs/{run_a}/stop", None),
        (f"/runs/{run_a}/actions/payment-real", {"from_pid": "p2", "to_pid": "p1", "equivalent": "UAH", "amount": "1"}),
        (f"/runs/{run_a}/actions/clearing-real", {"equivalent": "UAH"}),
        (f"/runs/{run_a}/actions/trustline-create", {"from_pid": "p2", "to_pid": "p1", "equivalent": "UAH", "limit": "5"}),
        (f"/runs/{run_a}/actions/trustline-update", {"from_pid": "p1", "to_pid": "p2", "equivalent": "UAH", "new_limit": "5"}),
        (f"/runs/{run_a}/actions/trustline-close", {"from_pid": "p1", "to_pid": "p2", "equivalent": "UAH"}),
    ]
    answered: dict[str, int] = {}
    for path in reads:
        answered["GET " + path] = (await b.get(path)).status_code
    for path, body in writes:
        answered["POST " + path] = (await b.post(path, json=body)).status_code

    assert {k: v for k, v in answered.items() if v != 403} == {}, answered
    # Nothing of A's run moved under B's refused requests.
    assert runtime.get_run_status(run_a).model_dump(mode="json") == before
    assert (await a.used_by_line(run_a))[("p1", "p2")] == Decimal("1")

    # The active-run routes serve each visitor their own run, never the other's.
    for visitor, own in ((a, run_a), (b, run_b)):
        r = await visitor.get("/runs/active")
        assert r.status_code == 200 and r.json()["run_id"] == own, r.text
    snap_b = await b.get("/graph/snapshot", params={"equivalent": "UAH"})
    assert snap_b.status_code == 200, snap_b.text
    assert {n["id"] for n in snap_b.json()["nodes"]} == {"alice", "bob"}

    # Counter-check: the 403s are about ownership, not a broken route - the owner reaches the same run.
    assert (await a.get(f"/runs/{run_a}")).status_code == 200
    assert (await a.get(f"/runs/{run_a}/actions/participants-list")).status_code == 200
    # And an unknown run is 404, not 403.
    assert (await b.get("/runs/run_does_not_exist")).status_code == 404


@pytest.mark.asyncio
async def test_two_runs_of_disjoint_scenarios_stay_in_their_own_perimeters(
    client: AsyncClient, db_session, started
) -> None:
    # Counter-check of the RED test below: where the scenarios name disjoint pids, the runs ARE apart.
    a, b = await _visitor(client, started), await _visitor(client, started)
    run_a, run_b = await a.start("minimal"), await b.start("trust-drift-decay-minimal")
    await a.pay(run_a, "minimal", amount="3")
    await b.pay(run_b, "trust-drift-decay-minimal", amount="2")

    perimeter_a, perimeter_b = await a.perimeter(run_a), await b.perimeter(run_b)
    assert perimeter_a == {"p1", "p2"} and perimeter_b == {"alice", "bob"}

    # B's clearing runs on B's perimeter; A's debt is untouched by it.
    r = await b.post(f"/runs/{run_b}/actions/clearing-real", json={"equivalent": "UAH"})
    assert r.status_code == 200, r.text
    assert await _debts_among(db_session, perimeter_a) == {("p2", "p1"): Decimal("3")}
    assert await _debts_among(db_session, perimeter_b) == {("bob", "alice"): Decimal("2")}
    assert await a.used_by_line(run_a) == {("p1", "p2"): Decimal("3")}
    assert await b.used_by_line(run_b) == {("alice", "bob"): Decimal("2")}
    # A payment of B's run cannot name A's participant: it is outside B's perimeter.
    r = await b.post(
        f"/runs/{run_b}/actions/payment-real",
        json={"from_pid": "p2", "to_pid": "p1", "equivalent": "UAH", "amount": "1"},
    )
    assert r.status_code == 404, r.text
    assert await _debts_among(db_session, perimeter_a) == {("p2", "p1"): Decimal("3")}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_a", "scenario_b"),
    [
        ("minimal", "minimal"),  # the public demo: every visitor auto-starts the same scenario
        ("clearing-demo-10", "trust-drift-decay-minimal"),  # two shipped scenarios that both name alice and bob
    ],
    ids=["same_scenario", "shipped_scenarios_sharing_pids"],
)
async def test_two_visitors_runs_have_disjoint_perimeters(
    client: AsyncClient, db_session, started, scenario_a: str, scenario_b: str
) -> None:
    """044's precondition for a public demo: two visitors' runs share no participant and no debt.

    RED on c99306c5 (CLASS 1 for 044 stage 2): the perimeter is the scenario's pid list resolved against the shared
    participants table, so two runs whose scenarios name a pid act on the same participant row, its lines and debts.
    """

    a, b = await _visitor(client, started), await _visitor(client, started)
    run_a, run_b = await a.start(scenario_a), await b.start(scenario_b)
    assert run_a != run_b
    await a.pay(run_a, scenario_a)  # only A pays

    # The values are formatted for the quote: pids, and debt as strings.
    shared = sorted((await a.perimeter(run_a)) & (await b.perimeter(run_b)))
    # What B's own run shows of debts it never made: lines of B's view whose `used` is not zero.
    foreign_debt_in_b = {f"{line[0]}->{line[1]}": str(used) for line, used in (await b.used_by_line(run_b)).items() if used != 0}

    assert {"shared_participants": shared, "debt_b_never_made": foreign_debt_in_b} == {
        "shared_participants": [],
        "debt_b_never_made": {},
    }
