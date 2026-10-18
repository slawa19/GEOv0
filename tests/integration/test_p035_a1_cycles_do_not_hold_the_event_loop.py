"""035 A1 (`F-035-1`): `GET /clearing/cycles` must not hold the event loop, and (owner decision П1-(а)) answers
with the cycles of the flow plan, computed behind the runner's process boundary.

THE GRAPH AND THE THRESHOLD WERE FIXED BEFORE THE FIRST MEASUREMENT (spec 035, Verification plan: "порог не
подбирается после"). Do not tune either to a measured number.

* `_LAYERS = 12` layers of `_WIDTH = 3` participants; every participant of layer `i` owes every participant of layer
  `i + 1` (mod 12): 36 participants, 108 debts, every vertex with in- and out-degree 3, one strongly connected
  component. Its shortest cycle has 12 edges, so at `max_depth=10` (the API's maximum, `app/api/v1/clearing.py`) the
  DFS of `find_cycles` closes nothing there and walks every simple path of up to 10 edges from each of the 36 starts:
  about 36 * (3**10 + ...) = 3.2 million frames. Strongly connected on purpose: pruning vertices that cannot lie on a
  cycle does not shrink it.
* one disjoint triangle - the admissible cycle, so the answer is NOT empty (an empty answer is not a fix).
* `_LOOP_DELAY_THRESHOLD_SECONDS = 0.25`: a request that only awaits the database yields within milliseconds; a
  quarter of a second is two orders above that and still what a neighbouring request would feel as a stall.

MEASURED ON `75dafc82` (the detectors, `max_depth=10`): the loop was held 1.460 s. With the fix the request carries
no `max_depth` (the parameter left the contract, П1-(а)) and the control of the instrument is a request on a small
equivalent instead of a shallow depth; the graph, the threshold and the assertion are the ones fixed above.

What this does not see: a loop held for less than the threshold, any other route, and how long the planner process
itself works (it shares one worker with clearing passes).

The test of a plan that is slow FOR THE PLANNER (below) applies the same threshold, since 041 `T4112` (2026-10-18)
only to the part of each loop gap that overlaps the plan's own time in flight - see its docstring.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import pytest
from sqlalchemy import update

from app.config import settings
from app.core.clearing import flow_planner, runner
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.conftest import MODE_B, sessionmaker_of
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_scenarios import register_and_login
from tests.p023_support import remaining_debts
from tests.p035_support import held_rotated_plan, stamped_slow_rotated_plan

_LAYERS = 12
_WIDTH = 3
_LOOP_DELAY_THRESHOLD_SECONDS = 0.25


def _people(prefix: str, n: str, size: int) -> list[Participant]:
    return [Participant(pid=f"{prefix}{i}_{n}", display_name=f"{prefix}{i}", public_key=f"pk{prefix}{i}-{n}",
                        type="person", status="active", profile={}) for i in range(size)]


async def _add_debts(db_session, eq, pairs, amount) -> list[Debt]:
    """Debts `debtor -> creditor`, each with its consenting line (`creditor -> debtor`). `amount` is one amount for
    every debt, or one per pair."""

    amounts = [amount] * len(pairs) if isinstance(amount, str) else list(amount)
    assert len(amounts) == len(pairs)
    db_session.add_all([TrustLine(from_participant_id=creditor.id, to_participant_id=debtor.id, equivalent_id=eq.id,
                                  limit=Decimal("100"), status="active", policy={"auto_clearing": True})
                        for debtor, creditor in pairs])
    debts = [Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal(one))
             for (debtor, creditor), one in zip(pairs, amounts)]
    async with debt_fixture_setup(db_session, label="p035-a1"):
        db_session.add_all(debts)
    await db_session.commit()
    return debts


async def _equivalent(db_session, tag: str) -> tuple[Equivalent, str]:
    n = uuid.uuid4().hex[:8].upper()
    eq = Equivalent(code=f"{tag}{n}", symbol=tag, description=None, precision=2, metadata_={}, is_active=True)
    db_session.add(eq)
    await db_session.flush()
    return eq, n


async def _ring(db_session, eq, n: str, prefix: str, size: int, amount) -> list[Debt]:
    people = _people(prefix, n, size)
    db_session.add_all(people)
    await db_session.flush()
    return await _add_debts(db_session, eq, [(p, people[(i + 1) % size]) for i, p in enumerate(people)], amount)


async def _dense_graph_with_one_short_cycle(db_session) -> tuple[Equivalent, set[str]]:
    eq, n = await _equivalent(db_session, "CY")
    layers = [_people(f"L{layer}N", n, _WIDTH) for layer in range(_LAYERS)]
    db_session.add_all([p for layer in layers for p in layer])
    await db_session.flush()
    pairs = [(debtor, creditor) for i, layer in enumerate(layers) for debtor in layer
             for creditor in layers[(i + 1) % _LAYERS]]
    assert len(pairs) == _LAYERS * _WIDTH * _WIDTH
    await _add_debts(db_session, eq, pairs, "10")
    triangle = await _ring(db_session, eq, n, "T", 3, "7")
    return eq, {str(d.id) for d in triangle}


#: Gaps shorter than this are not kept in `_Hold.gaps`: two orders below the threshold, they cannot matter to it.
_GAP_KEPT_ABOVE_SECONDS = 0.01


class _Hold(NamedTuple):
    """`worst`: the longest time one `asyncio.sleep(0)` took to come back - how long anything else on the loop
    waited; `at`: when that gap began, seconds into the window; `window`: how long the probe ran; `started`: the
    window's start and `gaps`: `(start, length)` of every gap above `_GAP_KEPT_ABOVE_SECONDS`, on `perf_counter`."""

    worst: float
    at: float
    window: float
    started: float
    gaps: tuple[tuple[float, float], ...]


