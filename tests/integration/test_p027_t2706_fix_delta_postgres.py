"""027 `T2706`: reproducers of the §15 review of `0cc44e3f..a2e309e0` (P1, P2 growth, P2 bounded waits)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update

from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.money_boundary import MoneyBoundary
from app.core.simulator import trust_drift_engine
from app.core.trustlines.service import TrustLineService
from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCloseRequest, TrustLineCreateRequest, TrustLineUpdateRequest
from app.utils.exceptions import TimeoutException
from tests.integration.p019_interlock_support import _seed_interlock_case
from tests.integration.test_p019_money_writers_refuse_non_serializable_postgres import _run_writer
from tests.integration.test_p019_t1908_lock_removal_experiments_postgres import _inject_runner, _seed_pair, stand  # noqa: F401
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture


async def _trust(sessions, call) -> None:
    async with sessions() as s:
        service = TrustLineService(s)
        batch = service.begin_internal_batch()
        await call(service, batch)
        await batch.finish()
        await s.commit()


async def _live(sessions, eq, creditor, debtor):
    async with sessions() as s:
        return await s.scalar(select(TrustLine.id).where(TrustLine.equivalent_id == eq.id, TrustLine.status != "closed",
                                                         TrustLine.from_participant_id == creditor.id,
                                                         TrustLine.to_participant_id == debtor.id))


@pytest.mark.asyncio
async def test_a_line_created_after_the_lock_is_never_used_unlocked(stand, monkeypatch) -> None:  # noqa: F811
    eq, x, y = await _seed_pair(stand, "NL")
    async with stand() as s:
        await s.execute(update(TrustLine).where(TrustLine.from_participant_id == y.id).values(status="closed"))
        await s.commit()
    paused, go, staged, go2 = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    lock = MoneyBoundary.lock_pair_lines

    async def lock_then_pause(self, *args, **kwargs):
        rows = await lock(self, *args, **kwargs)
        paused.set()
        await go.wait()
        return rows

    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", lock_then_pause)
    runner, run, scenario, _ = _inject_runner(eq, [x, y], creditor=y, debtor=x, amount="10.00")

    async def inject():
        async with stand() as session:
            commit = session.commit

            async def pause_then_commit():  # the book has completed (its growth check passed); COMMIT is next
                if paused.is_set():
                    staged.set()
                    await go2.wait()
                return await commit()

            session.commit = pause_then_commit
            await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)

    injecting = asyncio.create_task(inject())
    await asyncio.wait_for(paused.wait(), 20)
    creating = asyncio.create_task(_trust(stand, lambda svc, b: svc.execute_create(
        b, y.id, TrustLineCreateRequest(to=x.pid, equivalent=eq.code, limit="100", signature="-"), require_signature=False)))
    await asyncio.wait([creating], timeout=2)  # done on the old code; waits on the X -> Y lock after the fix
    go.set()
    await asyncio.wait([injecting, asyncio.create_task(staged.wait())], return_when=asyncio.FIRST_COMPLETED, timeout=20)
    if creating.done() and (new := await _live(stand, eq, y, x)) is not None:
        await _trust(stand, lambda svc, b: svc.execute_close(b, new, y.id, TrustLineCloseRequest(signature="-"),
                                                            require_signature=False))
    go2.set()
    await asyncio.wait_for(asyncio.gather(injecting, creating), 30)
    async with stand() as s:
        debt = await s.scalar(select(Debt.amount).where(Debt.debtor_id == x.id, Debt.creditor_id == y.id))
    assert not debt or await _live(stand, eq, y, x), f"a debt {debt} X -> Y without a live supporting line Y -> X"


@pytest.mark.asyncio
async def test_trust_growth_never_overwrites_a_limit_patched_while_it_computed(committed_database, monkeypatch) -> None:
    seed, patching, original = await _seed_interlock_case(), [], trust_drift_engine._set_limit_internally
    a_id, b_id, _c = seed["participant_ids"]
    line = select(TrustLine.limit).where(TrustLine.from_participant_id == b_id, TrustLine.to_participant_id == a_id)

    async def patch_then_set(*args, **kwargs):  # growth has computed from 200; a PATCH to 300 commits (or waits)
        async with committed_database.sessionmaker() as s:
            line_id = await s.scalar(select(TrustLine.id).where(line.whereclause))
        patching.append(asyncio.create_task(_trust(committed_database.sessionmaker, lambda svc, b: svc.execute_update(
            b, line_id, b_id, TrustLineUpdateRequest(limit="300", signature="-"), require_signature=False))))
        await asyncio.wait(patching, timeout=2)
        return await original(*args, **kwargs)

    monkeypatch.setattr(trust_drift_engine, "_set_limit_internally", patch_then_set)
    async with committed_database.sessionmaker() as session:
        await _run_writer("trust_growth", session, seed, committed_database)
        await session.commit()
    await asyncio.wait_for(asyncio.gather(*patching), 20)
    async with committed_database.sessionmaker() as s:
        assert patching and await s.scalar(line) == Decimal("300")


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["clearing", "inject"])
async def test_a_held_line_bounds_the_owner_wait(owner, stand, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 1)
    seed = await _seed_interlock_case() if owner == "clearing" else None
    eq, x, y = (None, None, None) if seed else await _seed_pair(stand, "BW")
    async with stand() as holder, stand() as s:
        await holder.execute(select(TrustLine.id).where(
            TrustLine.equivalent_id == (seed["equivalent_id"] if seed else eq.id)).with_for_update())
        if seed:
            call = ClearingService(s).execute_occurrence(seed["occurrence"])
        else:
            runner, run, scenario, _ = _inject_runner(eq, [x, y], creditor=x, debtor=y, amount="10.00")
            call = runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)
        try:
            outcome = await asyncio.wait_for(call, 8)
        except Exception as exc:  # noqa: BLE001 - compared below
            outcome = exc
        await holder.rollback()
    if seed:
        assert isinstance(outcome, TimeoutException), repr(outcome)
    else:
        assert not isinstance(outcome, asyncio.TimeoutError) and 0 not in run._real_fired_scenario_event_indexes, outcome
    async with stand() as s:
        assert not await s.scalar(text("SELECT count(*) FROM debt_operations WHERE kind IN ('CLEARING', 'INJECT')"))
