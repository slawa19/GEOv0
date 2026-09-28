"""Programme 023, slice (d): the simulator's tick clearing through the common runner, and the cold-spawn acceptance.

The tick adapter is REAL: `RealTick.maybe_run_clearing` (`app/core/simulator/tick.py`, the tick's call, with its
hard timeout `max(2 s, 4 × budget)` capped by `SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC`) -> `RealTick._run_clearing`
-> `RealClearingEngine` -> the runner, its `spawn` planner process and PostgreSQL. The budget is the normal one
(`SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS`, 250 ms); nothing is raised for the test. Until 021 stage 4 the adapter
was `RealTickClearingCoordinator.maybe_run_clearing` -> `RealRunnerImpl.tick_real_mode_clearing`.

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
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.db.models.transaction import Transaction
from tests.p020_support import debt_uuid, participant_uuid, ring, seed_graph
from tests.p023_support import positive_debt_total, require_target, slow_plan
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
        self.runner = RealRunnerImpl(
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
        """One clearing tick through the tick's own clearing step (`tick.py::RealTick.maybe_run_clearing`)."""

        async with self.factory() as session:
            return await self.runner._tick.maybe_run_clearing(
                session=session,
                run_id=self.run.run_id,
                run=self.run,
                equivalents=[CODE],
                planned_len=0,
                tick_t0=time.monotonic(),
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


def _spy_execute(monkeypatch, before_call=None, *, execute_delay: float = 0.0) -> list:
    """Record every occurrence the runner starts. `execute_delay` emulates a slow database: each occurrence takes
    that long BEFORE the real execution, so a delay above the 250 ms tick budget makes the runner's deadline - checked
    before every cycle start after the first - end the pass after ONE occurrence (`budget_exhausted`)."""

    real = ClearingService.execute_occurrence
    calls: list = []

    async def spy(self, occurrence, **kwargs):
        calls.append((occurrence, kwargs.get("allowed_participant_pids")))
        if before_call is not None:
            await before_call(len(calls))
        if execute_delay:
            await asyncio.sleep(execute_delay)
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", spy)
    return calls


#: A fast database, and one slower than the 250 ms tick budget per occurrence (one occurrence per tick). The slow
#: variant is the anti-vacuum control of the drainage below: it makes the budget end a pass after one occurrence.
EXECUTE_DELAYS = pytest.mark.parametrize("execute_delay", [0.0, 0.3], ids=["fast-db", "slow-db"])
#: How many ticks two cycles may take to drain. The contract guarantees one occurrence per tick (the runner's deadline
#: is not checked before the first cycle start), so two cycles need at most two ticks; the bound leaves room.
DRAIN_TICKS = 5


async def _drain(stand: "_Stand", caplog, *, first_tick: int) -> list[int]:
    """Tick until the graph is drained, at most `DRAIN_TICKS` ticks; every tick must commit at least one occurrence
    unless the tick's HARD TIMEOUT cut it first.

    The contract (023 decision 10; `app/core/clearing/runner.py`, the deadline is checked before every cycle start
    after the first): a tick with a non-empty plan commits at least one occurrence, and may stop on the tick budget
    after any of them (`budget_exhausted`). The one thing above the budget is the tick's hard timeout
    (`RealTick._execute_clearing_with_timeout`, 2 s by default): it may cancel a pass before its first commit -
    observed only under heavy CPU oversubscription. So what a stand may require is progress on every tick that the
    hard timeout did not cut (that tick must have logged `tick_clearing_hard_timeout`) and drainage within a bound -
    not a number of cycles in one tick, which depends on how long a commit takes.
    Returns the committed occurrences per tick.
    """

    per_tick: list[int] = []
    for k in range(DRAIN_TICKS):
        tick_index = first_tick + k
        stand.run.tick_index = tick_index
        before = await stand.clearings()
        caplog.clear()
        await stand.tick()
        made = await stand.clearings() - before
        if made < 1:
            marker = f"tick_clearing_hard_timeout run_id={stand.run.run_id} tick={tick_index} "
            cut = any(marker in r.getMessage() for r in caplog.records)
            assert cut, f"tick {tick_index} committed nothing and was not cut by the hard timeout; per tick: {per_tick}"
        per_tick.append(made)
        if await stand.total() == 0:
            break
    return per_tick


def _budget_does_not_bind(monkeypatch, stand: "_Stand") -> float:
    """For the stands whose SUBJECT is the hard timeout cutting a held second occurrence: the runner must start that
    occurrence, whatever the first commit costs. The tick budget - the runner's soft deadline, which may end the
    pass after the first occurrence - is widened on this stand's tick only, and the hard timeout is held at its
    default value (2 s) through its own cap, so the timeout under test is the product's. Nothing in `app/` changes.
    Returns the hard timeout."""

    monkeypatch.setenv("SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", "2")
    stand.runner._tick._real_clearing_time_budget_ms = 60_000
    hard_timeout = stand.runner._tick.clearing_hard_timeout_sec()
    assert hard_timeout == 2.0, hard_timeout
    return hard_timeout


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


@EXECUTE_DELAYS
@pytest.mark.asyncio
async def test_the_tick_clears_through_the_runner_with_creditor_to_debtor_pids(
    factory, monkeypatch, caplog, execute_delay
) -> None:
    caplog.set_level(logging.INFO)
    stand = await _stand(factory, T1 + T2)
    calls = _spy_execute(monkeypatch, execute_delay=execute_delay)
    # A WARM planner process: this test is about the path, not the first spawn (cold spawn is control 1 below).
    runner = _runner_module()
    await asyncio.wrap_future(runner._default_planner_executor().submit(runner.plan_clearing, []))

    # The per-tick volumes, collected through a wrapper around `stand.tick()`.
    volumes: list[Decimal] = []
    tick = stand.tick

    async def recording_tick() -> dict:
        result = await tick()
        assert isinstance(result[CODE], Decimal)
        volumes.append(result[CODE])
        return result

    stand.tick = recording_tick
    per_tick = await _drain(stand, caplog, first_tick=1)

    # 2026-09-28 (021 `T2109` fix-delta, §15 P2): the old form required both cycles in ONE tick, which the budget
    # does not promise - one commit then `budget_exhausted` is valid (`reason=budget_exhausted committed=1
    # elapsed_ms=297` measured). Now: progress every tick, drained within the bound, and the same totals.
    require_target(len(calls) == 2, f"the tick did not go through the runner's occurrences ({len(calls)} calls)")
    pids = {pid for _, pid in stand.run._real_participants}
    assert all(scope == pids for _, scope in calls), "the run perimeter reaches every occurrence"
    assert await stand.total() == 0 and await stand.clearings() == 2 and sum(per_tick) == 2, per_tick
    if execute_delay:
        # Control: the slow database ends every pass on the budget - one occurrence per committing tick.
        assert [n for n in per_tick if n] == [1, 1], f"the slow database must end every pass on the budget ({per_tick})"
    # One `clearing.done` per committing tick; a tick cut by the hard timeout before a commit publishes nothing.
    done = stand.done_events()
    assert [d["cleared_cycles"] for d in done] == [n for n in per_tick if n], (per_tick, done)
    # Each tick's reported volume is what its `clearing.done` published; together, both cycles' V_cyc.
    assert [Decimal(d["cleared_amount"]) for d in done] == [v for v in volumes if v] and sum(volumes) == Decimal("5")
    assert {(e["from"], e["to"]) for d in done for e in d["cycle_edges"]} == {(e.creditor, e.debtor) for e in T1 + T2}


@pytest.mark.asyncio
async def test_the_simulator_depth_setting_no_longer_limits_execution(factory, monkeypatch) -> None:
    monkeypatch.setenv("SIMULATOR_CLEARING_MAX_DEPTH", "3")
    stand = await _stand(factory, RING4)

    await stand.tick()

    left = await stand.total()
    require_target(left == 0, f"a 4-ring under SIMULATOR_CLEARING_MAX_DEPTH=3: {left} left")


# ---------------------------------------------------------------------------------- cold spawn, control 1


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


@EXECUTE_DELAYS
@pytest.mark.asyncio
async def test_planning_past_the_hard_timeout_is_reported_and_its_late_result_starts_nothing(
    factory, monkeypatch, caplog, execute_delay
) -> None:
    runner = _runner_module()
    stand = await _stand(factory, T1 + T2)
    hard_timeout = stand.runner._tick.clearing_hard_timeout_sec()
    real_pool = runner._default_planner_executor()
    await asyncio.wrap_future(real_pool.submit(runner.plan_clearing, []))  # warm: the delay, not a spawn, is timed
    pool = _DelegatingPool(real_pool)
    monkeypatch.setattr(runner, "_default_planner_executor", lambda: pool)
    calls = _spy_execute(monkeypatch, execute_delay=execute_delay)
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

    # Once the abandoned work is done, the next tick commits (at least one occurrence - the budget may end the pass
    # after it) and the graph drains within the bound. 021 `T2109` fix-delta: was "both cycles on the next tick".
    pool.delay = None
    per_tick = await _drain(stand, caplog, first_tick=2)
    assert len(calls) == 2 and await stand.total() == 0 and await stand.clearings() == 2, per_tick
    if execute_delay:
        # Control: the slow database ends every pass on the budget - one occurrence per committing tick.
        assert [n for n in per_tick if n] == [1, 1], f"the slow database must end every pass on the budget ({per_tick})"


# ------------------------------------------------------------------------------ cancellation after a commit


@EXECUTE_DELAYS
@pytest.mark.asyncio
async def test_a_tick_cancelled_after_a_commit_keeps_its_progress(factory, monkeypatch, caplog, execute_delay) -> None:
    stand = await _stand(factory, T1 + T2)
    # 021 `T2109` fix-delta: the second occurrence must START for the hard timeout to cut it; with the default budget
    # a slow first commit ended the pass on the budget instead (1 call). The budget does not bind on this stand; the
    # hard timeout is the product's 2 s.
    hard_timeout = _budget_does_not_bind(monkeypatch, stand)

    async def before(n: int) -> None:
        if n == 2:
            await asyncio.sleep(hard_timeout + 5.0)

    calls = _spy_execute(monkeypatch, before, execute_delay=execute_delay)
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


# ------------------------------------------------------------ the committed volume survives the hard timeout


@EXECUTE_DELAYS
@pytest.mark.asyncio
async def test_a_timeout_after_a_commit_reports_the_committed_volume(factory, monkeypatch, execute_delay) -> None:
    """Programme 021 stage 4, `specs/BACKLOG.md` ("Класс 2 из §15-ревью среза (d) программы 023", item 1).

    One occurrence commits, the next is held past the tick's hard timeout. The first is durable - in the database
    and in `clearing.done` - so the volume the tick reports for this equivalent (what feeds the `clearing_volume`
    metric) must be that occurrence's `V_cyc`, not zero. On `2df5703` the coordinator initialises the volume to
    zero and assigns the real one only when the clearing task RETURNS; the timeout cancels the task, and the
    committed volume is lost to the metric while SSE and the database keep it.
    """

    stand = await _stand(factory, T1 + T2)
    hard_timeout = _budget_does_not_bind(monkeypatch, stand)  # as in the test above: the second occurrence starts

    async def before(n: int) -> None:
        if n == 2:
            await asyncio.sleep(hard_timeout + 5.0)

    calls = _spy_execute(monkeypatch, before, execute_delay=execute_delay)
    volumes = await stand.tick()

    # Controls: the stand reached the second occurrence, the first is durable and published.
    assert len(calls) == 2, f"the tick did not reach a second runner occurrence ({len(calls)} calls)"
    assert await stand.clearings() == 1, "the first occurrence is durable"
    left = await stand.total()
    committed_v_cyc = (Decimal("15") - left) / 3
    assert committed_v_cyc in (Decimal("2"), Decimal("3")), left
    [done] = stand.done_events()
    assert Decimal(done["cleared_amount"]) == committed_v_cyc
    assert set(volumes) == {CODE} and isinstance(volumes[CODE], Decimal)

    require_target(
        volumes[CODE] == committed_v_cyc,
        f"the tick reports clearing volume {volumes[CODE]} for {CODE}; committed and published: {committed_v_cyc}",
    )