async def _worst_scheduling_delay(stop: asyncio.Event) -> _Hold:
    worst = at = 0.0
    gaps = []
    last = started = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0)
        now = time.perf_counter()
        if now - last > worst:
            worst, at = now - last, last - started
        if now - last > _GAP_KEPT_ABOVE_SECONDS:
            gaps.append((last, now - last))
        last = now
    return _Hold(worst, at, last - started, started, tuple(gaps))


async def _get_while_measuring_the_loop(client, url: str, headers) -> tuple[object, _Hold]:
    stop = asyncio.Event()
    probe = asyncio.create_task(_worst_scheduling_delay(stop))
    await asyncio.sleep(0)
    try:
        response = await client.get(url, headers=headers)
    finally:
        stop.set()
        hold = await probe
    return response, hold


def _debt_id_sets(cycles) -> set[frozenset[str]]:
    return {frozenset(edge["debt_id"] for edge in cycle) for cycle in cycles}


@pytest.mark.asyncio
async def test_the_deepest_cycle_search_does_not_hold_the_event_loop(client, db_session):
    eq, triangle = await _dense_graph_with_one_short_cycle(db_session)
    small, n = await _equivalent(db_session, "CS")
    await _ring(db_session, small, n, "S", 3, "7")
    dense_code, small_code = eq.code, small.code  # the route releases its read transaction, which expires `eq`
    user = await register_and_login(client, "P035A1Loop")

    # Anti-vacuum for the instrument: a request on a three-debt equivalent stays under the threshold, so the
    # database, the planner process and the probe itself are not what the assertion below measures.
    control, control_hold = await _get_while_measuring_the_loop(
        client, f"/api/v1/clearing/cycles?equivalent={small_code}", user["headers"])
    control_delay = control_hold.worst
    assert control.status_code == 200 and len(control.json()["cycles"]) == 1, control.text
    assert control_delay < _LOOP_DELAY_THRESHOLD_SECONDS, f"the control request held the loop {control_delay:.3f} s"

    response, hold = await _get_while_measuring_the_loop(
        client, f"/api/v1/clearing/cycles?equivalent={dense_code}", user["headers"])
    delay = hold.worst
    assert response.status_code == 200, response.text
    offered = _debt_id_sets(response.json()["cycles"])
    assert frozenset(triangle) in offered, "the admissible cycle is not offered: an empty answer is not a fix"
    # The dense component is what made the search deep; it is planned too - every one of its debts is on a cycle.
    assert len({debt for cycle in offered for debt in cycle}) == _LAYERS * _WIDTH * _WIDTH + 3, offered
    assert delay < _LOOP_DELAY_THRESHOLD_SECONDS, (
        f"GET /clearing/cycles held the event loop: actual={delay:.3f} s, "
        f"threshold={_LOOP_DELAY_THRESHOLD_SECONDS} s"
    )


