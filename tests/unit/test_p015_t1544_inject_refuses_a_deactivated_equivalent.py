"""T1544: the real-mode inject writer creates no line in an equivalent the operator has deactivated.

Protocol §11.5.1 blocks OPERATIONS in the equivalent. Since 030 S3b the inject has no money effect
(`inject_debt` is deleted); what it can still write into a stopped equivalent is a trust line
(`create_trustline`), and the stop check is the one inside the line creation itself
(`TrustLineService.execute_create` -> `MoneyBoundary.refuse_inactive_equivalents`), shared with every
other caller of that entrance.

THE REFUSAL IS A SKIP OF THAT EFFECT, NOT AN ERROR OF THE RUN - the rule the payments phase already applies
to a refused payment. The event is consumed with a visible note (`skipped_reasons == {equivalent_inactive: 1}`),
writes no line, no debt and no envelope, and is not retried: reactivating the equivalent later does not
re-apply it.

This is the plain refusal, on the tier database (PostgreSQL, mode A). The race binding (`FOR SHARE` against a
deactivating PATCH) is held in `test_p015_t1544_operator_stop_races_postgres.py`; the tick lifecycle in
`test_p015_t1544_operator_stop_through_the_tick_sqlite.py` (a mode-B PostgreSQL clone since 017 stage 3,
whatever its file name says); the same skip for a held equivalent, and the seeder's lines, in
`tests/integration/test_p030_s3b_simulator_through_the_services_postgres.py`.

What this module no longer asserts (the old `inject_debt` form): that no `INSERT INTO debt_operations` is sent
BEFORE the refusal. There is no inject envelope any more, so that ORDER (owner lock -> `FOR SHARE` -> envelope)
does not exist; the statement recorder instead shows that no line and no envelope is SENT for the refused
event, with a control that the recorder does see both for an applied one.
"""

from __future__ import annotations

import copy
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event, select, update

from app.core.money_boundary import MoneyBoundary
from app.core.simulator.real_scenario_seeder import simulated_public_key
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner


async def _seed(db_session):
    """An active equivalent and three simulator participants, no line anywhere."""
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"T44{n}"[:16], precision=2, is_active=True)
    people = [
        Participant(
            pid=f"T44_{role}_{n}", display_name=role, public_key=simulated_public_key(f"T44_{role}_{n}"),
            type="person", status="active",
        )
        for role in ("C", "D", "E")
    ]
    db_session.add_all([eq, *people])
    await db_session.flush()
    return (eq, *people)


def _scenario(eq, creditor, debtor) -> dict:
    return {
        "equivalents": [eq.code],
        "participants": [{"id": creditor.pid}, {"id": debtor.pid}],
        "trustlines": [],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {"op": "create_trustline", "from": creditor.pid, "to": debtor.pid,
                     "equivalent": eq.code, "limit": "100.00"}
                ],
            }
        ],
    }


