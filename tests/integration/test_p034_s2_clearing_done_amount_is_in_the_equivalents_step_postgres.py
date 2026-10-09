"""034 S2 (F-034-3): `clearing.done.cleared_amount` is written in the equivalent's step on the tick's path too.

Two orchestrations publish `clearing.done` around the same runner: the tick (`app/core/simulator/tick.py`,
`RealTick._run_clearing`) and the interactive action (`app/api/v1/simulator.py`, `action_clearing_real`). They take
the scale of `cleared_amount` from different places: the action from the equivalent's row (`eq.precision`), the tick
from the `VizPatchHelper` cached on the run - and, when the run has no helper for the equivalent yet, from the
constant 2 (`RealTick._cleared_amount_str`, before 034 S2b). On the tick's ordinary ending the helper was created just before the
event; on a CANCELLED pass that already committed (the hard timeout) the event goes out without patches and so without
a helper. In an equivalent whose precision is not 2 the tick then writes the amount in a step that is not the
equivalent's: `"2.00"` for a whole-unit equivalent, where the action writes `"2"`.

The contract (`docs/ru/04-api-reference.md`, "Денежные суммы"): a state amount, the clearing total of a simulator
event included, is written in the step of its equivalent.

The stand is 023 (d)'s cancellation stand (`test_a_tick_cancelled_after_a_commit_keeps_its_progress`): the first
occurrence commits, the second is held past the hard timeout. Only the equivalent's precision differs.
Not checked here: the action's own event (it formats with `eq.precision`, read at `simulator.py`, `action_clearing_real`).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.core.simulator.net_balance_utils import to_money_str
from tests.integration.test_p023_d_tick_driver_through_runner_postgres import (  # noqa: F401 - `factory` is a fixture
    CODE,
    T1,
    T2,
    _budget_does_not_bind,
    _spy_execute,
    _Stand,
    factory,
)
from tests.p020_support import debt_uuid, ring, seed_graph


#: Two triangles whose amounts are finer than hundredths (for an equivalent of precision 4).
FINE1 = ring(["p034fa", "p034fb", "p034fc"], ["0.0050"] * 3, [debt_uuid(0x34F3, k) for k in range(3)])
FINE2 = ring(["p034fd", "p034fe", "p034ff"], ["0.0075"] * 3, [debt_uuid(0x34F3, 10 + k) for k in range(3)])


async def _cancelled_after_one_commit(factory, monkeypatch, *, precision: int,  # noqa: F811
                                      edges=None, amounts=(Decimal("2"), Decimal("3"))) -> tuple[dict, Decimal]:
    """One tick whose pass is cut by the hard timeout after its first occurrence; (`clearing.done`, the amount)."""
    edges = T1 + T2 if edges is None else edges
    async with factory() as session:
        await seed_graph(session, CODE, edges, precision=precision)
    stand = _Stand(factory, edges)
    before_total = await stand.total()
    hard_timeout = _budget_does_not_bind(monkeypatch, stand)

    async def before(n: int) -> None:
        if n == 2:
            await asyncio.sleep(hard_timeout + 5.0)

    calls = _spy_execute(monkeypatch, before)
    # Control: the run enters the tick with no viz helper for the equivalent - the state in which the tick had no
    # precision of its own and wrote two digits.
    assert stand.run._real_viz_by_eq.get(CODE) is None
    await stand.tick()
    # Controls: the second occurrence started and was cut, the first is durable and published once, without patches.
    assert len(calls) == 2 and await stand.clearings() == 1, (len(calls), await stand.clearings())
    [done] = stand.done_events()
    assert done["node_patch"] is None and done["edge_patch"] is None, done
    amount = (before_total - await stand.total()) / 3
    assert amount in amounts, amount
    return done, amount


@pytest.mark.asyncio
async def test_control_in_a_two_digit_equivalent_the_cancelled_tick_writes_the_step(factory, monkeypatch) -> None:  # noqa: F811
    done, amount = await _cancelled_after_one_commit(factory, monkeypatch, precision=2)
    assert done["cleared_amount"] == to_money_str(amount, 2) and done["cleared_amount"] in ("2.00", "3.00"), done


@pytest.mark.parametrize("precision", [0, 4])
@pytest.mark.asyncio
async def test_a_cancelled_tick_writes_the_cleared_amount_in_the_equivalents_step(
    factory, monkeypatch, precision  # noqa: F811
) -> None:
    done, amount = await _cancelled_after_one_commit(factory, monkeypatch, precision=precision)
    expected = to_money_str(amount, precision)  # what the interactive action writes for the same equivalent
    assert done["cleared_amount"] == expected, (
        f"equivalent precision {precision}: the tick's clearing.done says cleared_amount {done['cleared_amount']!r}, "
        f"the equivalent's step is {expected!r}")


@pytest.mark.asyncio
async def test_an_amount_finer_than_hundredths_keeps_its_value_and_the_step(factory, monkeypatch) -> None:  # noqa: F811
    """Precision 4 and amounts of 0.0050 / 0.0075. `to_money_str` never erases a digit the value needs, so the old
    two-digit rendering kept the VALUE (`"0.005"`) and lost only the step (`"0.0050"`); both are held here."""
    done, amount = await _cancelled_after_one_commit(
        factory, monkeypatch, precision=4, edges=FINE1 + FINE2, amounts=(Decimal("0.0050"), Decimal("0.0075")))
    assert Decimal(done["cleared_amount"]) == amount, (done["cleared_amount"], amount)
    assert done["cleared_amount"] == to_money_str(amount, 4) and done["cleared_amount"] in ("0.0050", "0.0075"), done