@pytest.mark.asyncio
async def test_the_offered_cycles_are_the_plan_computed_off_the_loop(client, db_session, monkeypatch):
    """П1-(а): the answer is the set of cycles of `flow_planner` on the same snapshot, and the plan is computed in the
    planner process (`ProcessPoolExecutor`, as `runner._plan_off_the_loop`), not on the loop.

    A ring of 7 and a triangle: the plan holds both, the retired detectors stop at the default depth of 6. The
    boundary is observed as a `plan_clearing` submission to a process pool; a thread or an inline call does not count.
    The triangle's debts DIFFER (7, 9, 11) and its cycle clears 7: an `amount` that were the cycle's amount would
    read 7.00 on all three edges.
    """

    eq, n = await _equivalent(db_session, "CP")
    await _ring(db_session, eq, n, "R", 7, "10")
    await _ring(db_session, eq, n, "T", 3, ["7", "9", "11"])
    user = await register_and_login(client, "P035A1Plan")

    code = eq.code  # the route releases its read transaction, which expires `eq`
    plan = await flow_planner.plan_for_equivalent(db_session, code)
    planned = {frozenset(str(edge.debt_id) for edge in cycle.edges) for cycle in plan.cycles}
    assert sorted(len(c) for c in planned) == [3, 7], planned

    submitted: list[object] = []
    submit = ProcessPoolExecutor.submit

    def _recording_submit(self, fn, /, *args, **kwargs):
        submitted.append(fn)
        return submit(self, fn, *args, **kwargs)

    monkeypatch.setattr(ProcessPoolExecutor, "submit", _recording_submit)
    response = await client.get(f"/api/v1/clearing/cycles?equivalent={code}", headers=user["headers"])
    assert response.status_code == 200, response.text
    offered = _debt_id_sets(response.json()["cycles"])
    assert offered == planned, (
        f"GET /clearing/cycles differs from the plan: offered cycle lengths={sorted(len(c) for c in offered)}, "
        f"planned cycle lengths={sorted(len(c) for c in planned)}"
    )
    assert flow_planner.plan_clearing in submitted, (
        f"the plan was not handed to a planner process: process-pool submissions={submitted}"
    )
    # Every field of an edge, not only its id: the pids of the debt's two ends and the DEBT's amount on the snapshot
    # at the equivalent's precision - not the amount the cycle would clear (7.00 on every edge of the triangle).
    stored = {debt: (debtor, creditor, f"{amount:.2f}")
              for debt, debtor, creditor, amount in await remaining_debts(db_session, code)}
    answered = {edge["debt_id"]: (edge["debtor"], edge["creditor"], edge["amount"])
                for cycle in response.json()["cycles"] for edge in cycle}
    assert sorted(a for _, _, a in stored.values()) == ["10.00"] * 7 + ["11.00", "7.00", "9.00"], stored
    triangle = next(cycle for cycle in response.json()["cycles"] if len(cycle) == 3)
    assert sorted(edge["amount"] for edge in triangle) == ["11.00", "7.00", "9.00"], (
        f"the triangle's edges carry {[edge['amount'] for edge in triangle]}: each must be its own debt on the "
        f"snapshot (7.00, 9.00, 11.00), not the amount the cycle would clear"
    )
    assert answered == stored, (answered, stored)


# ------------------------------------------------------------------------- a plan that is slow FOR THE PLANNER

#: 041 `T4112`, 2026-10-18, after the Codex review of `4dcc6413`. The test below judges, with the module's fixed
#: `_LOOP_DELAY_THRESHOLD_SECONDS`, only the part of each loop gap that OVERLAPS THE PLAN'S TIME IN FLIGHT: from its
#: hand-over to the pool (stamped in this process just before `submit`) to the end of the plan in the worker (stamped
#: there, `tests/p035_support.py::stamped_slow_rotated_plan`; same clock, see that docstring).
#:
#: * Why: on ubuntu CI the test failed twice on branches with no backend change, 0.478 s (run 39031477460,
#:   2026-10-17) and 0.454 s (run 39116205045, 2026-10-18), against the whole-request 0.25 s. Where in the request
#:   those gaps lay was not recorded; their cause is not established. 32 busy processes on a 16-thread Windows
#:   machine did not reproduce them (largest gap 0.094 s with a warm worker). From now on the failure message and
#:   `p035_a1_loop_hold.json` give every gap's position against the plan's stamps.
#: * `_PLAN_SECONDS` stays 0.6 s (the value of 035): a synchronous wait for the worker overlaps the plan by its whole
#:   time (measured 0.606 s), and a 0.5 s partial wait during it fits inside the plan too. A longer plan only widens
#:   the span in which runner noise is judged.
#: * NOT JUDGED HERE, and said in the failure message: (1) gaps outside the plan's time in flight - authentication,
#:   the snapshot read, building and serializing the answer. The same route on the same graph is judged over the
#:   WHOLE request at the same 0.25 s by the deepest-search test above. (2) The cold start of the diagnostic pool:
#:   the request runs on a FRESH pool whose worker was started just before it by a direct submission, so the first
#:   request after pool creation is the subject, but the creation of the pool and its first `submit()` are not. The
#:   first `submit()` itself holds the loop while the worker process is created - measured 2026-10-18 on Windows at
#:   6-9 ms idle and 0.34-1.04 s under 32 busy processes (BACKLOG 035-16). Judging it here would fail this test on a
#:   loaded runner for a reason that is not the one it guards; its time is recorded instead.
_PLAN_SECONDS = 0.6


def _overlap(gap: tuple[float, float], start: float, end: float) -> float:
    gap_start, length = gap
    return max(0.0, min(gap_start + length, end) - max(gap_start, start))


def _record_hold(**measured) -> None:
    """The last run's numbers, green or red, in one file under the runner's artifact root (overwritten each run)."""

    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/direct-pytest/artifacts")
    root.mkdir(parents=True, exist_ok=True)
    (root / "p035_a1_loop_hold.json").write_text(
        json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in measured.items()}), encoding="utf-8")


class _Handed(list):
    def __init__(self) -> None:
        super().__init__()
        self.futures: list[Future] = []
        self.at: list[float] = []


