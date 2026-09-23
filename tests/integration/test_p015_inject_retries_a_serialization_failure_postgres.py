"""Programme 015, phase B step 3: a REAL serialization failure restarts the inject's unit of work.

The default tier proves the retry with a synthetic `DBAPIError`. That proves the owner's branch, not
that PostgreSQL produces what the branch expects where the branch expects it. This stand produces a
real `40001`: the inject stages an update of a debt it has read, another connection updates and
commits the same debt before the inject commits, and PostgreSQL refuses the inject's write under
SERIALIZABLE ("could not serialize access due to concurrent update").

What must follow, and is asserted on the stored row:
* PostgreSQL raised the 40001 at the owner's EXPLICIT FLUSH of the staged writes - where the UPDATE
  is executed - and nowhere else, so the failure is real, not assumed. Asserting the place, not just
  the code, also guards the flush itself: without it the write runs inside `commit()`, the error is
  raised there, and a commit error is exactly what the owner must not confuse with a staging one;
* the whole unit of work ran again (staging twice), not just the commit;
* the stored amount is the CONCURRENT value plus the injected amount, exactly once. A retry that
  replayed the first attempt's staged amount would store 5.00 + 3.00 and silently discard the
  concurrent write; a double application would add 3.00 twice.

THE STAND is the reproducer's (`test_p015_inject_holds_the_owner_lock_postgres.py`): its own engine
with `isolation_level="SERIALIZABLE"` and a real pool of two connections - the shared test engine runs
READ COMMITTED, where this conflict does not exist at all, and `db_session` wraps every commit in a
savepoint. The concurrent writer takes the pool's second connection.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError

from app.db.models.debt import Debt
from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (  # noqa: F401
    _Artifacts,
    _cleanup,
    _observations,
    _run,
    _runner,
    _seed,
    _stored,
    observed_factory,
)

from tests.debt_setup import debt_fixture_setup


_EXISTING = Decimal("5.00")
_CONCURRENT = Decimal("7.12345678")
_INJECTED = Decimal("3.00")


@pytest.mark.asyncio
async def test_a_real_serialization_failure_restarts_the_whole_inject_unit_of_work(
    observed_factory,  # noqa: F811
) -> None:
    world = await _seed(observed_factory)
    eq = world.equivalents[0]
    try:
        async with observed_factory() as s:
            async with debt_fixture_setup(s, label="setup"):
                s.add(
                    Debt(
                        debtor_id=world.debtor.id,
                        creditor_id=world.creditor.id,
                        equivalent_id=eq.id,
                        amount=_EXISTING,
                    )
                )
            await s.commit()
        _observations.clear()

        c, d = world.creditor.pid, world.debtor.pid
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
                        {
                            "op": "inject_debt",
                            "from": c,
                            "to": d,
                            "equivalent": eq.code,
                            "amount": str(_INJECTED),
                        }
                    ],
                }
            ],
        }
        run = _run(world, "p015-real-40001")
        artifacts = _Artifacts()
        runner = _runner(run, scenario, artifacts)

        real_stage = runner._inject_executor.stage_inject_event
        stage_calls = 0

        async def _stage_then_a_concurrent_writer_commits(session, **kwargs):
            nonlocal stage_calls
            stage_calls += 1
            staged = await real_stage(session, **kwargs)  # has read the debt at 5.00
            if stage_calls == 1:
                # THE COMPETITOR WRITES WITH THE JOURNAL STOOD DOWN, and both halves of that are
                # deliberate. Its statement is a Core `update(Debt)`, which the journal's write
                # guard refuses (`C2`) - and it has to STAY a Core statement, because the
                # observations counted at the end of this test are ORM debt flushes: routing the
                # competitor through the ORM would add a third and the assertion "both debt flushes
                # ran under the owner lock" would be counting a writer that is not the inject. What
                # this helper stands for is "somebody else committed the row", and that is what it
                # still does. Per engine, re-armed immediately.
                from app.core.ledger import journal

                journal.uninstall_write_guard(observed_factory.kw.get("bind"))
                try:
                    async with observed_factory() as other:
                        await other.execute(
                            update(Debt)
                            .where(
                                Debt.debtor_id == world.debtor.id,
                                Debt.creditor_id == world.creditor.id,
                                Debt.equivalent_id == eq.id,
                            )
                            .values(amount=_CONCURRENT)
                        )
                        await other.commit()
                finally:
                    journal.install_write_guard(observed_factory.kw.get("bind"))
            return staged

        runner._inject_executor.stage_inject_event = _stage_then_a_concurrent_writer_commits

        failures: list[tuple[str, str | None]] = []

        def _observed(where: str, real):
            async def _call(*args, **kwargs):
                try:
                    return await real(*args, **kwargs)
                except DBAPIError as exc:
                    orig = getattr(exc, "orig", None)
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

        assert failures == [("flush", "40001")], (
            f"non-vacuity: the stand must produce exactly one real serialization failure, at the "
            f"owner's explicit flush of the staged writes; observed {failures}"
        )
        assert stage_calls == 2, f"the unit of work must be staged again, staged {stage_calls}x"
        stored = await _stored(observed_factory, world)
        assert stored == {eq.id: _CONCURRENT + _INJECTED}, (
            f"expected the concurrent {_CONCURRENT} plus the injected {_INJECTED} exactly once, "
            f"stored {stored}"
        )
        assert run._real_fired_scenario_event_indexes == {0}
        notes = [
            p["scenario"]["description"] for p in artifacts.events if p.get("type") == "note"
        ]
        assert notes == ["inject applied"], notes

        # Both debt flushes (the refused one and the retried one) ran under the owner lock.
        debt_flushes = [o for o in _observations if o.equivalent_id == eq.id]
        assert len(debt_flushes) == 2, debt_flushes
        assert not [o for o in debt_flushes if o.error or not o.held], debt_flushes
    finally:
        await _cleanup(observed_factory, world)
