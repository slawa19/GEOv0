"""T1544: the real-mode inject writer refuses money in an equivalent the operator has deactivated.

Protocol §11.5.1 blocks OPERATIONS in the equivalent, and `inject_debt` writes the shared `debts`
table in real mode. The guard is the same helper the payment commit uses
(`PaymentEngine.refuse_inactive_equivalents`). It sits in the inject owner
(`RealRunnerImpl._apply_due_scenario_events`) after the owner locks and BEFORE the operation envelope
is opened - owner lock -> `FOR SHARE` -> envelope -> debt write.

THE REFUSAL IS A REJECTION OF THAT INJECT, NOT AN ERROR OF THE RUN - the rule the payments phase
already applies to a refused payment. The event is consumed with a visible note, writes no debt and
no envelope, and is not retried: reactivating the equivalent later does not re-apply it.

On SQLite this is the plain refusal. The race binding (`FOR SHARE` against a deactivating PATCH)
belongs to PostgreSQL and is held in `test_p015_t1544_operator_stop_races_postgres.py`; the tick
lifecycle is held in `test_p015_t1544_operator_stop_through_the_tick_sqlite.py`.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from sqlalchemy import event, select, update

from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from tests.unit.test_p015_t1514_simulator_must_not_requantise_stored_money import (
    _scenario,
    _seed,
)
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner

_REFUSED_NOTE = "inject refused (equivalent inactive)"


async def _debt_amount(db_session, *, debtor_id, creditor_id, equivalent_id) -> Decimal:
    """A column read by plain ids: the owner rolls the session back and expires every instance."""
    amount = (
        await db_session.execute(
            select(Debt.amount).where(
                Debt.debtor_id == debtor_id,
                Debt.creditor_id == creditor_id,
                Debt.equivalent_id == equivalent_id,
            )
        )
    ).scalar_one()
    return Decimal(str(amount))


class _StatementRecorder:
    """Every SQL statement sent on the engine, as sent - to see ORDER, not just outcome.

    The refused attempt is rolled back, so a check placed after the envelope would leave no row behind
    either; only the statements actually sent can tell "refused before the envelope" from "refused
    after it".
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


@pytest.mark.asyncio
async def test_an_inject_into_a_deactivated_equivalent_is_consumed_without_debt_or_envelope(
    db_session,
) -> None:
    """RED before the guard: the injected 1.00 was added to the stored debt after the stop."""
    eq, creditor, debtor = await _seed(
        db_session, existing_amount=Decimal("5.00000000"), limit=Decimal("100.00")
    )
    ids = {"debtor_id": debtor.id, "creditor_id": creditor.id, "equivalent_id": eq.id}
    run = _make_run(
        participants=[(creditor.id, creditor.pid), (debtor.id, debtor.pid)],
        equivalents=[str(eq.code)],
    )
    scenario = _scenario(eq, creditor, debtor, inject_amount="1.00", limit="100.00")
    await db_session.execute(
        update(Equivalent).where(Equivalent.id == ids["equivalent_id"]).values(is_active=False)
    )
    await db_session.commit()

    runner, artifacts = _make_runner(inject_enabled=True)
    refused_attempt = await _apply_recorded(db_session, runner, run=run, scenario=scenario)

    # A rejection of the inject, consumed and visible - not an exception for the tick to count.
    notes = [
        p for p in artifacts.payloads
        if p.get("type") == "note" and (p.get("scenario") or {}).get("description") == _REFUSED_NOTE
    ]
    assert len(notes) == 1 and notes[0]["scenario"]["event_index"] == 0, artifacts.payloads
    assert 0 in run._real_fired_scenario_event_indexes, "the refused event was left pending"
    assert await _debt_amount(db_session, **ids) == Decimal("5.00000000")

    # ORDER: the refusal came before the envelope. Premise first - the recorder saw the guard's own
    # read, so an empty envelope list is a measurement and not a listener that heard nothing.
    assert refused_attempt.sent("SELECT EQUIVALENTS.CODE, EQUIVALENTS.IS_ACTIVE"), (
        "premise: the statement recorder did not see the guard's read; it recorded "
        f"{len(refused_attempt.statements)} statement(s): {refused_attempt.statements[:12]}"
    )
    assert refused_attempt.sent("INSERT INTO DEBT_OPERATIONS") == [], (
        "the inject opened its operation envelope before refusing the stop: the order must be owner "
        "lock -> FOR SHARE -> envelope -> debt write"
    )

    # Reactivated, the CONSUMED event must not come back. A second event, new to the scenario, does
    # apply - which proves this call really ran, and that the recorder does see an envelope.
    await db_session.execute(
        update(Equivalent).where(Equivalent.id == ids["equivalent_id"]).values(is_active=True)
    )
    await db_session.commit()
    second = copy.deepcopy(scenario["events"][0])
    second["effects"][0]["amount"] = "2.00"
    scenario["events"].append(second)
    applied_attempt = await _apply_recorded(db_session, runner, run=run, scenario=scenario)

    assert await _debt_amount(db_session, **ids) == Decimal("7.00000000"), (
        "expected 5.00 plus only the NEW event's 2.00; 8.00 would mean the refused event was re-applied"
    )
    assert run._real_fired_scenario_event_indexes >= {0, 1}
    assert applied_attempt.sent("INSERT INTO DEBT_OPERATIONS"), (
        "control: the recorder did not see the envelope of an inject that was applied, so its "
        "silence above proves nothing"
    )
