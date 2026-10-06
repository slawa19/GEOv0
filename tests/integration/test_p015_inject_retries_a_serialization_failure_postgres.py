"""Programme 015, phase B step 3: a REAL transient failure restarts the inject's unit of work.

030 S3b: the carrier is a `create_trustline` effect (the `inject_debt` effect is deleted). The property is
unchanged: the owner of the event's transaction restarts the WHOLE unit of work on a transient database error.

The default tier proves the retry with a synthetic `DBAPIError` (`tests/unit/test_p015_inject_transaction_ownership.py`).
That proves the owner's branch, not that PostgreSQL produces what the branch expects where the branch expects it.
This stand produces a real `40P01`: another connection holds an UNCOMMITTED line of the very pair the event creates
and waits for the participant rows the event holds `FOR SHARE`; then the event's INSERT waits on the other
transaction's unique-index entry and closes the cycle. Since 031 `T3102` that order is confirmed before the INSERT
(`queue_behind_the_victim`), so the inject's own deadlock check finds the cycle and PostgreSQL aborts the inject at
the flush that sends the line (`TrustLineWriteBatch.finish`, inside `stage_inject_event`) - asserted from the DETAIL.
(027 stage 2: a real DEADLOCK stands in for the SSI `40001` of the earlier stand; every inject event holds its
participant rows `FOR SHARE`, so the cycle exists on any run of it.)

What must follow, and is asserted on the stored rows:
* PostgreSQL raised the `40P01` at the owner's flush of the staged line and nowhere else, so the failure is real,
  not assumed;
* the whole unit of work ran again (staging twice), not just the commit;
* the line lands EXACTLY ONCE with the event's own limit - not the competitor's, which was rolled back - and has
  exactly one audit row: a retry that replayed the first attempt's staged writes, or applied twice, would leave two.

THE STAND is the reproducer's (`test_p015_inject_holds_the_owner_lock_postgres.py`): its own engine at the
application's isolation level and a real pool of two connections - `db_session` wraps every commit in a savepoint.
The competitor takes the pool's second connection.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import DBAPIError

from app.core.trustlines.service import TrustLineWriteBatch
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (  # noqa: F401
    _Artifacts,
    _run,
    _runner,
    _seed,
    observed_factory,
)
from tests.p019_support import QueuedCompetitor, assert_victim_of, queue_behind_the_victim
from tests.p021_support import trust_line_audit_rows

_EVENT_LIMIT = Decimal("50.00")
_COMPETITOR_LIMIT = Decimal("1.00")


@pytest.mark.asyncio
async def test_a_real_deadlock_restarts_the_whole_inject_unit_of_work(
    observed_factory,  # noqa: F811
    monkeypatch,
) -> None:
    world = await _seed(observed_factory)
    eq = world.equivalents[0]
    c, d = world.creditor.pid, world.debtor.pid
    # The seed has the line creditor -> debtor in both equivalents; the event creates the opposite one.
    line = (TrustLine.from_participant_id == world.debtor.id, TrustLine.to_participant_id == world.creditor.id,
            TrustLine.equivalent_id == eq.id)
    scenario = {
        "equivalents": [e.code for e in world.equivalents],
        "participants": [{"id": c}, {"id": d}],
        "trustlines": [
            {"from": c, "to": d, "equivalent": eq.code, "limit": "100.00", "status": "active"}
        ],
        "behaviorProfiles": [],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {"op": "create_trustline", "from": d, "to": c, "equivalent": eq.code,
                     "limit": str(_EVENT_LIMIT)}
                ],
            }
        ],
    }
    run = _run(world, "p015-real-40P01")
    artifacts = _Artifacts()
    runner = _runner(run, scenario, artifacts)

    real_stage = runner._inject_executor.stage_inject_event
    stage_calls = 0

    async def _count_stages(session, **kwargs):
        nonlocal stage_calls
        stage_calls += 1
        return await real_stage(session, **kwargs)

    runner._inject_executor.stage_inject_event = _count_stages

    competitors: list[asyncio.Task] = []
    real_finish = TrustLineWriteBatch.finish
    finish_calls = 0

    queued: list[QueuedCompetitor] = []

    async def _competitor(other, waiting: QueuedCompetitor) -> None:
        try:
            await waiting.waiting  # returns once the inject's failure has ended its attempt
            await other.rollback()  # the competitor never commits its line
        finally:
            await other.close()

    async def _finish_after_the_competitor_holds_its_line(batch):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:  # the first attempt only: the retry runs against a competitor that is gone
            # WHO IS THE VICTIM (031 `T3102`, review `T3096` finding 3): the competitor inserts the same pair
            # UNCOMMITTED and is CONFIRMED waiting on the participant rows the inject holds `FOR SHARE`; only then
            # does the inject's flush send its line, wait on the competitor's index entry and close the cycle - so
            # the inject's own deadlock check finds it (`queue_behind_the_victim`). Before, the competitor closed
            # the cycle and a 30 s `deadlock_timeout` on it only made the inject's win likely (measured 2 of 6
            # competitor victims without it).
            other = observed_factory()
            try:
                waiting = await queue_behind_the_victim(
                    batch.session, other,
                    hold=insert(TrustLine).values(from_participant_id=world.debtor.id, to_participant_id=world.creditor.id,
                                                  equivalent_id=eq.id, limit=_COMPETITOR_LIMIT, status="active"),
                    wait_on_victim=select(Participant.id).where(
                        Participant.id.in_([world.creditor.id, world.debtor.id])).with_for_update(),
                )
            except BaseException:
                await other.close()
                raise
            queued.append(waiting)
            competitors.append(asyncio.create_task(_competitor(other, waiting)))
        return await real_finish(batch)

    monkeypatch.setattr(TrustLineWriteBatch, "finish", _finish_after_the_competitor_holds_its_line)

    failures: list[tuple[str, str | None]] = []
    errors: list[BaseException] = []

    def _observed(where: str, real):
        async def _call(*args, **kwargs):
            try:
                return await real(*args, **kwargs)
            except DBAPIError as exc:
                orig = getattr(exc, "orig", None)
                errors.append(exc)
                failures.append(
                    (where, getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None))
                )
                raise

        return _call

    async with observed_factory() as session:
        session.flush = _observed("flush", session.flush)  # type: ignore[method-assign]
        session.commit = _observed("commit", session.commit)  # type: ignore[method-assign]

        await runner._apply_due_scenario_events(
            session, run_id=run.run_id, run=run, scenario=scenario
        )
        assert not session.in_transaction()

    await asyncio.gather(*competitors)
    assert failures == [("flush", "40P01")], (
        f"non-vacuity: the stand must produce exactly one real deadlock, at the flush that sends the "
        f"event's line; observed {failures}"
    )
    assert len(errors) == len(queued) == 1, (errors, queued)
    assert_victim_of(errors[0], queued[0])  # detected in the inject's backend, against the competitor
    assert stage_calls == 2, f"the unit of work must be staged again, staged {stage_calls}x"
    async with observed_factory() as s:
        lines = (await s.execute(select(TrustLine.limit, TrustLine.status).where(*line))).all()
        audit = await trust_line_audit_rows(s, equivalent_codes=[eq.code], operation_type="TRUST_LINE_CREATE")
    assert [(Decimal(str(limit)), status) for limit, status in lines] == [(_EVENT_LIMIT, "active")], (
        f"expected the event's line exactly once with its own limit {_EVENT_LIMIT}, stored {lines}"
    )
    assert len(audit) == 1, f"the retried event must leave one audit row, got {len(audit)}"
    assert run._real_fired_scenario_event_indexes == {0}
    notes = [
        p["scenario"]["description"] for p in artifacts.events if p.get("type") == "note"
    ]
    assert notes == ["inject applied"], notes
