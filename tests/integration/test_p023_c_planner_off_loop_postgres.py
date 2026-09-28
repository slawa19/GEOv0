"""Programme 023, slice (c): planning OFF the event loop, cancellation and lease loss around it - the REQUIRED test.

Spec "Стадии", row (c), and the consultation `2026-09-26-023-acceptance` (P2, `PLANNER-THREAD-TEST: REQUIRED`):
on the REAL runner with a stress plan -

1. the snapshot's read transaction is released BEFORE the CPU work is handed over;
2. a concurrent request completes WHILE PLANNING IS STILL RUNNING;
3. cancellation, or lease loss, starts no next cycle - including after the worker's LATE result;
4. cancelling the await is not passed off as stopping the CPU worker.

The planner is the real `flow_planner.plan_clearing` on a real stress graph (200 participants, 2 000 debts with
atoms up to 10^20 - 1, the `largeatoms` shape of slice (a)). It is not stubbed (Verification plan §6): the
runner's reference is wrapped by a SPY that calls the real planner and only records, with thread-safe events,
when it started and finished and on which thread. "While planning is still running" is therefore an
observation - the finish event not yet set when the concurrent request returned - and not a timing guess;
if the planner ever became too fast for this stand to see the overlap, the test fails on that assertion
rather than passing without having seen it.

What "a concurrent request" is here: an HTTP request through the application (`GET /healthz`) and a database
round trip on a fresh session that also reads `pg_stat_activity` - so the same probe proves item 1: no
connection of this database is `idle in transaction` while the planner runs.

RED ON A TREE WITHOUT SLICE (c): the surface lookup (`tests/p023_support.py::slice_c_surface`) ends each test on
`TargetMismatch`.
"""

from __future__ import annotations

import asyncio
import random
import threading
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.clearing.service import ClearingService
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import Edge, debt_uuid, seed_graph
from tests.p023_support import positive_debt_total, slice_c_surface, target_xfail_023

pytestmark = [target_xfail_023("(c)", "no runner that plans off the event loop"), MODE_B]

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


class _PlannerSpy:
    """Calls the REAL planner; records start, finish and the thread. Events are `threading.Event`s."""

    def __init__(self, real) -> None:
        self.real = real
        self.started = threading.Event()
        self.finished = threading.Event()
        self.thread: int | None = None
        self.cycles: int | None = None

    def __call__(self, edges):
        self.thread = threading.get_ident()
        self.started.set()
        try:
            plan = self.real(edges)
            self.cycles = len(plan.cycles)
            return plan
        finally:
            self.finished.set()


async def _until(event: threading.Event, what: str, timeout: float = 120.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not event.is_set():
        assert loop.time() < deadline, f"the stand never saw: {what}"
        await asyncio.sleep(0.001)


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
    spy = _PlannerSpy(api.runner.plan_clearing)
    monkeypatch.setattr(api.runner, "plan_clearing", spy)
    async with factory() as session:
        total = await positive_debt_total(session, CODE)
    return api, factory, spy, total


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
    api, factory, spy, total = await _stand(db_session, monkeypatch)
    loop_thread = threading.get_ident()

    task = asyncio.create_task(api.run_clearing_pass(factory, CODE, max_replans=0))
    await _until(spy.started, "the planner starting")

    response = await client.get("/healthz")
    idle_in_transaction = await _idle_in_transaction(factory)
    served_while_planning = not spy.finished.is_set()

    assert response.status_code == 200
    assert spy.thread is not None and spy.thread != loop_thread, "the planner ran on the event loop's thread"
    assert served_while_planning, "the concurrent request did not complete while the planner was still running"
    assert idle_in_transaction == 0, "the snapshot's read transaction was still open during planning"

    task.cancel()  # the rest of the pass (thousands of occurrences) is not this test's subject
    with pytest.raises(asyncio.CancelledError):
        await task
    await _until(spy.finished, "the abandoned planner finishing")
    assert spy.cycles and spy.cycles > 100, f"control: a stress plan, {spy.cycles} cycles"


@pytest.mark.asyncio
async def test_cancelling_during_planning_starts_no_cycle_even_after_the_workers_late_result(db_session, monkeypatch) -> None:
    api, factory, spy, total = await _stand(db_session, monkeypatch)
    calls = _spy_execute(monkeypatch)

    task = asyncio.create_task(api.run_clearing_pass(factory, CODE))
    await _until(spy.started, "the planner starting")
    task.cancel()
    with pytest.raises(api.ClearingPassCancelled) as cancelled:
        await task
    worker_was_running = not spy.finished.is_set()

    result = cancelled.value.result
    assert result.status == "interrupted" and result.reason == "cancelled"
    assert result.committed == () and result.remaining_cycles is None, "no plan was received: not measured"
    # Cancelling the await did not stop the CPU worker, and the result says so rather than pretending.
    assert worker_was_running, "control: the cancellation landed while the worker was running"
    assert result.planner_abandoned is True

    await _until(spy.finished, "the worker's late result")
    await asyncio.sleep(0.2)  # give any wrongly scheduled continuation the loop
    assert calls == [], "a cycle started after cancellation"
    async with factory() as session:
        assert await positive_debt_total(session, CODE) == total


@pytest.mark.asyncio
async def test_lease_loss_during_planning_starts_no_cycle_on_the_late_result(db_session, monkeypatch) -> None:
    from tests.unit.test_p023_c_renewable_lease import KEY, _Clock, _FakeRedis

    api, factory, spy, total = await _stand(db_session, monkeypatch)
    calls = _spy_execute(monkeypatch)
    clock = _Clock()
    lease = api.RenewableLease(_FakeRedis(clock), KEY, clock=clock)
    await lease.acquire(wait_timeout_seconds=0.0)

    task = asyncio.create_task(api.run_clearing_pass(factory, CODE, lease=lease))
    await _until(spy.started, "the planner starting")
    clock.now += 10_000.0
    assert lease.lost and not spy.finished.is_set(), "control: the lease was lost while the planner ran"

    result = await asyncio.wait_for(task, timeout=120)
    assert spy.finished.is_set()
    assert result.status == "interrupted" and result.reason == "lease_lost", result
    assert calls == [] and result.committed == ()
    assert result.remaining_cycles == spy.cycles and result.remaining_cycles > 0
    async with factory() as session:
        assert await positive_debt_total(session, CODE) == total
