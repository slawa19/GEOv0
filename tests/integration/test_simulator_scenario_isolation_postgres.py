"""D1 (live acceptance on `97059b4d`, 2026-10-10): two shipped scenarios must not share identities in one database.

WHAT WAS WRONG. `riverside-town-50-realistic-v2` and `greenfield-village-100-realistic-v2` named the SAME 50
participants (`PID_U0001..` - `PID_U0050..`) with different names, types and statuses. The seeder adopts an existing
simulated participant as it is (`real_scenario_seeder.py`, the `have_p` skip) - the pseudo key proves the simulator
made the row, not which scenario it belongs to - so the second scenario ran on the first one's people: their names,
types, statuses, trust lines, limits and debts. Since 030 S3b its own lines to a participant the first scenario froze
are refused (`SCENARIO_TRUSTLINE_REFUSED`, `participant_suspended`), which is how it surfaced: Interact answered 409,
Auto-Run stopped with `REAL_MODE_TICK_FAILED_REPEATED`.

THE RULE (arbiter decision 2026-10-10): distinct shipped scenarios use distinct identities
(`<scenario_id>:<community pid>`, derived by `scripts/generate_simulator_seed_scenarios.py`); repeated runs of ONE
scenario keep theirs, with the state they accumulated.

Stand: the product path - `POST /simulator/runs` (real mode, the runtime's own heartbeat), Interact seeding through
`GET .../actions/trustlines-list`, `POST .../actions/payment-real` - in mode B. The DATABASE is asserted directly:
the graph a run shows is built from its scenario, so its labels and counts cannot show contamination.

WHAT THIS DOES NOT SEE: scenarios outside the two named here (the allowlist guard in
`tests/unit/test_simulator_scenario_allowlist_and_archives.py` covers the default list; nothing covers wildcard or
uploaded scenarios); a clearing inside the second scenario; the equivalent `UAH`, which the two scenarios share by
decision - a stop or an integrity hold on it reaches both.
"""

from __future__ import annotations

import asyncio
import copy
import time
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from app.config import settings
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder, scenario_participant_status
from app.core.simulator.runtime import runtime
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.conftest import MODE_B, sessionmaker_of

pytestmark = MODE_B

RIVERSIDE = "riverside-town-50-realistic-v2"
GREENFIELD = "greenfield-village-100-realistic-v2"
ORDERS = [(RIVERSIDE, GREENFIELD), (GREENFIELD, RIVERSIDE)]

#: The longest a test waits for Auto-Run to commit its first payment.
AUTO_RUN_DEADLINE_S = 120.0


@pytest_asyncio.fixture
async def stopped_runs(client, monkeypatch):
    """Stop whatever run a test started, pass or fail (as `test_simulator_real_snapshot_db_enrichment.py`)."""
    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    started: list[str] = []
    try:
        yield started
    finally:
        for run_id in started:
            try:
                await runtime.stop(run_id)
            except Exception:
                pass


def _admin() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


def _declared(scenario_id: str) -> dict:
    return runtime.get_scenario(scenario_id).raw


def _declared_roster(scenario: dict) -> dict[str, tuple[str, str, str]]:
    return {
        p["id"]: (p["name"], p["type"], scenario_participant_status(p.get("status")))
        for p in scenario["participants"]
    }


def _declared_lines(scenario: dict) -> dict[tuple[str, str, str], tuple[Decimal, dict, str]]:
    return {
        (t["from"], t["to"], t["equivalent"]): (Decimal(str(t["limit"])), t["policy"], "active")
        for t in scenario["trustlines"]
    }