def _slow_the_planner(monkeypatch, delay_seconds: float, stamp_path: str):
    """Every `plan_clearing` handed to ANY process pool runs `stamped_slow_rotated_plan` in the worker instead: it
    sleeps `delay_seconds` there, returns the real plan with every cycle rotated by one edge and writes its own start
    and end to `stamp_path`. Returns the list of pools that were handed a plan, in order; its `futures` are the worker
    futures and its `at` the `perf_counter` of each hand-over, taken before the pool's own `submit`."""

    handed = _Handed()
    submit = ProcessPoolExecutor.submit

    def _slow_submit(self, fn, /, *args, **kwargs):
        if fn is not flow_planner.plan_clearing:
            return submit(self, fn, *args, **kwargs)
        handed.append(self)
        handed.at.append(time.perf_counter())
        handed.futures.append(submit(self, stamped_slow_rotated_plan, stamp_path, delay_seconds, *args, **kwargs))
        return handed.futures[-1]

    monkeypatch.setattr(ProcessPoolExecutor, "submit", _slow_submit)
    return handed


async def _until(condition, *, seconds: float = 10.0) -> None:
    """Yield to the loop until `condition()` holds - an event of this loop, not a sleep that guesses a duration."""

    async def _spin():
        while not condition():
            await asyncio.sleep(0)

    await asyncio.wait_for(_spin(), timeout=seconds)


async def _until_ended(workers) -> None:
    """Wait, bounded, until every worker process has ended; `TimeoutError` if one is still running.

    Polled, not sampled once after `join` (2026-10-08, `required-backend` on PR #170, ubuntu): the pool's own
    management thread reaps a terminated worker too. When it wins the `waitpid`, this thread's `join` returns at
    once and `is_alive()` still answers True until that thread gets the GIL back to record the exit code - a single
    sample then calls a dead worker alive. The bound is shorter than `_HELD_PLAN_GIVES_UP_AFTER_SECONDS`, so a
    worker that was NOT terminated still fails here.
    """

    await _until(lambda: not any(worker.is_alive() for worker in workers), seconds=60.0)


@pytest.mark.asyncio
async def test_a_plan_that_is_slow_for_the_planner_does_not_hold_the_loop_and_the_answer_is_the_workers(
    client, db_session, monkeypatch, tmp_path
):
    """The dense graph above is heavy for the retired DFS and trivial for the planner (0.001 s), so the test above
    cannot tell a plan computed in the worker from one computed on the loop. Here the plan takes `_PLAN_SECONDS` in
    the worker, and the two effects are asserted, not the form:

    (a) while the plan is in flight - handed over, then computed in the worker - no loop gap overlaps that time by
        `_LOOP_DELAY_THRESHOLD_SECONDS` or more (what is not judged, and why: the comment above `_PLAN_SECONDS`);
    (b) the answer is what CAME BACK from the worker - its cycles are rotated, which no recomputation produces.

    The mechanism is asserted with timestamps: the plan was handed over inside the probe's window and ran in the
    worker for `_PLAN_SECONDS` before the answer arrived.
    """

    eq, _triangle = await _dense_graph_with_one_short_cycle(db_session)
    code = eq.code
    user = await register_and_login(client, "P035A1Slow")
    plan = await flow_planner.plan_for_equivalent(db_session, code)
    planned = {frozenset(str(edge.debt_id) for edge in cycle.edges) for cycle in plan.cycles}

    monkeypatch.setattr(runner, "_diagnostic_executor", None)  # restored at teardown; the fresh one is shut down below
    before = time.perf_counter()
    fresh = runner._default_diagnostic_executor()
    try:
        warming = fresh.submit(flow_planner.plan_clearing, [])  # the real planner: the patch below is not in place yet
        cold_start = time.perf_counter() - before
        await asyncio.wrap_future(warming)
        stamps = tmp_path / "plan.stamps"
        handed = _slow_the_planner(monkeypatch, _PLAN_SECONDS, str(stamps))
        response, hold = await _get_while_measuring_the_loop(
            client, f"/api/v1/clearing/cycles?equivalent={code}", user["headers"])
        answered = time.perf_counter()
    finally:
        fresh.shutdown(wait=True, cancel_futures=True)

    assert response.status_code == 200, response.text
    assert handed == [fresh], f"the plan was not handed to the fresh diagnostic pool: {handed}"
    plan_start, plan_end = (float(stamp) for stamp in stamps.read_text(encoding="utf-8").split())
    handed_over, zero = handed.at[0], hold.started
    nothing = (0.0, (zero, 0.0))
    inside = [(_overlap(gap, handed_over, plan_end), gap) for gap in hold.gaps]
    held, worst = max(((part, gap) for part, gap in inside if part > 0), default=nothing)
    outside, outside_gap = max(((gap[1] - part, gap) for part, gap in inside if gap[1] > part), default=nothing)
    measured = (
        f"held={held:.3f} s of the plan's time in flight (gap at {worst[0] - zero:.3f} s, {worst[1]:.3f} s long), "
        f"threshold={_LOOP_DELAY_THRESHOLD_SECONDS} s; in flight {handed_over - zero:.3f}..{plan_end - zero:.3f} s "
        f"(in the worker from {plan_start - zero:.3f} s) of a {hold.window:.3f} s window, plan={_PLAN_SECONDS} s; "
        f"NOT JUDGED: largest hold outside it {outside:.3f} s (gap at {outside_gap[0] - zero:.3f} s), creation and "
        f"first submit() of the fresh pool {cold_start:.3f} s"
    )
    _record_hold(held=held, held_gap_at=worst[0] - zero, held_gap=worst[1], threshold=_LOOP_DELAY_THRESHOLD_SECONDS,
                 handed_over=handed_over - zero, plan_start=plan_start - zero, plan_end=plan_end - zero,
                 window=hold.window, outside=outside, outside_gap_at=outside_gap[0] - zero, cold_start=cold_start,
                 plan=_PLAN_SECONDS)

    # The mechanism, on one clock: handed over under the probe, computed in the worker for the plan's time, and only
    # then answered. A clock that is not shared between the processes fails here, not silently below.
    assert zero <= handed_over <= plan_start < plan_end <= answered, measured
    assert plan_end - plan_start >= _PLAN_SECONDS > _LOOP_DELAY_THRESHOLD_SECONDS, measured
    cycles = response.json()["cycles"]
    assert _debt_id_sets(cycles) == planned and len(cycles) > 1
    canonical = [cycle for cycle in cycles if cycle[0]["debt_id"] == min(edge["debt_id"] for edge in cycle)]
    assert canonical == [], (
        f"{len(canonical)} of {len(cycles)} cycles are in the planner's canonical rotation: the answer was "
        f"recomputed, it is not the plan that came back from the worker"
    )
    assert held < _LOOP_DELAY_THRESHOLD_SECONDS, f"GET /clearing/cycles held the event loop while the plan ran: {measured}"


