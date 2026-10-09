"""034 S2b fix-delta (review of `ed3271fe`, P2): the tick reads the equivalent's precision before each pass.

`clearing.done.cleared_amount` is written in the equivalent's step. The first S2b fix took that step from the
`VizPatchHelper` cached on the run. The cache is not a source of the step: an operator may raise an equivalent's
precision while debts exist (`app/core/equivalents.py`, `update_equivalent`; the admin `PATCH`), nothing in the
simulator drops the run's helpers when that happens, and from then on the tick wrote `"2.00"` in an equivalent whose
step is `"2.0000"` - on the ordinary ending too. It also made clearing depend on visualisation: with no helper to
create, the pass did not start.

Now: `Equivalent.precision` is read on a short session of its own right before the pass and held as an integer for
every ending (the snapshot the interactive action takes as `eq_precision`); the helper and the patches are best
effort AFTER the clearing, as before S2b.

THE STAND is 023 (d)'s tick stand on real PostgreSQL. The precision is changed through `update_equivalent`, the
function behind the admin `PATCH`. Not checked: the admin route itself, and a precision changed DURING a pass (the
pass keeps the snapshot it started with, as the interactive action does).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.core.equivalents import update_equivalent
from app.core.simulator.viz_patch_helper import VizPatchHelper
from tests.integration.test_p023_d_tick_driver_through_runner_postgres import (  # noqa: F401 - `factory` is a fixture
    CODE,
    T1,
    T2,
    _budget_does_not_bind,
    _runner_module,
    _spy_execute,
    _Stand,
    factory,
)
from tests.p020_support import seed_graph


async def _stand_with_a_helper_of_the_old_precision(factory, edges) -> _Stand:  # noqa: F811
    """The equivalent starts at precision 2, the run caches its viz helper, then the operator raises it to 4."""
    async with factory() as session:
        await seed_graph(session, CODE, edges, precision=2)
    stand = _Stand(factory, edges)
    async with factory() as session:
        stand.run._real_viz_by_eq[CODE] = await VizPatchHelper.create(session, equivalent_code=CODE)
    assert stand.run._real_viz_by_eq[CODE].precision == 2
    async with factory() as session:
        eq, before, after = await update_equivalent(session, CODE, precision=4)
        await session.commit()
    assert (before["precision"], after["precision"]) == (2, 4), (before, after)
    return stand


@pytest.mark.asyncio
async def test_after_the_operator_raises_the_precision_an_ordinary_tick_writes_the_new_step(factory) -> None:  # noqa: F811
    stand = await _stand_with_a_helper_of_the_old_precision(factory, T1)
    runner = _runner_module()
    await asyncio.wrap_future(runner._default_planner_executor().submit(runner.plan_clearing, []))  # a warm planner
    for tick_index in range(1, 6):  # the hard timeout may cut a tick under load; the ordinary ending is the subject
        stand.run.tick_index = tick_index
        await stand.tick()
        if await stand.total() == 0:
            break
    assert await stand.total() == 0, "the triangle was not cleared: the stand did not reach the ordinary ending"
    done = [d for d in stand.done_events() if d.get("node_patch") is not None or d.get("edge_patch") is not None]
    assert done, f"no clearing.done of an ordinary ending (with patches): {stand.done_events()}"
    assert done[-1]["cleared_amount"] == "2.0000", (
        f"the equivalent's precision is 4 since the operator's change; the tick's clearing.done says "
        f"cleared_amount {done[-1]['cleared_amount']!r}, the step is '2.0000'")


@pytest.mark.asyncio
async def test_after_the_operator_raises_the_precision_a_cancelled_tick_writes_the_new_step(
    factory, monkeypatch  # noqa: F811
) -> None:
    stand = await _stand_with_a_helper_of_the_old_precision(factory, T1 + T2)
    hard_timeout = _budget_does_not_bind(monkeypatch, stand)

    async def before(n: int) -> None:
        if n == 2:
            await asyncio.sleep(hard_timeout + 5.0)

    calls = _spy_execute(monkeypatch, before)
    await stand.tick()
    assert len(calls) == 2 and await stand.clearings() == 1, (len(calls), await stand.clearings())
    [done] = stand.done_events()
    amount = (Decimal("15") - await stand.total()) / 3
    assert amount in (Decimal("2"), Decimal("3")), amount
    assert done["cleared_amount"] == f"{int(amount)}.0000", (
        f"precision 4 since the operator's change; the cancelled tick says cleared_amount "
        f"{done['cleared_amount']!r} for {amount}")


@pytest.mark.asyncio
async def test_clearing_does_not_depend_on_the_viz_helper(factory, monkeypatch) -> None:  # noqa: F811
    """The helper cannot be created: the clearing still commits and says so, in the right step, without patches."""
    async with factory() as session:
        await seed_graph(session, CODE, T1, precision=0)
    stand = _Stand(factory, T1)

    async def _no_helper(*_args, **_kwargs):
        raise RuntimeError("the viz helper of this stand cannot be created")

    monkeypatch.setattr(VizPatchHelper, "create", _no_helper)
    runner = _runner_module()
    await asyncio.wrap_future(runner._default_planner_executor().submit(runner.plan_clearing, []))
    for tick_index in range(1, 6):
        stand.run.tick_index = tick_index
        await stand.tick()
        if await stand.total() == 0:
            break
    assert await stand.total() == 0 and await stand.clearings() == 1, (
        f"without a viz helper the tick cleared nothing: {await stand.total()} left, run error {stand.run.last_error}")
    [done] = stand.done_events()
    assert (done["cleared_amount"], done["node_patch"], done["edge_patch"]) == ("2", None, None), done
