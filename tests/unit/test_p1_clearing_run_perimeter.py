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
from app.core.clearing.service import ClearingService
from app.utils.exceptions import GeoException
from app.core.simulator.models import RunRecord
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine

_EQ = "RPX"


@pytest.fixture
def run_a_only(monkeypatch):
    """Register run A, whose scenario contains a1/a2/a3 and nobody else."""

    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
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


async def _debt_amounts(db_session, eq_id) -> list[Decimal]:
    rows = (
        await db_session.execute(select(Debt.amount).where(Debt.equivalent_id == eq_id))
    ).scalars().all()
    return sorted(Decimal(str(a)) for a in rows)


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
        json={"equivalent": _EQ, "max_depth": 6, "client_action_id": "rt_010_4"},
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

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
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
        json={"equivalent": _EQ, "max_depth": 6, "client_action_id": "rt_010_4_positive"},
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
    eq, _people = await _seed_two_runs(db_session)
    service = ClearingService(db_session)

    unscoped = await service.find_cycles(_EQ, max_depth=6)
    assert len(unscoped) == 1, (
        "the stand must contain exactly one detectable cycle, otherwise this test is not "
        f"measuring the perimeter: {unscoped}"
    )

    scoped = await service.find_cycles(
        _EQ, max_depth=6, allowed_participant_pids={"a1", "a2", "a3"}
    )
    assert scoped == [], f"detection returned another run's cycle: {scoped}"


@pytest.mark.asyncio
async def test_detection_treats_an_empty_perimeter_as_nobody(db_session):
    """`_run_scoped_pids_or_none` returns an empty set when the perimeter cannot be built.

    Reading that as "no restriction" would be a literal return of F-009-1.
    """

    await _seed_two_runs(db_session)
    service = ClearingService(db_session)

    assert await service.find_cycles(_EQ, max_depth=6, allowed_participant_pids=set()) == []


@pytest.mark.asyncio
async def test_execution_layer_refuses_a_cycle_outside_the_perimeter(db_session):
    """Even a cycle handed in directly must be refused, not silently skipped."""

    eq, _people = await _seed_two_runs(db_session)
    # The refusal rolls back, which expires ORM instances; read the id while it is loaded.
    eq_id = eq.id
    service = ClearingService(db_session)

    cycle = (await service.find_cycles(_EQ, max_depth=6))[0]

    with pytest.raises(GeoException):
        await service.execute_clearing_with_amount(
            cycle, allowed_participant_pids={"a1", "a2", "a3"}
        )

    after = await _debt_amounts(db_session, eq_id)
    assert after == [Decimal("100")] * 3, (
        f"the refused execution still changed the debts: {after}"
    )


@pytest.mark.asyncio
async def test_the_sql_producer_itself_is_scoped(db_session):
    """Pin the SQL predicate directly, not through `find_cycles`.

    `find_cycles` wraps the whole SQL block in a broad `except Exception` and falls through
    to the DFS producer (`app/core/clearing/service.py:924`).  Since the DFS load is narrowed
    too, a scoped-SQL that is simply BROKEN still yields a correct empty result - silently,
    and by loading every debt of the equivalent instead of a handful.  So the route-level
    and `find_cycles`-level tests cannot tell a working predicate from a broken one, and
    this case exists to.
    """

    eq, _people = await _seed_two_runs(db_session)
    service = ClearingService(db_session)

    # The raw producer emits one row per starting vertex; `find_cycles` dedupes them later
    # (`_deduplicate_cycles`).  Three rotations of the same triangle is the correct shape here.
    unscoped = await service.find_triangles_sql(eq.id)
    assert len(unscoped) == 3, f"the stand must have one triangle, three rotations: {unscoped}"

    foreign_scope = set(
        (
            await db_session.execute(
                select(Participant.id).where(Participant.pid.in_(["a1", "a2", "a3"]))
            )
        ).scalars().all()
    )
    assert len(foreign_scope) == 3

    scoped = await service.find_triangles_sql(
        eq.id, allowed_participant_ids=foreign_scope
    )
    assert scoped == [], f"the SQL producer returned another run's cycle: {scoped}"

    own_scope = set(
        (
            await db_session.execute(
                select(Participant.id).where(Participant.pid.in_(["b1", "b2", "b3"]))
            )
        ).scalars().all()
    )
    assert len(await service.find_triangles_sql(eq.id, allowed_participant_ids=own_scope)) == 3, (
        "the predicate must admit the owning run, not reject everything"
    )
