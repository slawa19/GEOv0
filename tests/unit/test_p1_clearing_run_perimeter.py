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
