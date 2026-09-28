"""Programme 023, slice (c): planning OFF the event loop, cancellation and lease loss around it - the REQUIRED test.

Spec "Стадии", row (c), and the consultation `2026-09-26-023-acceptance` (P2, `PLANNER-THREAD-TEST: REQUIRED`):
on the REAL runner with a stress plan -

1. the snapshot's read transaction is released BEFORE the CPU work is handed over;
2. a concurrent request completes WHILE PLANNING IS STILL RUNNING;
3. cancellation, or lease loss, starts no next cycle - including after the worker's LATE result;
4. cancelling the await is not passed off as stopping the CPU worker.

The planner is the real `flow_planner.plan_clearing`, run by the runner's real default planner pool, on a real stress
graph (200 participants, 2 000 debts with atoms up to 10^20 - 1, the `largeatoms` shape of slice (a); about 0.7 s of
planning on this machine). Nothing is stubbed (Verification plan §6). The test only WATCHES the pool: the runner's
default pool is wrapped by a recorder that delegates every `submit` to it and keeps the returned future. "While
planning is still running" is therefore an observation - the plan's future not yet done when the concurrent request
returned - not a timing guess; if the stand ever stopped seeing the overlap, the test fails on that assertion
rather than passing without having seen it. If the runner planned on the loop - calling the planner directly, or
blocking on the future - the recorder sees no pending plan while the request runs, and the test is red.

HISTORY (2026-09-28, recorded in the spec): the first implementation planned in a THREAD. This test was red on it:
`GET /healthz` issued while the plan ran took 0.71 s - the plan's own duration - because the thread starved the loop
through the GIL. The runner now plans in a separate process.

What "a concurrent request" is here: an HTTP request through the application (`GET /healthz`) and a database round
trip on a fresh session that also reads `pg_stat_activity` - so the same probe proves item 1: no connection of this
database is `idle in transaction` while the planner runs.

RED ON A TREE WITHOUT SLICE (c): the surface lookup (`tests/p023_support.py::slice_c_surface`) ends each test on
`TargetMismatch`.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.clearing.service import ClearingService
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import Edge, debt_uuid, seed_graph
from tests.p023_support import positive_debt_total, require_target, slice_c_surface

pytestmark = MODE_B

CODE = "PQS"
PARTICIPANTS = 200
DEBTS = 2000
MAX_ATOMS = 10**20 - 1


def _stress_edges() -> list[Edge]:
    """A deterministic dense graph: unique pairs, no opposing pair (the book nets those), large atoms."""

    rnd = random.Random("023c-planner-off-loop")
    pids = [f"p023cs{i:03d}" for i in range(PARTICIPANTS)]
    pairs: set[tuple[int, int]] = set()
    while len(pairs) < DEBTS:
        u, v = rnd.randrange(PARTICIPANTS), rnd.randrange(PARTICIPANTS)
        if u == v or (u, v) in pairs or (v, u) in pairs:
            continue
        pairs.add((u, v))
    edges = []
    for n, (u, v) in enumerate(sorted(pairs)):
        atoms = rnd.randrange(10**11, MAX_ATOMS)
        edges.append(Edge(debt_uuid(0x2304, n), pids[u], pids[v], format(Decimal(atoms).scaleb(-8), "f")))
    return edges


class _RecordingPool:
    """Delegates to the runner's real default pool; keeps every submitted future. Watches, changes nothing."""

    def __init__(self, real) -> None:
        self.real = real
        self.futures: list = []

    def submit(self, fn, *args, **kwargs):
        future = self.real.submit(fn, *args, **kwargs)
        self.futures.append(future)
        return future


async def _until(condition, what: str, timeout: float = 120.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, f"the stand never saw: {what}"
        await asyncio.sleep(0.005)


def _spy_execute(monkeypatch) -> list:
    real = ClearingService.execute_occurrence
    calls: list = []

    async def spy(self, occurrence, **kwargs):
        calls.append(occurrence)
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", spy)
    return calls


async def _stand(db_session, monkeypatch):
    api = slice_c_surface()
    await seed_graph(db_session, CODE, _stress_edges(), precision=8)
    factory = sessionmaker_of(db_session)
    pool = _RecordingPool(api.runner._default_planner_executor())
    # Warm the pool so a process start is not what the test measures (a no-op plan: an empty snapshot).
    await asyncio.wrap_future(pool.real.submit(api.runner.plan_clearing, []))
    monkeypatch.setattr(api.runner, "_default_planner_executor", lambda: pool)
    async with factory() as session:
        total = await positive_debt_total(session, CODE)
    return api, factory, pool, total


def _planning(pool) -> bool:
    """The last plan is in the worker's hands (dispatched to the process, `running()`) and has no result yet."""

    return bool(pool.futures) and pool.futures[-1].running() and not pool.futures[-1].done()


@contextlib.asynccontextmanager
async def _running(coro):
    """Run the pass as a task; whatever the test asserts, the task is cancelled and awaited before the clone drops."""

    task = asyncio.create_task(coro)
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(BaseException):
            await task


async def _idle_in_transaction(factory) -> int:
    async with factory() as probe:
        count = (
            await probe.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                )
            )
        ).scalar_one()
        await probe.rollback()
    return int(count)


