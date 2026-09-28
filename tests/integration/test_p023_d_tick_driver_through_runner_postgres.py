"""Programme 023, slice (d): the simulator's tick clearing through the common runner, and the cold-spawn acceptance.

The tick adapter is REAL: `RealTickClearingCoordinator.maybe_run_clearing` (the orchestrator's call, with its hard
timeout `max(2 s, 4 × budget)` capped by `SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC`) -> `RealRunner.
tick_real_mode_clearing` -> `RealClearingEngine` -> the runner, its `spawn` planner process and PostgreSQL. The
budget is the normal one (`SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS`, 250 ms); nothing is raised for the test.

* THROUGH THE RUNNER - the tick's clearing goes through `execute_occurrence` in the run's perimeter; its
  `clearing.done` carries creditor -> debtor PIDs (decision R3: the runner's progress is debtor -> creditor by
  UUID, the wire is the trust-line direction by PID).
* NO EXECUTION DEPTH - `SIMULATOR_CLEARING_MAX_DEPTH=3` no longer limits the tick: a 4-ring clears (R4).
* COLD SPAWN, control 1 (spec (d), P2-2) - a FRESH planner pool, i.e. a worker process spawned by the tick itself,
  no warm-up, a pre-chosen non-empty graph: the first tick's outcome is recorded (printed, whatever it is) and a
  later tick must really clear - debts gone from the database and a `clearing.done` with cycles. Zeros alone do
  not pass. What is NOT fresh: this test process itself (its modules are imported); the worker is.
* COLD SPAWN, control 2 - planning delayed past the hard timeout (the real planner behind a sleep, in the worker):
  the interruption is reported (the coordinator's timeout, the driver's `planner_abandoned`), the late result
  starts nothing, no commit and unchanged debts; once the abandoned work is done the next tick commits.
* CANCELLATION AFTER A COMMIT - the second occurrence held past the hard timeout: the first stays durable in the
  database, in the driver's accounting and in `clearing.done` (SSE).

RED BEFORE THE SWITCH: the engine runs its own detector ladder through the v1 executor; the planner pool and the v2
entry are never used.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.clearing.service import ClearingService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.core.simulator.runtime_utils import safe_int_env
from app.db.models.transaction import Transaction
from tests.p020_support import debt_uuid, participant_uuid, ring, seed_graph
from tests.p023_support import positive_debt_total, require_target, slow_plan, target_xfail_023
from tests.simulator_tick_stand import RecordingSse, install_tick_stand, pooled_sessionmaker_over

CODE = "PQT"
T1 = ring(["p023ta", "p023tb", "p023tc"], ["2", "2", "2"], [debt_uuid(0x23D3, k) for k in range(3)])
T2 = ring(["p023td", "p023te", "p023tf"], ["3", "3", "3"], [debt_uuid(0x23D3, 10 + k) for k in range(3)])
RING4 = ring([f"p023tr{k}" for k in range(4)], ["1"] * 4, [debt_uuid(0x23D3, 20 + k) for k in range(4)])


@pytest_asyncio.fixture
async def factory(committed_database, monkeypatch):
    async with pooled_sessionmaker_over(committed_database.url) as made:
        install_tick_stand(monkeypatch, made)
        yield made


class _Stand:
    def __init__(self, factory, edges) -> None:
        self.factory = factory
        self.sse = RecordingSse()
        pids = sorted({p for e in edges for p in (e.debtor, e.creditor)})
        self.run = RunRecord(run_id="p023d-tick", scenario_id="p023d", mode="real", state="running")
        self.run.tick_index = 1
        self.run._real_seeded = True
        self.run._real_participants = [(participant_uuid(pid), pid) for pid in pids]
        self.run._real_equivalents = [CODE]
        # The scenario topology in the trust-line direction creditor -> debtor.
        self.run._edges_by_equivalent = {CODE: [(e.creditor, e.debtor) for e in edges]}
        self.runner = RealRunner(
            lock=threading.RLock(),
            get_run=lambda _run_id: self.run,
            get_scenario_raw=lambda _run_id: {"equivalents": [CODE], "participants": [{"id": p} for p in pids]},
            sse=self.sse,
            artifacts=None,
            utc_now=lambda: datetime.now(timezone.utc),
            publish_run_status=lambda _run_id: None,
            db_enabled=lambda: True,
            actions_per_tick_max=0,
            clearing_every_n_ticks=1,
            real_max_consec_tick_failures_default=3,
            real_max_timeouts_per_tick_default=10,
            real_max_errors_total_default=50,
            logger=logging.getLogger("p023d.tick"),
        )

    async def tick(self) -> dict:
        """One clearing tick through the real coordinator (the orchestrator's call, `real_tick_orchestrator.py`)."""

        coordinator = self.runner._real_tick_clearing_coordinator
        async with self.factory() as session:
            return await coordinator.maybe_run_clearing(
                session=session,
                run_id=self.run.run_id,
                run=self.run,
                equivalents=[CODE],
                planned_len=0,
                tick_t0=time.monotonic(),
                clearing_enabled=True,
                safe_int_env=safe_int_env,
                run_clearing=lambda: self.runner.tick_real_mode_clearing(session, self.run.run_id, self.run, [CODE]),
                payments_result=None,
            )

    def done_events(self) -> list[dict]:
        return [e for e in self.sse.events if e.get("type") == "clearing.done"]

    async def total(self) -> Decimal:
        async with self.factory() as session:
            return await positive_debt_total(session, CODE)

    async def clearings(self) -> int:
        async with self.factory() as session:
            return (
                await session.execute(select(func.count()).select_from(Transaction).where(Transaction.type == "CLEARING"))
            ).scalar_one()


async def _stand(factory, edges) -> _Stand:
    async with factory() as session:
        await seed_graph(session, CODE, edges, precision=2)
    return _Stand(factory, edges)


def _spy_execute(monkeypatch, before_call=None) -> list:
    real = ClearingService.execute_occurrence
    calls: list = []

    async def spy(self, occurrence, **kwargs):
        calls.append((occurrence, kwargs.get("allowed_participant_pids")))
        if before_call is not None:
            await before_call(len(calls))
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", spy)
    return calls


def _runner_module():
    import app.core.clearing.runner as runner

    return runner


class _DelegatingPool:
    """The runner's planner pool, observed; while `delay` is set, the worker sleeps before the real planner."""

    def __init__(self, real) -> None:
        self.real = real
        self.delay: float | None = None
        self.futures: list = []

    def submit(self, fn, *args, **kwargs):
        if self.delay is not None:
            future = self.real.submit(slow_plan, self.delay, *args, **kwargs)
        else:
            future = self.real.submit(fn, *args, **kwargs)
        self.futures.append(future)
        return future


# ------------------------------------------------------------------------------------------ through the runner


@target_xfail_023("(d)", "the tick's clearing goes through the runner and publishes creditor -> debtor PIDs")
@pytest.mark.asyncio
async def test_the_tick_clears_through_the_runner_with_creditor_to_debtor_pids(factory, monkeypatch) -> None:
    stand = await _stand(factory, T1 + T2)
    calls = _spy_execute(monkeypatch)

    volumes = await stand.tick()

    require_target(len(calls) == 2, f"the tick did not go through the runner's occurrences ({len(calls)} calls)")
    pids = {pid for _, pid in stand.run._real_participants}
    assert all(scope == pids for _, scope in calls), "the run perimeter reaches every occurrence"
    assert volumes[CODE] == Decimal("5") and isinstance(volumes[CODE], Decimal)
    [done] = stand.done_events()
    assert done["cleared_cycles"] == 2 and Decimal(done["cleared_amount"]) == Decimal("5")
    assert {(e["from"], e["to"]) for e in done["cycle_edges"]} == {(e.creditor, e.debtor) for e in T1 + T2}
    assert await stand.total() == 0


@target_xfail_023("(d)", "SIMULATOR_CLEARING_MAX_DEPTH no longer limits the tick's execution")
@pytest.mark.asyncio
async def test_the_simulator_depth_setting_no_longer_limits_execution(factory, monkeypatch) -> None:
    monkeypatch.setenv("SIMULATOR_CLEARING_MAX_DEPTH", "3")
    stand = await _stand(factory, RING4)

    await stand.tick()

    left = await stand.total()
    require_target(left == 0, f"a 4-ring under SIMULATOR_CLEARING_MAX_DEPTH=3: {left} left")


# ---------------------------------------------------------------------------------- cold spawn, control 1


@target_xfail_023("(d)", "cold spawn: the tick's first planning spawns the planner worker, and a later tick clears")
@pytest.mark.asyncio
async def test_cold_spawn_the_first_tick_is_recorded_and_a_later_tick_really_clears(factory, monkeypatch) -> None:
    runner = _runner_module()
    monkeypatch.setattr(runner, "_planner_executor", None)  # no pool: the tick spawns a fresh worker process
    stand = await _stand(factory, T1 + T2)
    before = await stand.total()
    assert before == Decimal("15")
    created = None
    outcomes: list[str] = []
    try:
        for tick in range(1, 11):
            stand.run.tick_index = tick
            t0 = time.monotonic()
            await stand.tick()
            created = created or runner._planner_executor
            left = await stand.total()
            done = stand.done_events()
            outcomes.append(
                f"tick {tick}: {1000 * (time.monotonic() - t0):.0f} ms, left {left}, clearings {await stand.clearings()}, "
                f"clearing.done {[d['cleared_cycles'] for d in done]}"
            )
            if left == 0:
                break
        print("COLD-SPAWN control 1 outcomes:", *outcomes, sep="\n  ")
        require_target(created is not None, "the tick never used the runner's planner pool")
        assert await stand.total() == 0, outcomes
        assert await stand.clearings() >= 2 and sum(d["cleared_cycles"] for d in stand.done_events()) >= 2, outcomes
    finally:
        if created is not None:
            created.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------------- cold spawn, control 2


@target_xfail_023("(d)", "cold spawn: planning past the hard timeout is reported, its late result starts nothing")
@pytest.mark.asyncio
async def test_planning_past_the_hard_timeout_is_reported_and_its_late_result_starts_nothing(factory, monkeypatch, caplog) -> None:
    runner = _runner_module()
    stand = await _stand(factory, T1 + T2)
    hard_timeout = stand.runner._real_tick_clearing_coordinator.compute_static_clearing_hard_timeout_sec(safe_int_env=safe_int_env)
    real_pool = runner._default_planner_executor()
    await asyncio.wrap_future(real_pool.submit(runner.plan_clearing, []))  # warm: the delay, not a spawn, is timed
    pool = _DelegatingPool(real_pool)
    monkeypatch.setattr(runner, "_default_planner_executor", lambda: pool)
    calls = _spy_execute(monkeypatch)
    caplog.set_level(logging.INFO)

    pool.delay = hard_timeout + 2.0
    await stand.tick()
    require_target(bool(pool.futures), "the tick never handed a plan to the runner's planner pool")

    messages = [r.getMessage() for r in caplog.records]
    assert any("tick_clearing_hard_timeout" in m for m in messages), "the coordinator's timeout is reported"
    assert any("clearing_pass_cancelled" in m and "planner_abandoned=True" in m for m in messages), messages[-20:]
    late = await asyncio.wrap_future(pool.futures[-1])  # the abandoned worker finishes ...
    assert len(late.cycles) == 2, "control: the late result is a real, non-empty plan"
    await asyncio.sleep(0.3)  # ... and a wrongly scheduled continuation would get the loop now
    assert calls == [], "the late result started an occurrence"
    assert await stand.clearings() == 0 and await stand.total() == Decimal("15")

    pool.delay = None
    stand.run.tick_index = 2
    await stand.tick()
    assert len(calls) == 2 and await stand.total() == 0, "once the abandoned work is done the next tick commits"


# ------------------------------------------------------------------------------ cancellation after a commit


@target_xfail_023("(d)", "a tick cancelled after a commit keeps its progress in the DB, the accounting and SSE")
@pytest.mark.asyncio
async def test_a_tick_cancelled_after_a_commit_keeps_its_progress(factory, monkeypatch, caplog) -> None:
    stand = await _stand(factory, T1 + T2)
    hard_timeout = stand.runner._real_tick_clearing_coordinator.compute_static_clearing_hard_timeout_sec(safe_int_env=safe_int_env)

    async def before(n: int) -> None:
        if n == 2:
            await asyncio.sleep(hard_timeout + 5.0)

    calls = _spy_execute(monkeypatch, before)
    caplog.set_level(logging.INFO)
    await stand.tick()

    require_target(len(calls) == 2, f"the tick did not reach a second runner occurrence ({len(calls)} calls)")
    assert await stand.clearings() == 1, "the first occurrence is durable"
    left = await stand.total()
    assert left in (Decimal("9"), Decimal("6")), left  # T1 (6) or T2 (9) cleared, not both
    [done] = stand.done_events()
    # `cleared_amount` is V_cyc (the cycle's amount), not V_edge: a triangle's V_edge is three times it.
    assert done["cleared_cycles"] == 1 and Decimal(done["cleared_amount"]) == (Decimal("15") - left) / 3
    messages = [r.getMessage() for r in caplog.records]
    assert any("clearing_pass_cancelled" in m and "committed=1" in m for m in messages), messages[-20:]