async def _state(db_session, pids) -> dict:
    """What the database holds for `pids`: their rows, every line and every non-zero debt that touches one of them."""
    pids = sorted(pids)
    a, b = aliased(Participant), aliased(Participant)
    async with sessionmaker_of(db_session)() as s:
        roster = {
            r.pid: (r.display_name, r.type, r.status)
            for r in (
                await s.execute(
                    select(Participant.pid, Participant.display_name, Participant.type, Participant.status).where(
                        Participant.pid.in_(pids)
                    )
                )
            ).all()
        }
        lines = {
            (r[0], r[1], r[2]): (r[3], r[4], r[5])
            for r in (
                await s.execute(
                    select(a.pid, b.pid, Equivalent.code, TrustLine.limit, TrustLine.policy, TrustLine.status)
                    .join(a, a.id == TrustLine.from_participant_id)
                    .join(b, b.id == TrustLine.to_participant_id)
                    .join(Equivalent, Equivalent.id == TrustLine.equivalent_id)
                    .where(or_(a.pid.in_(pids), b.pid.in_(pids)))
                )
            ).all()
        }
        debts = {
            (r[0], r[1], r[2]): r[3]
            for r in (
                await s.execute(
                    select(a.pid, b.pid, Equivalent.code, Debt.amount)
                    .join(a, a.id == Debt.debtor_id)
                    .join(b, b.id == Debt.creditor_id)
                    .join(Equivalent, Equivalent.id == Debt.equivalent_id)
                    .where(or_(a.pid.in_(pids), b.pid.in_(pids)), Debt.amount != 0)
                )
            ).all()
        }
    return {"roster": roster, "lines": lines, "debts": debts}


async def _start(client, stopped_runs, scenario_id: str) -> str:
    """A real-mode run at intensity 0: its heartbeat ticks, and no background payment moves anything."""
    response = await client.post(
        "/api/v1/simulator/runs",
        headers=_admin(),
        json={"scenario_id": scenario_id, "mode": "real", "intensity_percent": 0},
    )
    assert response.status_code == 200, response.text
    run_id = str(response.json()["run_id"])
    stopped_runs.append(run_id)
    return run_id


