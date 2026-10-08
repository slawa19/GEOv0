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
"""

from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from decimal import Decimal

import pytest

from app.core.clearing import flow_planner, runner
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_scenarios import register_and_login

_LAYERS = 12
_WIDTH = 3
_LOOP_DELAY_THRESHOLD_SECONDS = 0.25


def _people(prefix: str, n: str, size: int) -> list[Participant]:
    return [Participant(pid=f"{prefix}{i}_{n}", display_name=f"{prefix}{i}", public_key=f"pk{prefix}{i}-{n}",
                        type="person", status="active", profile={}) for i in range(size)]


async def _add_debts(db_session, eq, pairs, amount: str) -> list[Debt]:
    """Debts `debtor -> creditor`, each with its consenting line (`creditor -> debtor`)."""

    db_session.add_all([TrustLine(from_participant_id=creditor.id, to_participant_id=debtor.id, equivalent_id=eq.id,
                                  limit=Decimal("100"), status="active", policy={"auto_clearing": True})
                        for debtor, creditor in pairs])
    debts = [Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal(amount))
             for debtor, creditor in pairs]
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


async def _ring(db_session, eq, n: str, prefix: str, size: int, amount: str) -> list[Debt]:
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


async def _worst_scheduling_delay(stop: asyncio.Event) -> float:
    """The longest time one `asyncio.sleep(0)` took to come back: how long anything else on the loop waited."""

    worst = 0.0
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0)
        now = time.perf_counter()
        worst = max(worst, now - last)
        last = now
    return worst


async def _get_while_measuring_the_loop(client, url: str, headers) -> tuple[object, float]:
    stop = asyncio.Event()
    probe = asyncio.create_task(_worst_scheduling_delay(stop))
    await asyncio.sleep(0)
    try:
        response = await client.get(url, headers=headers)
    finally:
        stop.set()
        worst = await probe
    return response, worst


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
    control, control_delay = await _get_while_measuring_the_loop(
        client, f"/api/v1/clearing/cycles?equivalent={small_code}", user["headers"])
    assert control.status_code == 200 and len(control.json()["cycles"]) == 1, control.text
    assert control_delay < _LOOP_DELAY_THRESHOLD_SECONDS, f"the control request held the loop {control_delay:.3f} s"

    response, delay = await _get_while_measuring_the_loop(
        client, f"/api/v1/clearing/cycles?equivalent={dense_code}", user["headers"])
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
    """

    eq, n = await _equivalent(db_session, "CP")
    await _ring(db_session, eq, n, "R", 7, "10")
    await _ring(db_session, eq, n, "T", 3, "7")
    user = await register_and_login(client, "P035A1Plan")

    plan = await flow_planner.plan_for_equivalent(db_session, eq.code)
    planned = {frozenset(str(edge.debt_id) for edge in cycle.edges) for cycle in plan.cycles}
    assert sorted(len(c) for c in planned) == [3, 7], planned

    submitted: list[object] = []
    submit = ProcessPoolExecutor.submit

    def _recording_submit(self, fn, /, *args, **kwargs):
        submitted.append(fn)
        return submit(self, fn, *args, **kwargs)

    monkeypatch.setattr(ProcessPoolExecutor, "submit", _recording_submit)
    response = await client.get(f"/api/v1/clearing/cycles?equivalent={eq.code}", headers=user["headers"])
    assert response.status_code == 200, response.text
    offered = _debt_id_sets(response.json()["cycles"])
    assert offered == planned, (
        f"GET /clearing/cycles differs from the plan: offered cycle lengths={sorted(len(c) for c in offered)}, "
        f"planned cycle lengths={sorted(len(c) for c in planned)}"
    )
    assert flow_planner.plan_clearing in submitted, (
        f"the plan was not handed to a planner process: process-pool submissions={submitted}"
    )


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
    """The semantics of `runner._plan_off_the_loop`: a broken pool is discarded and the failure is loud; there is no
    fallback detector. The next request gets a new pool and answers."""

    eq, n = await _equivalent(db_session, "CF")
    await _ring(db_session, eq, n, "F", 3, "7")
    code = eq.code
    user = await register_and_login(client, "P035A1Fail")
    url = f"/api/v1/clearing/cycles?equivalent={code}"

    real = runner._planner_executor
    runner._planner_executor = pool
    try:
        failed = await client.get(url, headers=user["headers"])
        left = runner._planner_executor
    finally:
        runner._planner_executor = real
    assert failed.status_code == 500, failed.text
    error = failed.json()["error"]
    assert error["code"] == "E010" and error["request_id"], failed.text
    assert "cycles" not in failed.json()
    assert (left is None) is discarded, f"the pool after the failure: {left!r}"

    recovered = await client.get(url, headers=user["headers"])
    assert recovered.status_code == 200 and len(recovered.json()["cycles"]) == 1, recovered.text