# ------------------------------------------------------------------ diagnostics and a clearing pass, side by side

#: The hard timeout of the simulator tick's clearing at its floor (`app/core/simulator/tick.py`,
#: `clearing_hard_timeout_sec`: `max(2 s, 4 x budget)`).
_TICK_HARD_TIMEOUT_SECONDS = 2.0
_QUEUED_DIAGNOSTICS = 8


class _Gate:
    """The barrier of `held_rotated_plan`: closed until `open()`, and `started()` once a held plan runs in a worker."""

    def __init__(self, directory) -> None:
        self._started, self._release = directory / "plan.started", directory / "plan.release"

    def started(self) -> bool:
        return self._started.exists()

    def open(self) -> None:
        self._release.touch()

    @property
    def paths(self) -> tuple[str, str]:
        return str(self._started), str(self._release)


def _hold_diagnostic_plans(monkeypatch, tmp_path) -> tuple[_Gate, _Handed]:
    """Every DIAGNOSTIC plan stays in flight in its worker until the gate is opened; a pass's plan is the unchanged
    planner. On a tree where both share one worker the pass's plan then waits behind the held one - which is the
    defect, not a flaw of the stand. Returns the gate and the pools handed a held plan, in order."""

    gate, handed = _Gate(tmp_path), _Handed()
    submit = ProcessPoolExecutor.submit

    def _holding_submit(self, fn, /, *args, **kwargs):
        if fn is not flow_planner.plan_clearing or not _is_diagnostic_call():
            return submit(self, fn, *args, **kwargs)
        handed.append(self)
        handed.futures.append(submit(self, held_rotated_plan, *gate.paths, *args, **kwargs))
        return handed.futures[-1]

    monkeypatch.setattr(ProcessPoolExecutor, "submit", _holding_submit)
    return gate, handed


def _is_diagnostic_call() -> bool:
    """Whether the current hand-over comes from the diagnostic path - by the caller's name on the stack, so the stand
    does not depend on WHICH pool diagnostics use."""

    import inspect

    return any(frame.function == "planned_cycles_for_diagnostics" for frame in inspect.stack(context=0))


async def _diagnose(factory, code: str):
    async with factory() as session:
        return await runner.planned_cycles_for_diagnostics(session, code)


async def _three_triangles(db_session, tag: str) -> list[str]:
    codes = []
    for k in range(3):
        eq, n = await _equivalent(db_session, f"{tag}{k}")
        await _ring(db_session, eq, n, "Q", 3, "7")
        codes.append(eq.code)
    return codes


