"""027 `T2706`: reproducers of the §15 review of `0cc44e3f..a2e309e0` (P1, P2 growth, P2 bounded waits)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.core.simulator import trust_drift_engine
from app.core.trustlines.service import TrustLineService
from app.db.sqlstate import sqlstate
from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineCreateRequest, TrustLineUpdateRequest
from app.utils.exceptions import GeoException, TimeoutException
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
async def test_a_line_created_while_a_payment_holds_its_pair_waits_and_is_never_used(stand, monkeypatch) -> None:  # noqa: F811
    """§15 P1 of stage 2 (a line created after the lock was read unlocked), on the payment: Y -> X is closed, a payment
    X -> Y is handed a route over it past the router and pauses holding its pair's locks; the creation of Y -> X must
    WAIT for it (not commit under it), and no debt X -> Y exists without a live supporting line Y -> X. Until 030 S3b
    this ran on the inject effect `inject_debt`, now deleted; the property is the money writers'."""

    for name in ("PREPARE_TIMEOUT_SECONDS", "COMMIT_TIMEOUT_SECONDS", "PAYMENT_TOTAL_TIMEOUT_SECONDS"):
        monkeypatch.setattr(settings, name, 30)
    eq, x, y = await _seed_pair(stand, "NL")
    async with stand() as s:
        await s.execute(update(TrustLine).where(TrustLine.from_participant_id == y.id).values(status="closed"))
        await s.commit()
    paused, go, armed = asyncio.Event(), asyncio.Event(), [True]
    lock = MoneyBoundary.lock_pair_lines

    async def lock_then_pause(self, *args, **kwargs):  # only the payment pauses (the first caller), holding its rows
        rows = await lock(self, *args, **kwargs)
        if armed[0]:
            armed[0] = False
            paused.set()
            await go.wait()
        return rows

    monkeypatch.setattr(MoneyBoundary, "lock_pair_lines", lock_then_pause)

    async def pay():
        async with stand() as session:
            service = PaymentService(session)
            service.router.find_flow_routes = lambda *_a, **_k: [([x.pid, y.pid], Decimal("10.00"))]
            try:
                return (await service.create_payment_internal(x.id, to_pid=y.pid, equivalent=eq.code,
                                                              amount="10.00")).status
            except GeoException as exc:  # a refusal is an outcome
                return exc.details.get("reason") or type(exc).__name__

    paying = asyncio.create_task(pay())
    await asyncio.wait_for(paused.wait(), 20)
    creating = asyncio.create_task(_trust(stand, lambda svc, b: svc.execute_create(
        b, y.id, TrustLineCreateRequest(to=x.pid, equivalent=eq.code, limit="100", signature="-"), require_signature=False)))
    await asyncio.wait([creating], timeout=2)
    waited = not creating.done()
    go.set()
    outcome = await asyncio.wait_for(paying, 30)
    await asyncio.wait_for(creating, 30)
    async with stand() as s:
        debt = await s.scalar(select(Debt.amount).where(Debt.debtor_id == x.id, Debt.creditor_id == y.id))
    assert waited, f"the line Y -> X was created while the payment held its pair's locks: {outcome}"
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


async def _blocked_by(stand, holder) -> bool:
    """True once a session is waiting on `holder`'s row locks (`pg_blocking_pids`): the wait was reached."""
    pid = await holder.scalar(text("SELECT pg_backend_pid()"))
    async with stand() as w:
        for _ in range(200):
            if await w.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE :p = ANY(pg_blocking_pids(pid))"),
                              {"p": pid}):
                return True
            await w.rollback()
            await asyncio.sleep(0.02)
    return False


def _inject_create(eq, x, y):
    """028 F-028-14: an event whose only effect is `create_trustline` Y -> X."""
    runner, run, scenario, _ = _inject_runner(eq, [x, y], [
        {"op": "create_trustline", "from": y.pid, "to": x.pid, "equivalent": eq.code, "limit": "50"}])
    return runner, run, scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["clearing", "inject_create"])
async def test_a_held_line_bounds_the_owner_wait(owner, stand, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 1)
    seed = await _seed_interlock_case() if owner == "clearing" else None
    eq, x, y = (None, None, None) if seed else await _seed_pair(stand, "BW")
    if owner == "inject_create":  # Y -> X closed: the event creates it; X -> Y stays live and is held
        async with stand() as s:
            await s.execute(update(TrustLine).where(TrustLine.from_participant_id == y.id).values(status="closed"))
            await s.commit()
    async with stand() as holder, stand() as s:
        await holder.execute(select(TrustLine.id).where(
            TrustLine.equivalent_id == (seed["equivalent_id"] if seed else eq.id)).with_for_update())
        if seed:
            call = ClearingService(s).execute_occurrence(seed["occurrence"])
        else:
            runner, run, scenario = _inject_create(eq, x, y)
            call = runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)
        task = asyncio.create_task(asyncio.wait_for(call, 8))
        waited = await _blocked_by(stand, holder)
        try:
            outcome = await task
        except Exception as exc:  # noqa: BLE001 - compared below
            outcome = exc
        await holder.rollback()
    assert waited, "the owner never waited on the held line"
    if seed:
        assert isinstance(outcome, TimeoutException), repr(outcome)
    else:  # the inject's transient class: a real 55P03 within the budget, the event stays pending
        assert isinstance(outcome, DBAPIError) and sqlstate(outcome.orig, walk=False) == "55P03", repr(outcome)
        assert 0 not in run._real_fired_scenario_event_indexes
    async with stand() as s:
        assert not await s.scalar(text("SELECT count(*) FROM debt_operations WHERE kind IN ('CLEARING', 'INJECT')"))
        if owner == "inject_create":
            assert not await _live(stand, eq, y, x), "a line created past the refused wait"


@pytest.mark.asyncio
async def test_a_line_released_within_the_budget_lets_the_inject_create(stand) -> None:  # noqa: F811
    """Positive control of F-028-14: the bounded wait is a wait, not a refusal - released in time, it succeeds."""
    eq, x, y = await _seed_pair(stand, "BR")
    async with stand() as s:
        await s.execute(update(TrustLine).where(TrustLine.from_participant_id == y.id).values(status="closed"))
        await s.commit()
    runner, run, scenario = _inject_create(eq, x, y)
    async with stand() as holder, stand() as s:
        await holder.execute(select(TrustLine.id).where(TrustLine.equivalent_id == eq.id).with_for_update())
        task = asyncio.create_task(asyncio.wait_for(
            runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario), 8))
        assert await _blocked_by(stand, holder), "the inject never waited on the held line"
        await holder.rollback()
        await task
    assert 0 in run._real_fired_scenario_event_indexes and await _live(stand, eq, y, x)
