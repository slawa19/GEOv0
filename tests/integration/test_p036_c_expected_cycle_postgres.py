"""036 C fix-delta (R2): a scripted `clearing` that ANNOUNCES a cycle (`expected_cycle`) reports whether it cleared it.

Before: a complete pass that cleared nothing, or something else, was `done` with `cleared_cycles` 0 (or another cycle's) and
no `reason` - indistinguishable from success (measured on `e94f13fb`: injects off, or a periodic clearing at tick 10/20/40
taking the cycle first). Now the event is still spent and still `done` (the pass completed - that is a fact), but when the
cycles the event has committed in this launch (cumulatively) contain none over the edges of `expected_cycle`, the record
carries `reason: "expected_cycle_not_cleared"`.

* The comparison is by the SET of edges, creditor -> debtor, consecutive pairs of `expected_cycle` and the last to the first:
  a rotation of the same cycle matches; the same cycle listed debtor -> creditor does not (the direction of the report).
* The amount is not compared: `expected_cycle` has none.
* An event without `expected_cycle` is not touched (control), and a matching event carries no reason (control).

TARGETS are RED on `e94f13fb` (no reason); controls are green there.
"""

from __future__ import annotations

import pytest

from app.core.simulator.runtime_utils import run_to_status
from tests.integration.test_p036_b1_scripted_events_postgres import (  # noqa: F401 - `factory` is a fixture
    CYCLE_DEBTS,
    CYCLE_LINES,
    _clearing_event,
    _stand,
    factory,
    ticks,
)
from tests.p021_support import require_target

REASON = "expected_cycle_not_cleared"


async def _clearing(factory, monkeypatch, announce, *, debts=CYCLE_DEBTS):  # noqa: F811
    """One tick of a stand whose only event is a clearing; `announce(p)` gives its `expected_cycle` (or None for none)."""

    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, debts,
        lambda e, q: [{**_clearing_event(e), **({"expected_cycle": announce(q)} if announce(q) is not None else {})}],
    )
    await ticks(runner, run, 1)
    [item] = run_to_status(run).episode_progress or []
    return item, run


# CYCLE_LINES: B->A, C->B, A->C (creditor -> debtor); CYCLE_DEBTS: A owes B, B owes C, C owes A. The cleared edges, creditor ->
# debtor, are B->A, C->B, A->C, i.e. the cycle [B, A, C] (and its rotations [A, C, B], [C, B, A]).


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("B", "A", "C"), ("A", "C", "B"), ("C", "B", "A")], ids=["as-cleared", "rotated-1", "rotated-2"])
async def test_control_the_cycle_announced_is_the_cycle_cleared_and_no_reason_is_written(factory, monkeypatch, order) -> None:  # noqa: F811
    item, run = await _clearing(factory, monkeypatch, lambda q: [q[r].pid for r in order])

    assert (item.status, item.cleared_cycles) == ("done", 1) and 0 in run._real_fired_scenario_event_indexes  # control: cleared and spent
    assert item.reason is None, item.reason


@pytest.mark.asyncio
async def test_a_cycle_announced_over_other_edges_than_the_cleared_one_says_so(factory, monkeypatch) -> None:  # noqa: F811
    """Debtor -> creditor is the other direction: the same three people, the edges the other way round, were not cleared."""

    item, run = await _clearing(factory, monkeypatch, lambda q: [q["A"].pid, q["B"].pid, q["C"].pid])

    assert (item.status, item.cleared_cycles) == ("done", 1) and 0 in run._real_fired_scenario_event_indexes  # the pass completed and cleared its cycle
    require_target(item.reason == REASON, f"reason {item.reason!r} for a cycle announced over the edges it did not clear")


@pytest.mark.asyncio
async def test_a_complete_pass_that_cleared_nothing_says_the_announced_cycle_was_not_cleared(factory, monkeypatch) -> None:  # noqa: F811
    item, run = await _clearing(factory, monkeypatch, lambda q: [q["B"].pid, q["A"].pid, q["C"].pid], debts=[])

    assert (item.status, item.cleared_cycles) == ("done", 0) and 0 in run._real_fired_scenario_event_indexes  # control: complete, empty, spent
    require_target(item.reason == REASON, f"reason {item.reason!r} for an announced cycle of a pass that cleared nothing")


@pytest.mark.asyncio
async def test_control_a_clearing_that_announces_nothing_has_no_reason_even_when_it_clears_nothing(factory, monkeypatch) -> None:  # noqa: F811
    item, run = await _clearing(factory, monkeypatch, lambda q: None, debts=[])

    assert (item.status, item.cleared_cycles, item.reason) == ("done", 0, None)


@pytest.mark.asyncio
async def test_the_cycle_cleared_by_an_earlier_attempt_of_the_same_event_counts(factory, monkeypatch) -> None:  # noqa: F811
    """Cumulative within the launch: an attempt that committed the announced cycle and then failed leaves the event pending;
    the attempt that completes it (an empty pass) must not report the cycle as not cleared."""

    import app.core.clearing.runner as clearing_runner

    eq, p, run, runner = await _stand(
        factory, monkeypatch, ["A", "B", "C"], CYCLE_LINES, CYCLE_DEBTS,
        lambda e, q: [{**_clearing_event(e), "expected_cycle": [q["B"].pid, q["A"].pid, q["C"].pid]}],
    )
    original = clearing_runner.run_clearing_pass
    calls = {"n": 0}

    async def commits_then_fails(*args, **kwargs):
        calls["n"] += 1
        result = await original(*args, **kwargs)
        if calls["n"] == 1:
            raise RuntimeError("p036 c fix-delta: the pass fails after it committed the cycle")
        return result

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", commits_then_fails)

    await ticks(runner, run, 1)
    [first] = run_to_status(run).episode_progress or []
    run.state, run.errors_total, run.last_error = "running", 0, None
    await ticks(runner, run, 1)
    [last] = run_to_status(run).episode_progress or []

    assert (first.status, first.cleared_cycles) == ("incomplete", 1)  # control: the cycle was committed, the event waits
    assert (last.status, last.cleared_cycles, last.reason) == ("done", 1, None), last