async def _timed_pass(factory, code: str):
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(runner.run_clearing_pass(factory, code), timeout=_TICK_HARD_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        pytest.fail(
            f"HARD TIMEOUT - the clearing pass was cancelled: actual>{_TICK_HARD_TIMEOUT_SECONDS} s, "
            f"threshold={_TICK_HARD_TIMEOUT_SECONDS} s"
        )
    return result, time.perf_counter() - started


@MODE_B
@pytest.mark.asyncio
async def test_queued_diagnostics_do_not_delay_a_clearing_pass(db_session, monkeypatch, tmp_path):
    """Eight diagnostic requests at once while a diagnostic plan is HELD in its worker, then a clearing pass in the
    tick's form (hard timeout 2.0 s). The pass must commit its cycle as it does with no diagnostics around: a
    diagnostic plan never stands in front of a pass's plan, and the diagnostics do not queue up either - one is
    being computed, the others are refused at once.

    The overlap is a barrier, not a duration: the plan stays in flight until the test opens the gate, so the pass
    runs beside it however slow this machine is. What IS measured is the subject: the pass inside the tick's hard
    timeout, and not slower than the control pass by more than a second."""

    control_code, busy_code, diagnosed_code = await _three_triangles(db_session, "QD")
    factory = sessionmaker_of(db_session)
    # Warm planner processes: this is about the queue, not the first spawn.
    await asyncio.wrap_future(runner._default_planner_executor().submit(flow_planner.plan_clearing, []))
    assert len(await _diagnose(factory, diagnosed_code)) == 1
    control, control_seconds = await _timed_pass(factory, control_code)
    assert len(control.committed) == 1

    gate, handed = _hold_diagnostic_plans(monkeypatch, tmp_path)
    diagnostics = [asyncio.create_task(_diagnose(factory, diagnosed_code)) for _ in range(_QUEUED_DIAGNOSTICS)]
    await _until(lambda: len(handed) + sum(task.done() for task in diagnostics) >= _QUEUED_DIAGNOSTICS)
    await _until(gate.started, seconds=60.0)
    result, seconds = await _timed_pass(factory, busy_code)
    in_flight = [task for task in diagnostics if not task.done()]
    gate.open()
    outcomes = await asyncio.gather(*diagnostics, return_exceptions=True)

    assert len(result.committed) == 1
    assert seconds < control_seconds + 1.0, (
        f"the pass beside {_QUEUED_DIAGNOSTICS} diagnostics took actual={seconds:.3f} s, "
        f"control={control_seconds:.3f} s, threshold=control+1.0 s"
    )
    answered = [o for o in outcomes if isinstance(o, list)]
    refused = [o for o in outcomes if isinstance(o, runner.ClearingDiagnosticsUnavailable)]
    assert (len(in_flight), len(handed)) == (1, 1), "one diagnostic plan was in flight while the pass ran"
    assert (len(answered), len(refused)) == (1, _QUEUED_DIAGNOSTICS - 1), outcomes
    assert {r.details["reason"] for r in refused} == {"diagnostics_busy"}, refused


@MODE_B
@pytest.mark.asyncio
async def test_a_second_diagnostic_request_is_refused_while_one_plan_is_in_flight(
    client, db_session, monkeypatch, tmp_path
):
    """One diagnostic plan a process. A request that arrives while it is computed is answered - 503 (E007,
    `details.reason = diagnostics_busy`) with a request id, not queued and not an empty list; when the plan is done
    the next request answers. The first plan is held behind a gate, so "while it is computed" does not depend on
    how long authentication or anything else on the HTTP path takes."""

    code = (await _three_triangles(db_session, "QB"))[0]
    factory = sessionmaker_of(db_session)
    user = await register_and_login(client, "P035A1Busy")
    url = f"/api/v1/clearing/cycles?equivalent={code}"
    gate, handed = _hold_diagnostic_plans(monkeypatch, tmp_path)

    first = asyncio.create_task(_diagnose(factory, code))
    await _until(lambda: handed)
    busy = await client.get(url, headers=user["headers"])
    assert not first.done(), "stand: the held plan ended while the gate was closed"
    assert busy.status_code == 503, busy.text
    error = busy.json()["error"]
    assert (error["code"], error["details"]["reason"]) == ("E007", "diagnostics_busy") and error["request_id"], error
    assert error["details"]["retry_after_seconds"] > 0 and "cycles" not in busy.json()
    assert len(handed) == 1, "the refused request was handed to a worker (queued), not refused"

    gate.open()
    assert len(await first) == 1
    after = await client.get(url, headers=user["headers"])
    assert after.status_code == 200 and len(after.json()["cycles"]) == 1, after.text


@MODE_B
@pytest.mark.asyncio
async def test_a_pass_beside_a_hung_diagnostic_plan_is_not_delayed(db_session, monkeypatch, tmp_path):
    """A clearing pass that runs WHILE a diagnostic plan hangs in its worker takes what it takes with no diagnostics
    around. The plan hangs behind a gate that is never opened and the time bound is out of reach, so the overlap
    holds for as long as the pass needs; the pass's own time is the subject and is measured against the control."""

    control_code, busy_code, diagnosed_code = await _three_triangles(db_session, "QP")
    factory = sessionmaker_of(db_session)
    await asyncio.wrap_future(runner._default_planner_executor().submit(flow_planner.plan_clearing, []))
    assert len(await _diagnose(factory, diagnosed_code)) == 1
    control, control_seconds = await _timed_pass(factory, control_code)
    assert len(control.committed) == 1

    gate, handed = _hold_diagnostic_plans(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "DIAGNOSTIC_PLAN_TIMEOUT_SECONDS", 600.0)
    hung = asyncio.create_task(_diagnose(factory, diagnosed_code))
    await _until(gate.started, seconds=60.0)
    try:
        result, seconds = await _timed_pass(factory, busy_code)
        assert not hung.done(), "stand: the held plan ended while the gate was closed"
    finally:
        hung.cancel()  # the worker of the abandoned plan is terminated with it (the cancellation test below)
        with pytest.raises(asyncio.CancelledError):
            await hung
    assert len(result.committed) == 1 and len(handed) == 1
    assert seconds < control_seconds + 1.0, (
        f"the pass beside a hung diagnostic plan took actual={seconds:.3f} s, control={control_seconds:.3f} s, "
        f"threshold=control+1.0 s"
    )


@MODE_B
@pytest.mark.asyncio
async def test_a_diagnostic_plan_past_its_time_is_refused_and_its_worker_replaced(
    client, db_session, monkeypatch, tmp_path
):
    """The planning phase of a diagnostic request has an upper bound of time. Past it the request is answered 503
    (E007, `details.reason = diagnostics_timeout`), the worker that is still computing is terminated and a new one
    serves the next request - an abandoned plan cannot hold the slot.

    Nothing overlaps here: the plan hangs behind a closed gate, so the bound is the only thing that can end the
    request, however long that takes on this machine."""

    code = (await _three_triangles(db_session, "QT"))[0]
    factory = sessionmaker_of(db_session)
    user = await register_and_login(client, "P035A1Timeout")
    url = f"/api/v1/clearing/cycles?equivalent={code}"
    gate, handed = _hold_diagnostic_plans(monkeypatch, tmp_path)
    default_bound = runner.DIAGNOSTIC_PLAN_TIMEOUT_SECONDS
    monkeypatch.setattr(runner, "DIAGNOSTIC_PLAN_TIMEOUT_SECONDS", 1.0)

    hung = asyncio.create_task(_diagnose(factory, code))
    await _until(lambda: handed)
    hung_pool = handed[0]
    hung_workers = list(hung_pool._processes.values())
    assert hung_workers, "stand: the pool started no worker at hand-over"
    with pytest.raises(runner.ClearingDiagnosticsUnavailable) as refused:
        await hung
    assert refused.value.details["reason"] == "diagnostics_timeout"
    assert runner._diagnostic_executor is not hung_pool
    await _until_ended(hung_workers)

    # The same refusal over HTTP (a new worker, the plan hangs again), then the gate opens: 200 from a new worker.
    timed_out = await client.get(url, headers=user["headers"])
    assert timed_out.status_code == 503, timed_out.text
    error = timed_out.json()["error"]
    assert (error["code"], error["details"]["reason"]) == ("E007", "diagnostics_timeout") and error["request_id"]
    gate.open()
    monkeypatch.setattr(runner, "DIAGNOSTIC_PLAN_TIMEOUT_SECONDS", default_bound)  # the new worker starts cold
    recovered = await client.get(url, headers=user["headers"])
    assert recovered.status_code == 200 and len(recovered.json()["cycles"]) == 1, recovered.text


@MODE_B
@pytest.mark.asyncio
async def test_a_cancelled_diagnostic_request_leaves_no_plan_running_in_the_slot(db_session, monkeypatch, tmp_path):
    """A client that goes away cancels the request, not the worker's computation. The worker of the abandoned plan
    is terminated with it, so the next request is neither refused as busy nor queued behind a plan nobody awaits."""

    code = (await _three_triangles(db_session, "QC"))[0]
    factory = sessionmaker_of(db_session)
    gate, handed = _hold_diagnostic_plans(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "DIAGNOSTIC_PLAN_TIMEOUT_SECONDS", 600.0)

    request = asyncio.create_task(_diagnose(factory, code))
    # RUNNING IN THE WORKER, not merely handed over: a plan cancelled while still queued is simply dropped and needs
    # no termination.
    await _until(gate.started, seconds=60.0)
    abandoned_workers = list(handed[0]._processes.values())
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert abandoned_workers, "stand: the pool started no worker"
    await _until_ended(abandoned_workers)
    assert runner._diagnostic_executor is not handed[0]

    gate.open()
    assert len(await _diagnose(factory, code)) == 1 and len(handed) == 2


# ---------------------------------------------------------------- the plan is offered where the pass would refuse


@MODE_B
@pytest.mark.asyncio
async def test_a_stopped_equivalent_and_disabled_clearing_still_show_the_plan_the_pass_refuses(
    client, db_session, monkeypatch
):
    """Recorded behaviour, not a promise that the pass would run: `/cycles` is the plan a pass WOULD COMPUTE on this
    snapshot. The pass itself can refuse - the equivalent is stopped, or clearing is switched off - and `/cycles`
    still shows the plan rather than hiding real cycles under an empty list."""

    code = (await _three_triangles(db_session, "QS"))[0]
    user = await register_and_login(client, "P035A1Stopped")
    cycles_url, auto_url = f"/api/v1/clearing/cycles?equivalent={code}", f"/api/v1/clearing/auto?equivalent={code}"

    monkeypatch.setattr(settings, "CLEARING_ENABLED", False)
    shown = await client.get(cycles_url, headers=user["headers"])
    refused = await client.post(auto_url, headers=user["headers"])
    assert shown.status_code == 200 and len(shown.json()["cycles"]) == 1, shown.text
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (409, "clearing_disabled"), refused.text
    monkeypatch.setattr(settings, "CLEARING_ENABLED", True)

    await db_session.execute(update(Equivalent).where(Equivalent.code == code).values(is_active=False))
    await db_session.commit()
    shown = await client.get(cycles_url, headers=user["headers"])
    refused = await client.post(auto_url, headers=user["headers"])
    assert shown.status_code == 200 and len(shown.json()["cycles"]) == 1, shown.text
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "E008"), refused.text
    assert refused.json()["error"]["details"]["reason"] == "equivalent_inactive", refused.text