async def _lines(db_session, equivalent_id) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Plain id columns: the owner rolls the session back and expires every instance."""
    rows = (
        await db_session.execute(
            select(TrustLine.from_participant_id, TrustLine.to_participant_id, TrustLine.limit).where(
                TrustLine.equivalent_id == equivalent_id
            )
        )
    ).all()
    assert all(Decimal(str(limit)) == Decimal("100.00") for _f, _t, limit in rows), rows
    return {(f, t) for f, t, _limit in rows}


class _StatementRecorder:
    """Every SQL statement sent on the engine, as sent - what was actually written, not what remained.

    The refused attempt is rolled back, so the tables show nothing either way; only the statements sent
    can tell "never wrote" from "wrote and rolled back".
    """

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.statements.append(" ".join(str(statement).split()).upper())

    def sent(self, prefix: str) -> list[str]:
        return [s for s in self.statements if s.startswith(prefix)]


async def _apply_recorded(db_session, runner, *, run, scenario) -> _StatementRecorder:
    # On the ENGINE, not on one Connection wrapper: the owner ends and restarts its transaction while
    # it works, and a listener on a single wrapper misses the statements sent after that (measured).
    sync_engine = (await db_session.connection()).sync_connection.engine
    recorder = _StatementRecorder()
    event.listen(sync_engine, "before_cursor_execute", recorder)
    try:
        await runner._apply_due_scenario_events(
            db_session, run_id="r-t1544-inject", run=run, scenario=scenario
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", recorder)
    return recorder


def _inject_notes(artifacts) -> list[dict]:
    return [
        p["scenario"] for p in artifacts.payloads
        if p.get("type") == "note" and (p.get("scenario") or {}).get("event_index") is not None
    ]


@pytest.mark.asyncio
async def test_an_inject_line_into_a_deactivated_equivalent_is_skipped_and_consumed_without_line_or_envelope(
    db_session,
) -> None:
    """RED before the stop check in the line creation: the line is created after the stop."""
    eq, creditor, debtor, third = await _seed(db_session)
    eq_id, creditor_id, debtor_id, third_id, third_pid = eq.id, creditor.id, debtor.id, third.id, third.pid
    run = _make_run(
        participants=[(creditor.id, creditor.pid), (debtor.id, debtor.pid), (third.id, third.pid)],
        equivalents=[str(eq.code)],
    )
    scenario = _scenario(eq, creditor, debtor)
    await db_session.execute(update(Equivalent).where(Equivalent.id == eq_id).values(is_active=False))
    await db_session.commit()

    runner, artifacts = _make_runner(inject_enabled=True)
    refused_attempt = await _apply_recorded(db_session, runner, run=run, scenario=scenario)

    # A skip of the effect, consumed and visible - not an exception for the tick to count.
    notes = _inject_notes(artifacts)
    assert len(notes) == 1 and notes[0]["event_index"] == 0, artifacts.payloads
    assert notes[0]["stats"]["applied"] == 0, notes
    assert notes[0]["stats"]["skipped_reasons"] == {MoneyBoundary.EQUIVALENT_INACTIVE_REASON: 1}, notes
    assert 0 in run._real_fired_scenario_event_indexes, "the refused event was left pending"
    assert await _lines(db_session, eq_id) == set()

    # Statements SENT. Premise first - the recorder saw the guard's own read, so an empty list is a
    # measurement and not a listener that heard nothing.
    assert refused_attempt.sent("SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE"), (
        "premise: the statement recorder did not see the guard's read; it recorded "
        f"{len(refused_attempt.statements)} statement(s): {refused_attempt.statements[:12]}"
    )
    assert refused_attempt.sent("INSERT INTO TRUST_LINES") == [], "a line INSERT was sent into a stopped equivalent"
    assert refused_attempt.sent("INSERT INTO DEBT_OPERATIONS") == [], "the inject opened an envelope"

    # Reactivated, the CONSUMED event must not come back. A second event, new to the scenario and naming
    # another debtor, does apply - which proves this call really ran, that the recorder does see a line
    # INSERT, and (by the absence of creditor -> debtor) that the refused effect was not re-applied.
    await db_session.execute(update(Equivalent).where(Equivalent.id == eq_id).values(is_active=True))
    await db_session.commit()
    second = copy.deepcopy(scenario["events"][0])
    second["effects"][0]["to"] = third_pid
    scenario["events"].append(second)
    applied_attempt = await _apply_recorded(db_session, runner, run=run, scenario=scenario)

    assert await _lines(db_session, eq_id) == {(creditor_id, third_id)}, (
        "expected only the NEW event's line; a creditor -> debtor line would mean the refused event was re-applied"
    )
    assert run._real_fired_scenario_event_indexes >= {0, 1}
    assert applied_attempt.sent("INSERT INTO TRUST_LINES"), (
        "control: the recorder did not see the line INSERT of an inject that was applied, so its "
        "silence above proves nothing"
    )
    assert applied_attempt.sent("INSERT INTO DEBT_OPERATIONS") == [], "an inject opened an envelope"
