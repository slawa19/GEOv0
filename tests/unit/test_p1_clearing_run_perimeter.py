"""RT-010-4: one run must not clear a cycle made of another run's participants.

Program 010, finding `F-010-3`.

`action_clearing_real` checks that the caller owns the run and then hands
`ClearingService` nothing but an equivalent code (`app/api/v1/simulator.py:1712`, `:1738`,
`:1772`).  Cycle detection is bounded by the equivalent and by nothing else
(`app/core/clearing/service.py:397`, `:485`, `:856-859`), and execution re-reads the rows by
`debt_id` without checking whose they are (`:1266-1271`).  So the owner of run A can reduce
the obligations of run B's participants.

The effect is durable, not cosmetic: the cycle's debts are reduced, zeroed rows are deleted
and a COMMITTED clearing transaction is written (`:1473-1483`, `:1445-1465`).  Net positions
are preserved, but the gross volume of another run's mutual debt is changed by someone with
no relationship to it, and run A's operator is told the amount as their own result.

This is the same class as `C-A1a-003`, which program 009 closed for participant resolution
(`F-009-1`).  That fix scoped the ENTRANCE of the mutating routes; this one is about the
money path behind the entrance, which the perimeter never reached.

The stand deliberately uses no participant of run A at all.  Run A holds a1/a2/a3, the cycle
is b1 -> b2 -> b3 -> b1, and the request names only the equivalent - which is all the route
accepts.  Nothing here is contrived: the request is exactly what the UI sends.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.clearing.service import ClearingOccurrenceRefused, ClearingService
from app.utils.exceptions import GeoException
from app.core.simulator.models import RunRecord
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup
from tests.conftest import MODE_B
from tests.p023_support import TEST_PLAN_ID, occurrence_of, planned_cycles

_EQ = "RPX"


@pytest.fixture
def run_a_only(monkeypatch):
    """Register run A, whose scenario contains a1/a2/a3 and nobody else."""

    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda run_id: SimpleNamespace(
            run_id=str(run_id),
            state="running",
            owner_id="",
            _real_seeded=True,
            _real_seeding_lock=None,
        ),
    )

    run = RunRecord(run_id="run-a", scenario_id="scn-a", mode="real", state="running")
    run._scenario_raw = {
        "participants": [
            {"id": "a1", "name": "A1", "type": "person", "status": "active"},
            {"id": "a2", "name": "A2", "type": "person", "status": "active"},
            {"id": "a3", "name": "A3", "type": "person", "status": "active"},
        ],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, "run-a", run)
    return simulator_module


async def _seed_two_runs(db_session):
    """Six participants in one equivalent; the closed debt cycle belongs to run B only."""

    eq = Equivalent(code=_EQ, precision=2, is_active=True)
    db_session.add(eq)

    people: dict[str, Participant] = {}
    for pid in ("a1", "a2", "a3", "b1", "b2", "b3"):
        p = Participant(
            id=uuid.uuid4(),
            pid=pid,
            display_name=pid.upper(),
            public_key=pid * 16,
            type="person",
            status="active",
            profile={},
        )
        people[pid] = p
        db_session.add(p)
    await db_session.commit()

    # A debt debtor -> creditor is only clearable when a LIVE trustline runs the other way
    # (creditor -> debtor), which is what the detection query joins on.
    cycle = [("b1", "b2"), ("b2", "b3"), ("b3", "b1")]
    for debtor, creditor in cycle:
        db_session.add(
            TrustLine(
                from_participant_id=people[creditor].id,
                to_participant_id=people[debtor].id,
                equivalent_id=eq.id,
                limit=Decimal("1000"),
                policy={"auto_clearing": True},
                status="active",
            )
        )
        async with debt_fixture_setup(db_session, label="setup"):
            db_session.add(
                Debt(
                    debtor_id=people[debtor].id,
                    creditor_id=people[creditor].id,
                    equivalent_id=eq.id,
                    amount=Decimal("100"),
                )
            )
    await db_session.commit()
    return eq, people


def _occurrence_of(cycle, eq_id):
    """025 `T2508.1`: the plan occurrence of the detected run-B cycle (in detection's order), declared 100."""

    return occurrence_of(cycle, equivalent_id=eq_id, amount="100", plan_id=TEST_PLAN_ID, ordinal=0)


async def _debt_amounts(db_session, eq_id) -> list[Decimal]:
    rows = (
        await db_session.execute(select(Debt.amount).where(Debt.equivalent_id == eq_id))
    ).scalars().all()
    return sorted(Decimal(str(a)) for a in rows)


# MODE B since 2026-09-28 (programme 023 slice (d)): the route clears through the common runner, which opens
# sessions of its own - in mode A the seed is uncommitted and invisible to them (as for `POST /payments`).
@MODE_B
@pytest.mark.asyncio
async def test_clearing_real_does_not_touch_another_runs_participants(
    client, db_session, run_a_only
):
    eq, _people = await _seed_two_runs(db_session)
    # Capture the id now: the route commits through this same session, and touching an
    # expired ORM attribute afterwards would be sync IO inside async code.
    eq_id = eq.id
    before = await _debt_amounts(db_session, eq_id)
    assert before == [Decimal("100")] * 3, before

    resp = await client.post(
        "/api/v1/simulator/runs/run-a/actions/clearing-real",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json={"equivalent": _EQ, "client_action_id": "rt_010_4"},
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()

    # A column select, so the values come from the database rather than from identity map.
    after = await _debt_amounts(db_session, eq_id)

    assert int(payload["cleared_cycles"]) == 0, (
        "run A was told it cleared cycles that belong to run B: "
        f"{payload['cleared_cycles']} cycles, amount {payload['total_cleared_amount']}; "
        f"and run B's debts went from {before} to {after}"
    )
    assert after == before, (
        "run A cleared a cycle made entirely of run B's participants: the debts of a run "
        f"the caller has no relationship to changed from {before} to {after}. Zeroed rows "
        "are deleted, so an empty list means the obligations are gone, not merely reduced"
    )


@pytest.fixture
def run_b_too(monkeypatch):
    """Same fixture, but the run owns b1/b2/b3 - the participants of the cycle."""

    import app.api.v1.simulator as simulator_module

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    monkeypatch.setattr(
        simulator_module.runtime,
        "get_run",
        lambda run_id: SimpleNamespace(
            run_id=str(run_id),
            state="running",
            owner_id="",
            _real_seeded=True,
            _real_seeding_lock=None,
        ),
    )
    run = RunRecord(run_id="run-b", scenario_id="scn-b", mode="real", state="running")
    run._scenario_raw = {
        "participants": [
            {"id": "b1", "name": "B1", "type": "person", "status": "active"},
            {"id": "b2", "name": "B2", "type": "person", "status": "active"},
            {"id": "b3", "name": "B3", "type": "person", "status": "active"},
        ],
        "trustlines": [],
    }
    monkeypatch.setitem(simulator_module.runtime._runs, "run-b", run)
    return simulator_module


@MODE_B
@pytest.mark.asyncio
async def test_the_owning_run_still_clears_its_own_cycle(client, db_session, run_b_too):
    """Anti-vacuum: the perimeter must refuse strangers, not disable clearing.

    Without this, a fix that simply broke cycle detection would make the test above pass.
    """

    eq, _people = await _seed_two_runs(db_session)
    eq_id = eq.id
    before = await _debt_amounts(db_session, eq_id)
    assert before == [Decimal("100")] * 3, before

    resp = await client.post(
        "/api/v1/simulator/runs/run-b/actions/clearing-real",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json={"equivalent": _EQ, "client_action_id": "rt_010_4_positive"},
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    after = await _debt_amounts(db_session, eq_id)

    assert int(payload["cleared_cycles"]) == 1, (
        f"the run that owns the cycle could not clear it: {payload}"
    )
    assert after == [], (
        "the owning run's cycle should be cleared to zero and the rows removed, "
        f"got {after}"
    )


# The two layers are independent on purpose: detection so a foreign cycle is never found,
# execution so one that arrives by any other route is still refused.  The route-level tests
# above pass with EITHER layer alone, so each needs its own case - otherwise removing one
# leaves the suite green and the defence silently halved.


@pytest.mark.asyncio
async def test_detection_layer_does_not_return_a_foreign_cycle(db_session):
    # 035 A2a (2026-10-08): "detection" is the PLANNER's snapshot and plan now (`planned_cycles`, the perimeter being
    # `flow_planner.load_snapshot`'s own `allowed_participant_pids`) - the retired detectors are no longer what a
    # pass or the diagnostic reads. The two assertions are unchanged; the third is the half that
    # `test_the_sql_producer_itself_is_scoped` carried for the SQL producer: the OWNING run still gets its cycle,
    # so "empty for a stranger" is not "empty for everyone".
    eq, _people = await _seed_two_runs(db_session)

    unscoped = await planned_cycles(db_session, _EQ)
    assert len(unscoped) == 1, (
        "the stand must contain exactly one detectable cycle, otherwise this test is not "
        f"measuring the perimeter: {unscoped}"
    )

    scoped = await planned_cycles(db_session, _EQ, allowed_participant_pids={"a1", "a2", "a3"})
    assert scoped == [], f"detection returned another run's cycle: {scoped}"

    own = await planned_cycles(db_session, _EQ, allowed_participant_pids={"b1", "b2", "b3"})
    assert [{e["debtor"] for e in cycle} for cycle in own] == [{"b1", "b2", "b3"}], (
        f"the perimeter must admit the owning run, not reject everything: {own}"
    )
    # Two of the three vertices inside is still outside: the rule is per edge endpoint, not a vertex count.
    assert await planned_cycles(db_session, _EQ, allowed_participant_pids={"b1", "b2", "a1"}) == []


@pytest.mark.asyncio
async def test_detection_treats_an_empty_perimeter_as_nobody(db_session):
    """`_run_scoped_pids_or_none` returns an empty set when the perimeter cannot be built.

    Reading that as "no restriction" would be a literal return of F-009-1.
    """

    await _seed_two_runs(db_session)

    assert len(await planned_cycles(db_session, _EQ)) == 1  # control: without a perimeter the cycle is there
    assert await planned_cycles(db_session, _EQ, allowed_participant_pids=set()) == []


@MODE_B
@pytest.mark.asyncio
async def test_execution_layer_refuses_a_cycle_outside_the_perimeter(db_session):
    """Even a cycle handed in directly must be refused, not silently skipped."""

    eq, _people = await _seed_two_runs(db_session)
    # The refusal rolls back, which expires ORM instances; read the id while it is loaded.
    eq_id = eq.id
    service = ClearingService(db_session)

    cycle = (await planned_cycles(db_session, _EQ))[0]  # 035 A2a: the planner, not the retired detectors

    with pytest.raises(GeoException) as refused:
        await service.execute_occurrence(
            _occurrence_of(cycle, eq_id), allowed_participant_pids={"a1", "a2", "a3"}
        )
    # 025 `T2508.1`: the perimeter refused it, not the occurrence's own descriptor check.
    assert not isinstance(refused.value, ClearingOccurrenceRefused), refused.value

    after = await _debt_amounts(db_session, eq_id)
    assert after == [Decimal("100")] * 3, (
        f"the refused execution still changed the debts: {after}"
    )


# REMOVED 2026-10-09 (035 A2b): `test_the_sql_producer_itself_is_scoped` pinned the perimeter predicate of
# `find_triangles_sql` directly, because `find_cycles` swallowed a broken SQL producer and fell through to the DFS.
# The query and the fall-through are removed. The perimeter of the one remaining producer - a foreign scope sees
# nothing, the owning scope sees its cycle, a scope holding two of three vertices sees nothing - is
# `test_detection_layer_does_not_return_a_foreign_cycle` above.


# Found by external review of this batch: two ways the perimeter was still bypassable.


@MODE_B
@pytest.mark.asyncio
async def test_a_committed_replay_is_not_returned_to_a_foreign_scope(db_session):
    """The replay shortcut returned before the guard, handing over a stranger's amount.

    `_execute_clearing_with_amount` answers an already-committed execution from the recorded
    transaction and returns that amount straight away - ahead of the locked re-read and
    therefore ahead of the perimeter check.  A scoped caller replaying a cycle of another
    run's debts would be told the foreign clearing succeeded, as its own result.
    """

    eq, _people = await _seed_two_runs(db_session)
    eq_id = eq.id
    service = ClearingService(db_session)
    cycle = (await planned_cycles(db_session, _EQ))[0]  # 035 A2a: the planner, not the retired detectors

    # Clear it legitimately first, so a committed CLEARING transaction exists for this cycle.
    cleared = await service.execute_occurrence(
        _occurrence_of(cycle, eq_id), allowed_participant_pids={"b1", "b2", "b3"}
    )
    assert cleared == Decimal("100"), cleared

    # Now a foreign run replays the same occurrence.
    with pytest.raises(GeoException) as refused:
        await service.execute_occurrence(
            _occurrence_of(cycle, eq_id), allowed_participant_pids={"a1", "a2", "a3"}
        )
    assert not isinstance(refused.value, ClearingOccurrenceRefused), refused.value


@pytest.mark.asyncio
async def test_an_unavailable_perimeter_is_not_reported_as_an_empty_result(
    client, db_session, run_a_only, monkeypatch
):
    """A perimeter that cannot be measured must not read as "nothing to clear".

    `_run_scoped_pids_or_none` is fail-closed: it returns an empty set both when the run is
    genuinely empty and when the snapshot cannot be built at all.  Passing that straight into
    detection makes the route answer 200 with zero cycles - a failed authorisation
    measurement dressed as a completed, empty clearing.
    """

    import app.api.v1.simulator as simulator_module

    await _seed_two_runs(db_session)

    async def _no_snapshot(**_kwargs):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", _no_snapshot)

    resp = await client.post(
        "/api/v1/simulator/runs/run-a/actions/clearing-real",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json={"equivalent": _EQ, "client_action_id": "rt_010_4_unavailable"},
    )

    assert resp.status_code != 200, (
        "the run perimeter could not be established, but the route reported a completed "
        f"clearing: {resp.status_code} {resp.text}"
    )
    assert resp.json()["code"] == "RUN_PERIMETER_UNAVAILABLE", resp.text


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "trustline-create",
            {"from_pid": "b1", "to_pid": "b2", "equivalent": _EQ, "limit": "10"},
        ),
        (
            "trustline-update",
            {"from_pid": "b1", "to_pid": "b2", "equivalent": _EQ, "new_limit": "5"},
        ),
        (
            "trustline-close",
            {"from_pid": "b1", "to_pid": "b2", "equivalent": _EQ},
        ),
        (
            "payment-real",
            {"from_pid": "b1", "to_pid": "b2", "equivalent": _EQ, "amount": "1"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_no_mutating_route_reports_an_unmeasurable_perimeter_as_a_fact(
    client, db_session, run_a_only, monkeypatch, path, payload
):
    """An unmeasurable perimeter must not be answered as a statement about the run.

    `_run_scoped_pids_or_none` is fail-closed and returns an empty set both when the run is
    empty and when the snapshot cannot be built.  For authorisation that is right.  For an
    ANSWER it is not: with an empty set every participant resolves to
    `404 PARTICIPANT_NOT_FOUND`, so the caller is told a participant that exists, in a run
    that contains them, is not there.  That is the same class the clearing route was fixed
    for - a failed measurement of authority dressed as a finding about the data.

    All four mutating routes, because the first fix reached only one of them.
    """

    import app.api.v1.simulator as simulator_module

    await _seed_two_runs(db_session)

    async def _no_snapshot(**_kwargs):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", _no_snapshot)

    resp = await client.post(
        f"/api/v1/simulator/runs/run-a/actions/{path}",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
        json=payload,
    )

    assert resp.json().get("code") == "RUN_PERIMETER_UNAVAILABLE", (
        f"{path} answered {resp.status_code} {resp.text} for a perimeter it could not "
        "measure, instead of saying so"
    )