@pytest.mark.asyncio
async def test_an_equivalent_without_cycles_answers_an_empty_list_and_max_depth_is_refused(client, db_session):
    """No debts at all, and debts that close no cycle: 200 with `cycles: []`, not an error. `max_depth` in any form is
    422 (E009) as on `POST /clearing/auto` - an old client is told, not silently answered with something else."""

    empty, _ = await _equivalent(db_session, "CE")
    chain, n = await _equivalent(db_session, "CH")
    people = _people("H", n, 3)
    db_session.add_all(people)
    await db_session.flush()
    await _add_debts(db_session, chain, [(people[0], people[1]), (people[1], people[2])], "5")
    empty_code, chain_code = empty.code, chain.code
    user = await register_and_login(client, "P035A1Empty")

    for code in (empty_code, chain_code):
        response = await client.get(f"/api/v1/clearing/cycles?equivalent={code}", headers=user["headers"])
        assert (response.status_code, response.json()) == (200, {"cycles": []}), (code, response.text)

    for query in ("max_depth=3", "max_depth=", "max_depth", "max_depth=99", "max_depth=3&max_depth=4"):
        refused = await client.get(f"/api/v1/clearing/cycles?equivalent={chain_code}&{query}", headers=user["headers"])
        error = refused.json().get("error") or {}
        assert (refused.status_code, error.get("code")) == (422, "E009"), (query, refused.text)
        assert any("max_depth" in str(e.get("loc")) for e in error["details"]["errors"]), (query, refused.text)