@pytest.mark.asyncio
async def test_a_concurrent_request_completes_while_planning_runs_and_the_snapshot_is_released(
    db_session, client, monkeypatch
) -> None:
    api, factory, pool, total = await _stand(db_session, monkeypatch)

    async with _running(api.run_clearing_pass(factory, CODE, max_replans=0)) as task:
        await _until(lambda: _planning(pool) or task.done(), "the plan handed to the planner pool")
        assert _planning(pool), "the plan never waited in the pool: planned on the event loop, or already done"

        response = await client.get("/healthz")
        idle_in_transaction = await _idle_in_transaction(factory)
        served_while_planning = _planning(pool)

        assert response.status_code == 200
        assert served_while_planning, "the concurrent request did not complete while the planner was still running"
        assert idle_in_transaction == 0, "the snapshot's read transaction was still open during planning"

    plan = pool.futures[-1].result(timeout=120)
    assert len(plan.cycles) > 100, f"control: a stress plan, {len(plan.cycles)} cycles"


@pytest.mark.asyncio
async def test_cancelling_during_planning_starts_no_cycle_even_after_the_workers_late_result(db_session, monkeypatch) -> None:
    api, factory, pool, total = await _stand(db_session, monkeypatch)
    calls = _spy_execute(monkeypatch)

    async with _running(api.run_clearing_pass(factory, CODE)) as task:
        await _until(lambda: _planning(pool) or task.done(), "the plan handed to the planner pool")
        assert _planning(pool)
        task.cancel()
        with pytest.raises(api.ClearingPassCancelled) as cancelled:
            await task
        worker_was_running = _planning(pool)

    result = cancelled.value.result
    assert result.status == "interrupted" and result.reason == "cancelled"
    assert result.committed == () and result.remaining_cycles is None, "no plan was received: not measured"
    # Cancelling the await did not stop the CPU worker, and the result says so rather than pretending.
    assert worker_was_running, "control: the cancellation landed while the worker was running"
    assert result.planner_abandoned is True

    late = await asyncio.wrap_future(pool.futures[-1])  # the worker's late result arrives ...
    assert len(late.cycles) > 100
    await asyncio.sleep(0.3)  # ... and any wrongly scheduled continuation gets the loop
    assert calls == [], "a cycle started after cancellation"
    async with factory() as session:
        assert await positive_debt_total(session, CODE) == total


@pytest.mark.asyncio
async def test_lease_loss_during_planning_starts_no_cycle_on_the_late_result(db_session, monkeypatch) -> None:
    from tests.unit.test_p023_c_renewable_lease import KEY, _Clock, _FakeRedis

    api, factory, pool, total = await _stand(db_session, monkeypatch)
    calls = _spy_execute(monkeypatch)
    clock = _Clock()
    lease = api.RenewableLease(_FakeRedis(clock), KEY, clock=clock)
    await lease.acquire(wait_timeout_seconds=0.0)

    async with _running(api.run_clearing_pass(factory, CODE, lease=lease)) as task:
        await _until(lambda: _planning(pool) or task.done(), "the plan handed to the planner pool")
        clock.now += 10_000.0
        assert lease.lost and _planning(pool), "control: the lease was lost while the planner ran"
        result = await asyncio.wait_for(task, timeout=120)

    plan = pool.futures[-1].result(timeout=0)
    assert result.status == "interrupted" and result.reason == "lease_lost", result
    assert calls == [] and result.committed == ()
    assert result.remaining_cycles == len(plan.cycles) and result.remaining_cycles > 0
    async with factory() as session:
        assert await positive_debt_total(session, CODE) == total


# ------------------------------------------------------------------------------ fix-delta (review P2-2), red-first


@pytest.mark.asyncio
async def test_a_planner_worker_that_dies_while_idle_does_not_break_every_later_pass(db_session, monkeypatch) -> None:
    from concurrent.futures.process import BrokenProcessPool

    from tests.p020_support import ring

    api = slice_c_surface()
    await seed_graph(db_session, "PQB", ring(["p023cb0", "p023cb1", "p023cb2"], ["2", "2", "2"], [debt_uuid(0x2306, k) for k in range(3)]))
    factory = sessionmaker_of(db_session)
    # A pool of this test's own: the one the module caches is put back by monkeypatch afterwards.
    monkeypatch.setattr(api.runner, "_planner_executor", None)
    created: list = []
    try:
        assert (await api.run_clearing_pass(factory, "PQB")).status == "complete"
        pool = api.runner._planner_executor
        created.append(pool)
        for process in list(pool._processes.values()):
            process.kill()  # the worker dies while idle
        await _until(lambda: bool(pool._broken), "the pool noticing its dead worker")

        with pytest.raises(api.ClearingPassError) as failed:
            await api.run_clearing_pass(factory, "PQB")
        assert isinstance(failed.value.cause, BrokenProcessPool), failed.value.cause  # this pass fails loudly

        try:
            recovered = await api.run_clearing_pass(factory, "PQB")
        except api.ClearingPassError as again:
            require_target(False, f"the broken planner pool stayed cached: the next pass failed too ({again.cause!r})")
        require_target(recovered.status == "complete", f"the next pass did not recover: {recovered}")
        created.append(api.runner._planner_executor)
    finally:
        for pool in created:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
