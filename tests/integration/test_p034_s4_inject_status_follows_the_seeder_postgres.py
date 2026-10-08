"""034 S4 (F-034-14, the status part): one scenario status means one participant status on both entry paths.

A scenario names a participant's status in two places: in `participants` (read by the seeder when the run starts) and
in an `add_participant` inject effect (read by the inject executor mid-run). The scenario schema lets `frozen` through
in the first and ANY string in the second (`fixtures/simulator/scenario.schema.json`, `participant.status` and
`injectEffectAddParticipant.participant.status`). The seeder turns `frozen` into `suspended` and `banned` into
`deleted`; the inject executor turned both into `active`, so a participant the scenario declares frozen entered the
network able to pay and be paid, and was counted as active by the tick's metrics.

What is asserted is the stored `Participant.status` after each path, and the status the run's in-memory scenario
carries for the injected participant (the tick's `active_participants` metric reads it). The expected value is the
seeder's own answer for the same status, not a literal - the two paths are compared, not one against a table.

Not asserted: which statuses the schema should allow (owner: programme 036), and that a suspended participant's
edges stay out of the planner's adjacency (neither path filters them; not a divergence).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner


async def _stored_status(db, pid: str) -> str | None:
    return await db.scalar(select(Participant.status).where(Participant.pid == pid)
                           .execution_options(populate_existing=True))


async def _seeded_status(db, status: str | None) -> str | None:
    """The status the SEEDER stores for a new participant whose scenario entry carries `status`."""
    n = uuid.uuid4().hex[:8].upper()
    pid = f"S4_SEED_{n}"
    entry = {"id": pid, "type": "person"}
    if status is not None:
        entry["status"] = status
    await RealScenarioSeeder().seed_scenario_into_db(
        session=db, scenario={"equivalents": [f"S4S{n[:6]}"], "participants": [entry], "trustlines": []})
    await db.commit()
    return await _stored_status(db, pid)


async def _injected_status(db, status: str | None) -> tuple[str | None, str | None]:
    """(stored status, status in the run's scenario) of a participant added by an `add_participant` inject."""
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"S4I{n[:6]}", precision=2, is_active=True)
    db.add(eq)
    await db.commit()
    pid = f"S4_INJ_{n}"
    participant = {"id": pid, "type": "person"}
    if status is not None:
        participant["status"] = status
    scenario = {"participants": [], "trustlines": [],
                "events": [{"type": "inject", "time": 0, "effects": [{"op": "add_participant",
                                                                      "participant": participant}]}]}
    runner, _artifacts = _make_runner(inject_enabled=True)
    await runner._apply_due_scenario_events(db, run_id="r-034-s4", scenario=scenario,
                                            run=_make_run(participants=[], equivalents=[eq.code]))
    in_scenario = [p.get("status") for p in scenario["participants"] if p.get("id") == pid]
    assert len(in_scenario) == 1, f"the injected participant is not in the run's scenario exactly once: {scenario}"
    return await _stored_status(db, pid), in_scenario[0]


@pytest.mark.parametrize("status", [None, "active", "suspended", "left", "deleted", "no-such-status"])
@pytest.mark.asyncio
async def test_control_statuses_both_paths_already_agree_on(db_session, status) -> None:
    seeded = await _seeded_status(db_session, status)
    stored, in_scenario = await _injected_status(db_session, status)
    assert seeded is not None and stored is not None, (seeded, stored)  # both paths really created their participant
    assert (stored, in_scenario) == (seeded, seeded), (
        f"scenario status {status!r}: the seeder stores {seeded!r}, the inject stores {stored!r} "
        f"and leaves {in_scenario!r} in the run's scenario")


@pytest.mark.asyncio
async def test_control_the_statuses_are_really_told_apart(db_session) -> None:
    """Anti-vacuum for the comparison: the two paths do not answer one constant for every status."""
    assert await _seeded_status(db_session, "suspended") == "suspended"
    assert await _seeded_status(db_session, "frozen") == "suspended"
    assert await _seeded_status(db_session, "banned") == "deleted"
    assert await _seeded_status(db_session, None) == "active"
    assert await _injected_status(db_session, "suspended") == ("suspended", "suspended")
    assert await _injected_status(db_session, "left") == ("left", "left")


@pytest.mark.parametrize("status", ["frozen", "banned", " Frozen "])
@pytest.mark.asyncio
async def test_an_injected_participant_gets_the_status_the_seeder_gives_the_same_scenario_status(
    db_session, status
) -> None:
    seeded = await _seeded_status(db_session, status)
    assert seeded in {"suspended", "deleted"}, seeded  # the seeder does not let this status in as active
    stored, in_scenario = await _injected_status(db_session, status)
    assert stored == seeded, (
        f"scenario status {status!r}: the seeder stores {seeded!r}, the inject stores {stored!r} - a participant "
        "the scenario declares frozen/banned entered the network active")
    assert in_scenario == seeded, (
        f"scenario status {status!r}: the run's scenario carries {in_scenario!r} for the injected participant, "
        f"the database {stored!r}, the seeder {seeded!r}")