class _DeadAtSubmission:
    def submit(self, fn, /, *args, **kwargs):
        raise BrokenProcessPool("p035-a1: the planner process is gone")


class _FailsWith:
    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    def submit(self, fn, /, *args, **kwargs):
        done: Future = Future()
        done.set_exception(self._failure)
        return done


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pool,discarded",
    [
        (_DeadAtSubmission(), True),
        (_FailsWith(BrokenProcessPool("p035-a1: the planner process died while planning")), True),
        (_FailsWith(flow_planner.PlanIntegrityError("p035-a1: the plan failed its own check")), False),
    ],
    ids=["pool broken at submission", "worker died while planning", "plan integrity error"],
)
async def test_a_planner_that_cannot_answer_is_an_error_with_a_request_id_not_an_empty_list(
    client, db_session, pool, discarded
):
    """The semantics of `runner._plan_off_the_loop`, on the diagnostic pool: a broken pool is discarded and the failure
    is loud; there is no fallback detector. The next request gets a new pool and answers."""

    eq, n = await _equivalent(db_session, "CF")
    await _ring(db_session, eq, n, "F", 3, "7")
    code = eq.code
    user = await register_and_login(client, "P035A1Fail")
    url = f"/api/v1/clearing/cycles?equivalent={code}"

    real = runner._diagnostic_executor
    runner._diagnostic_executor = pool
    try:
        failed = await client.get(url, headers=user["headers"])
        left = runner._diagnostic_executor
    finally:
        runner._diagnostic_executor = real
    assert failed.status_code == 500, failed.text
    error = failed.json()["error"]
    assert error["code"] == "E010" and error["request_id"], failed.text
    assert "cycles" not in failed.json()
    assert (left is None) is discarded, f"the pool after the failure: {left!r}"

    recovered = await client.get(url, headers=user["headers"])
    assert recovered.status_code == 200 and len(recovered.json()["cycles"]) == 1, recovered.text