async def _interact_seeds(client, run_id: str) -> None:
    response = await client.get(
        f"/api/v1/simulator/runs/{run_id}/actions/trustlines-list", headers=_admin(), params={"equivalent": "UAH"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"], "the run lists no trust lines"


async def _pay_one_line(client, run_id: str, scenario: dict) -> tuple[str, str]:
    """One manual payment over one declared line between two active participants: the debtor pays the creditor."""
    roster = _declared_roster(scenario)
    line = next(
        t for t in scenario["trustlines"] if roster[t["from"]][2] == "active" and roster[t["to"]][2] == "active"
    )
    debtor, creditor = line["to"], line["from"]  # `from -> to` is creditor -> debtor
    response = await client.post(
        f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
        headers=_admin(),
        json={"from_pid": debtor, "to_pid": creditor, "equivalent": "UAH", "amount": "1.00"},
    )
    assert response.status_code == 200, response.text
    return debtor, creditor


async def _a_scenario_ran_and_left_debt(client, db_session, stopped_runs, scenario_id: str) -> dict:
    """The first scenario is seeded, one payment is committed in it, its run is stopped; returns what it left."""
    scenario = _declared(scenario_id)
    run_id = await _start(client, stopped_runs, scenario_id)
    await _interact_seeds(client, run_id)
    debtor, creditor = await _pay_one_line(client, run_id, scenario)
    await runtime.stop(run_id)

    left = await _state(db_session, _declared_roster(scenario))
    # Non-vacuous precondition: there IS committed debt and a frozen participant to inherit or to disturb.
    assert left["debts"] == {(debtor, creditor, "UAH"): Decimal("1.00")}, left["debts"]
    assert "suspended" in {status for _n, _t, status in left["roster"].values()}
    assert left["roster"] == _declared_roster(scenario)
    return left


def _assert_is_its_declaration(state: dict, scenario: dict, *, whose: str) -> None:
    assert state["roster"] == _declared_roster(scenario), f"{whose}: the participants are not the declared ones"
    assert state["lines"] == _declared_lines(scenario), f"{whose}: the trust lines are not the declared ones"


@pytest.mark.asyncio
@pytest.mark.parametrize(("first", "second"), ORDERS)
async def test_the_second_scenario_runs_on_its_own_participants_and_leaves_the_first_alone(
    client, db_session, stopped_runs, first: str, second: str
) -> None:
    left_by_first = await _a_scenario_ran_and_left_debt(client, db_session, stopped_runs, first)
    declared = _declared(second)

    run_id = await _start(client, stopped_runs, second)
    await _interact_seeds(client, run_id)

    # Before anything moves in the second run (intensity 0): it is exactly its declaration, and owes nothing.
    seeded = await _state(db_session, _declared_roster(declared))
    _assert_is_its_declaration(seeded, declared, whose=second)
    assert seeded["debts"] == {}, f"{second} inherited debts"

    # A controlled payment, then Auto-Run: both operate, and both stay inside the second scenario.
    debtor, creditor = await _pay_one_line(client, run_id, declared)
    after_payment = await _state(db_session, _declared_roster(declared))
    assert after_payment["debts"] == {(debtor, creditor, "UAH"): Decimal("1.00")}

    run = runtime.get_run(run_id)
    response = await client.post(
        f"/api/v1/simulator/runs/{run_id}/intensity", headers=_admin(), json={"intensity_percent": 100}
    )
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + AUTO_RUN_DEADLINE_S
    while run._real_money_committed_payments_total < 1 and run.state == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
    assert run.state == "running", run.last_error
    assert run._real_money_committed_payments_total >= 1, "Auto-Run committed no payment"
    await runtime.stop(run_id)

    operated = await _state(db_session, _declared_roster(declared))
    own = set(_declared_roster(declared))
    assert len(operated["debts"]) > 1, "Auto-Run left no debt in the database"
    assert all(d in own and c in own for d, c, _eq in operated["debts"]), "a debt of the second run left its scenario"
    assert all(f in own and t in own for f, t, _eq in operated["lines"]), "a line of the second run left its scenario"

    assert await _state(db_session, left_by_first["roster"]) == left_by_first, f"{second} disturbed {first}"


@pytest.mark.asyncio
async def test_a_second_run_of_the_same_scenario_keeps_what_the_first_left_and_thaws_nobody(
    client, db_session, stopped_runs
) -> None:
    """Counter-check: isolation between scenarios must not become a reset between runs of one scenario."""
    left = await _a_scenario_ran_and_left_debt(client, db_session, stopped_runs, RIVERSIDE)

    run_id = await _start(client, stopped_runs, RIVERSIDE)
    await _interact_seeds(client, run_id)

    assert await _state(db_session, left["roster"]) == left


def _as_before_the_namespaces(scenario: dict) -> dict:
    """The scenario with the participant ids it had before 2026-10-10: the community pid, no scenario prefix."""

    def old(pid: str) -> str:
        return pid.rsplit(":", 1)[-1]

    legacy = copy.deepcopy(scenario)
    for p in legacy["participants"]:
        p["id"] = old(p["id"])
    for t in legacy["trustlines"]:
        t["from"], t["to"] = old(t["from"]), old(t["to"])
    return legacy


@pytest.mark.asyncio
async def test_a_database_seeded_before_the_namespaces_does_not_contaminate_either_scenario(
    client, db_session, stopped_runs
) -> None:
    legacy = _as_before_the_namespaces(_declared(RIVERSIDE))
    await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=legacy)
    await db_session.commit()
    old_rows = await _state(db_session, _declared_roster(legacy))
    assert len(old_rows["roster"]) == 50 and "suspended" in {s for _n, _t, s in old_rows["roster"].values()}

    for scenario_id in (GREENFIELD, RIVERSIDE):
        declared = _declared(scenario_id)
        run_id = await _start(client, stopped_runs, scenario_id)
        await _interact_seeds(client, run_id)
        await runtime.stop(run_id)
        seeded = await _state(db_session, _declared_roster(declared))
        _assert_is_its_declaration(seeded, declared, whose=scenario_id)
        assert seeded["debts"] == {}

    assert await _state(db_session, _declared_roster(legacy)) == old_rows
