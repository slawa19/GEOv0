"""Programme 023, slice (c): the common runner and its committed-progress contract (spec decisions 3, 4, 7, 10).

The runner reads a snapshot, releases the read transaction, plans off the event loop, and executes the plan
cycle by cycle through `ClearingService.execute_occurrence` (slice (b), the 019 boundary). Decision 10: every
durable occurrence is HANDED OFF to the caller right after its commit - before any later error or
cancellation propagates - with its identity (occurrence id, plan UUID, ordinal), its actual amount (atoms and
money text) and its edges (debt id, debtor -> creditor); the pass ends `complete` (a plan on a fresh snapshot
is empty) or `interrupted` with a reason and the remaining work (unexecuted cycles and their planned `V_edge`).
Nothing in production calls the runner in slice (c) (`tests/unit/test_p023_c_runner_is_not_wired.py`).

The stand: two disjoint triangles in one equivalent - T1 a->b->c->a holding 2 each, T2 d->e->f->d holding 3
each - so a plan has two cycles, `V_edge` = 15 and `V_cyc` = 5 differ, and "the first commits, the next ..."
is a real second occurrence. Every test runs on a disposable clone (`MODE_B`); the injected faults go through
REAL paths - the operator stop, a real cancellation, a commit whose acknowledgement is held while the caller
is cancelled, a concurrent v2 occurrence that makes the plan stale, the lease's own expiry - and the planner
is never stubbed (Verification plan §6).

Paths walked: success (complete, with the confirming empty re-plan), the next occurrence failing (operator
stop), the caller cancelling after the first handoff, a commit that became durable while the caller was
cancelled, a stale plan (skip -> tail dropped -> re-plan on a fresh snapshot) and its re-plan limit, the
caller's budget, lease loss with the in-flight occurrence completed, a planner error, the run perimeter.

RED ON A TREE WITHOUT SLICE (c): the surface lookup (`tests/p023_support.py::slice_c_surface`) ends each test on
`TargetMismatch`.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clearing.service import ClearingService
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.utils.exceptions import BadRequestException, ConflictException
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import Edge, debt_uuid, participant_uuid, ring, seed_graph
from tests.p023_support import positive_debt_total, remaining_debts, slice_c_surface

pytestmark = MODE_B

ATOM = 10**8
CODE = "PQC"
T1 = ring(["p023ca", "p023cb", "p023cc"], ["2", "2", "2"], [debt_uuid(0x2303, k) for k in range(3)])
T2 = ring(["p023cd", "p023ce", "p023cf"], ["3", "3", "3"], [debt_uuid(0x2303, 10 + k) for k in range(3)])


async def _seed(db_session, edges=None):
    return await seed_graph(db_session, CODE, list(edges if edges is not None else T1 + T2), precision=2)


def _debt_ids(cycle) -> set:
    return {e.debt_id for e in cycle}


def _triangle_of(occurrence):
    ids = {edge.debt_id for edge in occurrence.edges}
    return "T1" if ids == _debt_ids(T1) else "T2" if ids == _debt_ids(T2) else ids


async def _clearings(factory) -> list[tuple[str, str]]:
    async with factory() as session:
        rows = (
            await session.execute(select(Transaction.tx_id, Transaction.state).where(Transaction.type == "CLEARING"))
        ).all()
    return sorted((tx_id, state) for tx_id, state in rows)


async def _left(factory) -> list:
    async with factory() as session:
        return await remaining_debts(session, CODE)


def _spy_execute(monkeypatch, before_call):
    """Delegate to the real `execute_occurrence`, running `before_call(n, occurrence)` first (n from 1)."""

    real = ClearingService.execute_occurrence
    calls: list = []

    async def spy(self, occurrence, **kwargs):
        calls.append(occurrence)
        await before_call(len(calls), occurrence)
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", spy)
    return calls, real


def _assert_handoff_shape(occurrence, cycle) -> None:
    by_id = {e.debt_id: e for e in cycle}
    assert {edge.debt_id for edge in occurrence.edges} == set(by_id)
    for edge in occurrence.edges:
        assert (edge.debtor_id, edge.creditor_id) == (
            participant_uuid(by_id[edge.debt_id].debtor),
            participant_uuid(by_id[edge.debt_id].creditor),
        )
    # Edges in cycle order: each creditor is the next edge's debtor.
    k = len(occurrence.edges)
    assert all(occurrence.edges[i].creditor_id == occurrence.edges[(i + 1) % k].debtor_id for i in range(k))
    assert occurrence.amount == Decimal(occurrence.amount_atoms).scaleb(-8)
    assert Decimal(occurrence.amount_text) == occurrence.amount


# ---------------------------------------------------------------------------------------------- control


@pytest.mark.asyncio
async def test_a_full_pass_hands_off_every_occurrence_and_ends_complete(db_session) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []

    result = await api.run_clearing_pass(factory, CODE, on_committed=handed.append)

    assert result.status == "complete" and result.reason is None, result
    assert list(result.committed) == handed and len(handed) == 2
    assert sorted(_triangle_of(o) for o in handed) == ["T1", "T2"]
    for occurrence in handed:
        cycle = T1 if _triangle_of(occurrence) == "T1" else T2
        _assert_handoff_shape(occurrence, cycle)
        assert occurrence.amount_atoms == int(Decimal(cycle[0].amount) * ATOM)
        assert occurrence.after_cancellation is False
    # One plan: its two occurrences share the plan UUID and are ordinals 0 and 1; the second plan was the
    # confirming re-plan on a fresh snapshot, and it was empty.
    assert len({o.plan_id for o in handed}) == 1 and sorted(o.ordinal for o in handed) == [0, 1]
    assert result.plans == 2
    assert result.remaining_cycles == 0 and result.remaining_v_edge_atoms == 0
    assert result.v_edge_atoms == 15 * ATOM and result.v_cyc_atoms == 5 * ATOM
    assert await _clearings(factory) == sorted((o.occurrence_id, "COMMITTED") for o in handed)
    assert await _left(factory) == []
    assert result.distributed_exclusive is False, "no lease was passed: no exclusivity is claimed"


# ------------------------------------------------------------------------------------- first commits, next


@pytest.mark.asyncio
async def test_first_commits_next_fails_the_first_is_handed_off_before_the_error_propagates(db_session, monkeypatch) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []

    async def stop_before_the_second(n, _occurrence):
        if n == 2:
            async with factory() as session:
                await session.execute(update(Equivalent).where(Equivalent.code == CODE).values(is_active=False))
                await session.commit()

    calls, _ = _spy_execute(monkeypatch, stop_before_the_second)
    with pytest.raises(api.ClearingPassError) as failed:
        await api.run_clearing_pass(factory, CODE, on_committed=handed.append)

    assert len(calls) == 2, "control: the second occurrence was attempted"
    assert isinstance(failed.value.cause, ConflictException), failed.value.cause
    result = failed.value.result
    assert result.status == "interrupted" and result.reason == "error"
    assert len(handed) == 1 and list(result.committed) == handed
    first = handed[0]
    assert first.occurrence_id == calls[0].occurrence_id
    assert result.remaining_cycles == 1
    rest = T2 if _triangle_of(first) == "T1" else T1
    assert result.remaining_v_edge_atoms == 3 * int(Decimal(rest[0].amount) * ATOM)
    assert await _clearings(factory) == [(first.occurrence_id, "COMMITTED")]
    assert {row[0] for row in await _left(factory)} == {str(e.debt_id) for e in rest}


@pytest.mark.asyncio
async def test_first_commits_next_is_cancelled_the_first_is_handed_off_and_no_next_cycle_runs(db_session) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []
    task_box: dict = {}

    def cancel_after_the_first(occurrence):
        handed.append(occurrence)
        task_box["task"].cancel()

    task = asyncio.create_task(api.run_clearing_pass(factory, CODE, on_committed=cancel_after_the_first))
    task_box["task"] = task
    with pytest.raises(api.ClearingPassCancelled) as cancelled:
        await asyncio.wait_for(task, timeout=60)
    assert isinstance(cancelled.value, asyncio.CancelledError), "cancellation still propagates as cancellation"
    result = cancelled.value.result
    assert result.status == "interrupted" and result.reason == "cancelled"
    assert len(handed) == 1 and list(result.committed) == handed
    assert result.remaining_cycles == 1
    assert await _clearings(factory) == [(handed[0].occurrence_id, "COMMITTED")]
    rest = T2 if _triangle_of(handed[0]) == "T1" else T1
    assert {row[0] for row in await _left(factory)} == {str(e.debt_id) for e in rest}


@pytest.mark.asyncio
async def test_a_commit_that_lands_while_the_caller_is_cancelled_is_handed_off(db_session, monkeypatch) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []
    real_commit = AsyncSession.commit
    committed, release = asyncio.Event(), asyncio.Event()
    state = {"armed": False}

    async def commit_then_hold_the_ack(session):
        await real_commit(session)
        if state["armed"]:
            state["armed"] = False
            committed.set()
            await release.wait()

    def arm_for_the_second(occurrence):
        handed.append(occurrence)
        state["armed"] = True

    monkeypatch.setattr(AsyncSession, "commit", commit_then_hold_the_ack)
    task = asyncio.create_task(api.run_clearing_pass(factory, CODE, on_committed=arm_for_the_second))
    await asyncio.wait_for(committed.wait(), timeout=60)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(api.ClearingPassCancelled) as cancelled:
        await asyncio.wait_for(task, timeout=60)
    monkeypatch.setattr(AsyncSession, "commit", real_commit)

    result = cancelled.value.result
    assert result.status == "interrupted" and result.reason == "cancelled"
    assert len(handed) == 2 and list(result.committed) == handed
    assert handed[0].after_cancellation is False and handed[1].after_cancellation is True
    assert result.remaining_cycles == 0
    assert await _clearings(factory) == sorted((o.occurrence_id, "COMMITTED") for o in handed)
    assert await _left(factory) == []


# --------------------------------------------------------------------------------------------- stale plan


async def _make_t2_stale(real, factory, plan_occurrence):
    """A concurrent v2 occurrence of ANOTHER plan clears 1 unit around T2: every T2 edge now holds 2 < 3."""

    from app.core.clearing.service import ClearingOccurrence
    import uuid

    concurrent = ClearingOccurrence(
        plan_id=uuid.UUID("0a023c00-0000-4000-8000-0000000000cc"),
        equivalent_id=plan_occurrence.equivalent_id,
        ordinal=0,
        debt_ids=plan_occurrence.debt_ids,
        amount_atoms=1 * ATOM,
    )
    async with factory() as session:
        assert await real(ClearingService(session), concurrent) == Decimal("1")


@pytest.mark.asyncio
async def test_a_stale_occurrence_drops_the_tail_and_replans_on_a_fresh_snapshot(db_session, monkeypatch) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []
    box: dict = {}

    async def make_t2_stale(n, occurrence):
        if set(occurrence.debt_ids) == _debt_ids(T2) and not box:
            box["stale"] = occurrence
            await _make_t2_stale(real, factory, occurrence)

    calls, real = _spy_execute(monkeypatch, make_t2_stale)
    result = await api.run_clearing_pass(factory, CODE, on_committed=handed.append)

    assert result.status == "complete", result
    stale = box["stale"]
    assert stale.amount_atoms == 3 * ATOM
    assert stale.occurrence_id not in {o.occurrence_id for o in handed}, "a skipped occurrence is not progress"
    # T2 came back on a NEW plan with the amount the fresh snapshot allows.
    t2 = [o for o in handed if _triangle_of(o) == "T2"]
    assert len(t2) == 1 and t2[0].amount_atoms == 2 * ATOM and t2[0].plan_id != stale.plan_id
    assert result.plans == 3  # the first plan, the re-plan after the skip, the confirming empty plan
    assert await _left(factory) == []


@pytest.mark.asyncio
async def test_the_replan_limit_ends_the_pass_interrupted_with_the_dropped_tail_as_remaining_work(db_session, monkeypatch) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []

    async def make_t2_stale(n, occurrence):
        if set(occurrence.debt_ids) == _debt_ids(T2):
            await _make_t2_stale(real, factory, occurrence)

    calls, real = _spy_execute(monkeypatch, make_t2_stale)
    result = await api.run_clearing_pass(factory, CODE, on_committed=handed.append, max_replans=0)

    assert result.status == "interrupted" and result.reason == "replan_limit", result
    assert [_triangle_of(o) for o in handed] == ["T1"] or not handed
    t1_committed = [o for o in handed if _triangle_of(o) == "T1"]
    # Whatever order the plan had, the remaining work is exactly the cycles not executed from the skip on.
    assert result.remaining_cycles == 2 - len(t1_committed)
    assert result.remaining_v_edge_atoms == 9 * ATOM + (0 if t1_committed else 6 * ATOM)


# ---------------------------------------------------------------------------------------- budget and lease


@pytest.mark.asyncio
async def test_the_callers_budget_is_checked_between_occurrences(db_session) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []
    now = {"t": 0.0}

    def spend_the_budget(occurrence):
        handed.append(occurrence)
        now["t"] = 200.0

    result = await api.run_clearing_pass(
        factory, CODE, on_committed=spend_the_budget, deadline=100.0, clock=lambda: now["t"]
    )
    assert result.status == "interrupted" and result.reason == "budget_exhausted", result
    assert len(handed) == 1 and result.remaining_cycles == 1
    assert len(await _clearings(factory)) == 1


@pytest.mark.asyncio
async def test_lease_loss_completes_the_occurrence_in_flight_and_starts_no_new_cycle(db_session, monkeypatch) -> None:
    api = slice_c_surface()
    from tests.unit.test_p023_c_renewable_lease import KEY, _Clock, _FakeRedis

    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []
    clock = _Clock()
    redis = _FakeRedis(clock)
    lease = api.RenewableLease(redis, KEY, clock=clock)
    await lease.acquire(wait_timeout_seconds=0.0)

    async def lose_the_lease_during_the_first(n, _occurrence):
        if n == 1:
            clock.now += 10_000.0  # past TTL - margin, with no confirmed renewal
            assert lease.lost

    calls, _ = _spy_execute(monkeypatch, lose_the_lease_during_the_first)
    result = await api.run_clearing_pass(factory, CODE, on_committed=handed.append, lease=lease)

    assert result.status == "interrupted" and result.reason == "lease_lost", result
    assert len(calls) == 1, "no cycle started after the loss"
    assert len(handed) == 1 and handed[0].occurrence_id == calls[0].occurrence_id, "the in-flight one completed"
    assert result.remaining_cycles == 1
    assert result.distributed_exclusive is True
    assert len(await _clearings(factory)) == 1


# ------------------------------------------------------------------------------- planner error, perimeter


@pytest.mark.asyncio
async def test_a_planner_error_is_an_interrupted_error_with_no_plan_and_no_effect(db_session) -> None:
    api = slice_c_surface()
    from app.core.clearing.flow_planner import PlanIntegrityError

    opposing = [
        Edge(debt_uuid(0x2303, 30), "p023cx", "p023cy", "1"),
        Edge(debt_uuid(0x2303, 31), "p023cy", "p023cx", "1"),
    ]
    await _seed(db_session, T1 + opposing)
    factory = sessionmaker_of(db_session)
    async with factory() as session:
        before = await positive_debt_total(session, CODE)
    handed: list = []
    with pytest.raises(api.ClearingPassError) as failed:
        await api.run_clearing_pass(factory, CODE, on_committed=handed.append)
    assert isinstance(failed.value.cause, PlanIntegrityError), failed.value.cause
    result = failed.value.result
    assert result.status == "interrupted" and result.reason == "error"
    assert result.committed == () and handed == []
    assert result.remaining_cycles is None and result.remaining_v_edge_atoms is None, "no plan: not measured, not 0"
    async with factory() as session:
        assert await positive_debt_total(session, CODE) == before


@pytest.mark.asyncio
async def test_the_run_perimeter_bounds_the_pass(db_session) -> None:
    api = slice_c_surface()
    await _seed(db_session)
    factory = sessionmaker_of(db_session)
    handed: list = []
    result = await api.run_clearing_pass(
        factory, CODE, on_committed=handed.append, allowed_participant_pids={"p023ca", "p023cb", "p023cc"}
    )
    assert result.status == "complete"
    assert [_triangle_of(o) for o in handed] == ["T1"]
    assert {row[0] for row in await _left(factory)} == {str(e.debt_id) for e in T2}

    nobody = await api.run_clearing_pass(factory, CODE, allowed_participant_pids=set())
    assert nobody.status == "complete" and nobody.committed == ()
    assert {row[0] for row in await _left(factory)} == {str(e.debt_id) for e in T2}


# ------------------------------------------------------------------------------------ the awaited entry


@pytest.mark.asyncio
async def test_the_awaited_entry_refuses_when_clearing_is_disabled_and_runs_under_the_equivalents_lease(
    db_session, monkeypatch
) -> None:
    api = slice_c_surface()
    from app.config import settings
    from tests.unit.test_p023_c_renewable_lease import _FakeRedis

    import time

    await _seed(db_session)
    factory = sessionmaker_of(db_session)

    monkeypatch.setattr(settings, "CLEARING_ENABLED", False)
    with pytest.raises(BadRequestException):
        await api.run_awaited_clearing(factory, None, CODE)
    assert await _clearings(factory) == []
    monkeypatch.setattr(settings, "CLEARING_ENABLED", True)

    redis = _FakeRedis(time.monotonic)
    redis.steal(f"dlock:clearing:{CODE}")  # another owner holds this equivalent
    with pytest.raises(ConflictException):
        await api.run_awaited_clearing(factory, redis, CODE, wait_timeout_seconds=0.0)
    assert await _clearings(factory) == []
    del redis.store[f"dlock:clearing:{CODE}"]

    result = await api.run_awaited_clearing(factory, redis, CODE, wait_timeout_seconds=0.0)
    assert result.status == "complete" and len(result.committed) == 2
    assert result.distributed_exclusive is True
    assert f"dlock:clearing:{CODE}" not in redis.store, "the lease is released after the pass"

    without_redis = await api.run_awaited_clearing(factory, None, CODE)
    assert without_redis.status == "complete" and without_redis.distributed_exclusive is False
