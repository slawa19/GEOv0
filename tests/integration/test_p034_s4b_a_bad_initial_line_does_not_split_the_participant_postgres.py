"""034 S4b: a bad initial line of `add_participant` does not leave the participant half-added.

Found by the section-15 review of S4 (static trace). The scenario schema lets any string through as an initial line's
`limit` (`fixtures/simulator/scenario.schema.json`, `initialTrustline.limit`), `NaN` included. `Decimal("NaN") <= 0`
raises, and it was raised AFTER the participant row was inserted `active` and after the run's scenario entry was
prepared with the declared status: the effect's general handler counted the effect as skipped, the status was never
set, and the event still committed. The database then said `active`, the run's scenario `suspended`, and a good line
written before the bad one stayed.

Every other bad initial line - unparsable, unstorable, refused by the trust-line service - is skipped and the effect
goes on. A non-finite limit is now one of them, under the storability rule's own name (`MONEY_FINITENESS`).

Asserted: the stored `Participant.status` equals the status the run's scenario carries; the good line is there, the
bad one is not; the note counts the skipped line by its reason. Entry: `runner._apply_due_scenario_events`, as S4.
Not asserted: any other exception after the insert (none is known to have an input).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.integration.test_p030_s3b_simulator_through_the_services_postgres import _world
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner


async def _add(db, *, status: str, limits: list[str]) -> dict:
    """Inject one `add_participant` with an initial line per limit (sponsors A, B in turn); what came of it."""
    eq, p = await _world(db)
    eq_id, eq_code = eq.id, eq.code
    pid = f"S4B_NEW_{uuid.uuid4().hex[:8].upper()}"
    sponsors = [p["A"].pid, p["B"].pid]
    effect = {"op": "add_participant", "participant": {"id": pid, "type": "person", "status": status},
              "initial_trustlines": [{"sponsor": sponsors[i % 2], "equivalent": eq_code, "limit": limit}
                                     for i, limit in enumerate(limits)]}
    scenario = {"participants": [{"id": x.pid} for x in p.values()], "trustlines": [],
                "events": [{"type": "inject", "time": 0, "effects": [effect]}]}
    runner, artifacts = _make_runner(inject_enabled=True)
    run = _make_run(participants=[(x.id, x.pid) for x in p.values()], equivalents=[eq_code])
    await runner._apply_due_scenario_events(db, run_id="r-034-s4b", scenario=scenario, run=run)
    await db.rollback()  # read what is COMMITTED, not what the session still holds
    stored = await db.scalar(select(Participant.status).where(Participant.pid == pid)
                             .execution_options(populate_existing=True))
    in_scenario = [x.get("status") for x in scenario["participants"] if x.get("id") == pid]
    lines = await db.scalar(select(func.count()).select_from(TrustLine).where(TrustLine.equivalent_id == eq_id))
    note = [a for a in artifacts.payloads if a.get("type") == "note"][-1]["scenario"]
    return {"stored": stored, "in_scenario": in_scenario, "lines": lines, "stats": note["stats"],
            "fired": set(run._real_fired_scenario_event_indexes)}


@pytest.mark.parametrize("status,expected", [("frozen", "suspended"), ("suspended", "suspended")])
@pytest.mark.asyncio
async def test_control_good_initial_lines_leave_one_status_everywhere(db_session, status, expected) -> None:
    got = await _add(db_session, status=status, limits=["10", "20"])
    assert (got["stored"], got["in_scenario"], got["lines"]) == (expected, [expected], 2), got


@pytest.mark.asyncio
async def test_control_an_unparsable_initial_line_is_skipped_and_the_status_is_set(db_session) -> None:
    """The neighbouring bad line, handled before S4b: the effect goes on past it."""
    got = await _add(db_session, status="frozen", limits=["10", "not-a-number"])
    assert (got["stored"], got["in_scenario"], got["lines"]) == ("suspended", ["suspended"], 1), got


@pytest.mark.parametrize("status,expected", [("frozen", "suspended"), ("suspended", "suspended"), ("deleted", "deleted")])
@pytest.mark.parametrize("bad", ["NaN", "sNaN"])
@pytest.mark.asyncio
async def test_a_non_finite_initial_limit_does_not_split_the_participant(db_session, status, expected, bad) -> None:
    got = await _add(db_session, status=status, limits=["10", bad])
    assert got["fired"] == {0}, got  # control: the event went through and was committed, not left pending
    assert got["stored"] is None or got["stored"] == (got["in_scenario"] or [None])[0], (
        f"scenario status {status!r}, initial limits ['10', {bad!r}]: the database says {got['stored']!r}, the run's "
        f"scenario {got['in_scenario']} - the participant was committed half-added ({got['stats']})")
    assert (got["stored"], got["lines"]) == (expected, 1), (
        f"expected the participant {expected!r} with its one good line; got {got}")
    assert got["stats"]["skipped_reasons"] == {"MONEY_FINITENESS": 1}, got["stats"]
