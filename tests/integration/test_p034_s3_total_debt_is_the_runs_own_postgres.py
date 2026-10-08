"""034 S3 (F-034-14, `total_debt`): a run's `total_debt` metric counts the debts of the run, not of the equivalent.

Everything else a run does stays inside its perimeter - the run's own participant list: the tick's clearing passes
it to every occurrence, the interactive actions resolve participants within it. The `total_debt` metric alone summed
`debts.amount` over the whole equivalent, so two runs of different scenarios in one equivalent (or a run next to a
seeded community in the same database) each reported the other's debts as their own - debts the run cannot pay,
clear or show.

The stand is 023 (d)'s: two triangles in one equivalent, the run's participants are those of the first only. The
metric is read through the tick's own `RealTick.populate_per_eq_metric_values`.

A debt with only ONE end in the run is outside too (both directions are seeded): the run's clearing and payments
cannot reach it. Not checked: how the metric is stored or drawn.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.integration.test_p023_d_tick_driver_through_runner_postgres import (  # noqa: F401 - `factory` is a fixture
    CODE,
    T1,
    T2,
    _Stand,
    factory,
)
from tests.p020_support import Edge, debt_uuid, seed_graph
from tests.p023_support import positive_debt_total


#: Debts across the run's edge, one in each direction: a run participant owes an outsider, and the reverse.
ACROSS = [Edge(debt_uuid(0x34D3, 1), T1[0].debtor, T2[0].debtor, "5"),
          Edge(debt_uuid(0x34D3, 2), T2[1].debtor, T1[1].debtor, "7")]


async def _total_debt_metric(stand: _Stand) -> Decimal:
    values: dict = {CODE: {}}
    stand.runner._tick._real_db_metrics_every_n_ticks = 1  # the snapshot is throttled; measure on this tick
    async with stand.factory() as session:
        await stand.runner._tick.populate_per_eq_metric_values(
            session=session, run=stand.run, scenario={"participants": []}, equivalents=[CODE], per_eq_route={},
            clearing_volume_by_eq={}, per_eq_metric_values=values)
    assert "total_debt" in values[CODE], f"the tick measured no total_debt at all: {values}"
    return values[CODE]["total_debt"]


@pytest.mark.asyncio
async def test_control_a_run_alone_in_its_equivalent_reports_all_of_it(factory) -> None:  # noqa: F811
    async with factory() as session:
        await seed_graph(session, CODE, T1, precision=2)
    stand = _Stand(factory, T1)
    assert await _total_debt_metric(stand) == Decimal("6")  # three debts of 2


@pytest.mark.asyncio
async def test_total_debt_leaves_out_the_debts_of_participants_outside_the_run(factory) -> None:  # noqa: F811
    async with factory() as session:
        await seed_graph(session, CODE, T1 + T2 + ACROSS, precision=2)
    stand = _Stand(factory, T1)  # the run's participants: the first triangle only
    # Controls: the outsiders' debts are really there, and the run's own are what the other test measures.
    async with factory() as session:
        assert await positive_debt_total(session, CODE) == Decimal("15") + 5 + 7
    own = {pid for _, pid in stand.run._real_participants}
    assert own == {e.debtor for e in T1} and not own & {e.debtor for e in T2}

    measured = await _total_debt_metric(stand)
    assert measured == Decimal("6"), (
        f"the run's total_debt is {measured}; the debts among the run's participants are 6; the other 9 are among "
        "outsiders, 5 are owed by a run participant to an outsider and 7 by an outsider to a run participant")
