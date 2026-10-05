"""030 S3b: the simulator writes only through the services, with their checks (F-030-6, F-030-19; owner В1).

F-030-6: the `inject_debt` effect is gone - the schema refuses it and the executor writes no debt and no `INJECT`
operation for it. F-030-19 (`T3000` item 3): the seeder's lines go through `TrustLineService.execute_create`, whose
shared entry refuses a suspended end, a stopped or held equivalent, a limit off the step and a self-line; a refused
line fails the whole seeding, and its owner's rollback leaves nothing of it. Mode A.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import func, insert, select

from app.core.simulator.real_scenario_seeder import (RealScenarioSeeder, ScenarioTrustLineRefused,
                                                     SimulatorPidTakenError, simulated_public_key)
from app.db.journal_tables import debt_operations
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.db.reconciliation_tables import debt_reconciliation_results
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "fixtures/simulator/scenario.schema.json").read_text("utf-8"))


async def _world(db, *, status="active", active=True, held=False, precision=2):
    n = uuid.uuid4().hex[:6].upper()
    eq = Equivalent(code=f"S3B{n}", precision=precision, is_active=active)
    p = {k: Participant(pid=f"S3B_{k}_{n}", display_name=k, public_key=simulated_public_key(f"S3B_{k}_{n}"),
                        type="person", status=status if k == "B" else "active") for k in "AB"}
    db.add_all([eq, *p.values()])
    await db.flush()
    if held:
        result_id, now = uuid.uuid4(), datetime.now(timezone.utc)
        await db.execute(insert(debt_reconciliation_results).values(
            id=result_id, equivalent_id=eq.id, status="FAILED", fingerprint=result_id.hex * 2, detail={},
            checked_at=now, last_checked_at=now, is_latest=True))
        eq.integrity_hold_result_id = result_id
    await db.commit()
    return eq, p


async def _inject(db, eq, p, effect) -> dict:
    runner, artifacts = _make_runner(inject_enabled=True)
    scenario = {"participants": [{"id": x.pid} for x in p.values()], "trustlines": [],
                "events": [{"type": "inject", "time": 0, "effects": [effect]}]}
    await runner._apply_due_scenario_events(db, run_id="r-030-s3b", scenario=scenario, run=_make_run(
        participants=[(x.id, x.pid) for x in p.values()], equivalents=[eq.code]))
    return [a for a in artifacts.payloads if a.get("type") == "note"][-1]["scenario"]


async def _lines(db, eq_id) -> int:
    return await db.scalar(select(func.count()).select_from(TrustLine).where(TrustLine.equivalent_id == eq_id))


def _scenario(eq, p, *lines) -> dict:
    return {"equivalents": [eq.code], "participants": [{"id": x.pid} for x in p.values()],
            "trustlines": [{"from": p[a].pid, "to": p[b].pid, "equivalent": eq.code, "limit": lim} for a, b, lim in lines]}


# ------------------------------------------------------------------ F-030-6


def test_the_schema_refuses_an_inject_debt_effect() -> None:
    event = {"type": "inject", "time": 0, "effects": [{"op": "inject_debt", "from": "A", "to": "B", "equivalent": "UAH",
                                                       "amount": "1"}]}
    control = {"type": "inject", "time": 0, "effects": [{"op": "freeze_participant", "participant_id": "A"}]}
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/timelineEvent"})
    assert list(validator.iter_errors(control)) == []
    assert list(validator.iter_errors(event)) != [], "an inject_debt effect passed the scenario schema"


@pytest.mark.asyncio
async def test_an_inject_debt_event_writes_no_debt_and_no_inject_operation(db_session) -> None:
    eq, p = await _world(db_session)
    db_session.add(TrustLine(from_participant_id=p["A"].id, to_participant_id=p["B"].id, equivalent_id=eq.id,
                             limit=Decimal("100"), status="active", policy={}))
    await db_session.commit()
    injects = select(func.count()).select_from(debt_operations).where(debt_operations.c.kind == "INJECT")
    before = await db_session.scalar(injects)
    await _inject(db_session, eq, p, {"op": "inject_debt", "from": p["A"].pid, "to": p["B"].pid,
                                      "equivalent": eq.code, "amount": "5"})
    debts = (await db_session.execute(select(Debt.amount).where(Debt.equivalent_id == eq.id))).scalars().all()
    assert (debts, await db_session.scalar(injects)) == ([], before), "inject_debt still writes a debt"


# ------------------------------------------------------------------ F-030-19: the inject's lines


@pytest.mark.parametrize("world,reason", [({"held": True}, "equivalent_integrity_hold"),
                                          ({"active": False}, "equivalent_inactive")])
@pytest.mark.asyncio
async def test_an_inject_line_in_a_stopped_or_held_equivalent_is_skipped(db_session, world, reason) -> None:
    eq, p = await _world(db_session, **world)
    note = await _inject(db_session, eq, p, {"op": "create_trustline", "from": p["A"].pid, "to": p["B"].pid,
                                             "equivalent": eq.code, "limit": "10"})
    assert await _lines(db_session, eq.id) == 0, note
    assert note["stats"]["skipped_reasons"] == {reason: 1}, note


# ------------------------------------------------------------------ F-030-19: the seeder's lines


@pytest.mark.parametrize("world,lines,reason", [
    ({"status": "suspended"}, [("A", "B", "10")], "participant_suspended"),
    ({"held": True}, [("A", "B", "10")], "equivalent_integrity_hold"),
    ({"active": False}, [("A", "B", "10")], "equivalent_inactive"),
    ({}, [("A", "B", "10"), ("A", "A", "10")], None),  # a self-line, after a good line: the whole seed goes
    ({}, [("A", "B", "10"), ("B", "A", "1.005")], "amount_precision_exceeded"),  # control: refused before S3b too
])
@pytest.mark.asyncio
async def test_a_refused_seed_line_fails_the_seeding_and_its_rollback_leaves_nothing(
    db_session, world, lines, reason
) -> None:
    eq, p = await _world(db_session, **world)
    scenario, eq_id = _scenario(eq, p, *lines), eq.id
    ghost = f"S3B_NEW_{uuid.uuid4().hex[:6]}"
    scenario["participants"].append({"id": ghost})
    with pytest.raises(ScenarioTrustLineRefused) as refused:
        await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=scenario)
    assert reason is None or refused.value.details["reason"] == reason, refused.value.details
    await db_session.rollback()  # what both owners of the seeding transaction do
    assert await _lines(db_session, eq_id) == 0
    assert await db_session.scalar(select(func.count()).select_from(Participant).where(Participant.pid == ghost)) == 0


@pytest.mark.asyncio
async def test_control_a_foreign_pid_is_refused_and_a_clean_seed_lands(db_session) -> None:
    eq, p = await _world(db_session)
    p["B"].public_key = "a-real-key"
    await db_session.commit()
    with pytest.raises(SimulatorPidTakenError):
        await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=_scenario(eq, p, ("A", "B", "1")))
    await db_session.rollback()
    eq, p = await _world(db_session)
    await RealScenarioSeeder().seed_scenario_into_db(session=db_session, scenario=_scenario(eq, p, ("A", "B", "10")))
    assert await _lines(db_session, eq.id) == 1
