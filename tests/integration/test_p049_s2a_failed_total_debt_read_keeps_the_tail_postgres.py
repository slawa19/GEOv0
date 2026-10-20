"""049 S2a (`T4915`): a failed `total_debt` read does not poison the tick's tail transaction.

WHAT WAS WRONG. `RealTick.populate_per_eq_metric_values` reads the run's total debt on the TAIL session and, on failure,
logs and swallows the exception WITHOUT a rollback (`tick.py`, the "total debt snapshot"); so does the edge-patch read
after the trust decay (`apply_trust_decay_and_broadcast`). On PostgreSQL the transaction is then aborted, and every
later statement of the tail on that session fails with `InFailedSQLTransactionError: current transaction is aborted`.
MEASURED on this stand before the fix (2026-10-20): the metrics write failed on every such tick (the real writer
swallows it into False, so the tick is never marked flushed); the tick's own COMMIT on the aborted transaction did not
raise here, so the run went on and the tick-failure counter did not climb - the loss is the tail's writes, silently.
The class AGENTS §9 names ("a swallowed 40001 poisons the transaction").

THE FIX. The read runs in a SAVEPOINT: a failed read rolls back only itself, the tail goes on in a usable transaction,
`total_debt` is simply not measured this tick (the existing "not a measurement - persist nothing").

THE FAILURE IS REAL: the read's SUM is replaced by `sqrt(-1)`, which PostgreSQL refuses server-side ("cannot take
square root of a negative number"), aborting the transaction exactly as a real failing read would - no synthetic
DBAPI injection. The stand is a REAL tick (`RealRunnerImpl.tick_real_mode`) on a mode-B clone; the metrics writer is a
spy that runs one statement on the tail session the way the real writer does (inside its own savepoint).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
import sqlalchemy as sa

import app.core.simulator.storage as simulator_storage
import app.core.simulator.tick as tick_module
from tests.integration.test_p021_trust_drift_is_audited_postgres import (  # noqa: F401 - `factory` is a fixture
    factory,
    run_for,
    runner_for,
    scenario_for,
    ticks,
    world,
)
from tests.simulator_tick_stand import install_tick_stand

LINES = [("C", "D", "100.00", "active")]
DEBTS = [("D", "C", "40.00")]


class _FuncWithARefusedSum:
    """`sqlalchemy.func` as `tick.py` sees it, except that the total-debt SUM becomes `sqrt(-1)`."""

    def __init__(self) -> None:
        self.refused = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(sa.func, name)

    def coalesce(self, *_args: Any, **_kwargs: Any) -> Any:
        self.refused += 1
        return sa.func.sqrt(sa.literal(-1.0))


class _MetricsSpy:
    """Stands for `write_tick_metrics(session=<tail session>, commit=False)`: one statement on the tail session in a
    savepoint (as the real writer does), recording whether the transaction could still take it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, session, metric_values_by_eq=None, **_kwargs: Any) -> bool:
        try:
            async with session.begin_nested():
                await session.execute(sa.text("SELECT 1"))
            ok = True
        except Exception as exc:  # what the real writer swallows into `False`
            ok = False
            cause = getattr(exc, "orig", None) or exc
            self.calls.append({"ok": ok, "error": f"{type(cause).__name__}: {str(cause)[:90]}", "values": metric_values_by_eq})
            return False
        self.calls.append({"ok": ok, "error": None, "values": metric_values_by_eq})
        return True


async def _stand(factory, monkeypatch, *, refuse: bool):  # noqa: F811
    eq, people = await world(factory, ["C", "D"], LINES, DEBTS)
    scenario = scenario_for(eq, people, LINES, {"enabled": False})
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=10_000, logger=logging.getLogger("tests.p049_s2a.total_debt"))
    runner._tick._real_db_metrics_every_n_ticks = 1  # the read and the write on every tick
    install_tick_stand(monkeypatch, factory)
    spy = _MetricsSpy()
    monkeypatch.setattr(simulator_storage, "write_tick_metrics", spy)
    shim = _FuncWithARefusedSum()
    if refuse:
        monkeypatch.setattr(tick_module, "func", shim)
    return eq, run, spy, shim, runner


@pytest.mark.asyncio
async def test_a_refused_total_debt_read_leaves_the_tail_usable_and_the_tick_successful(factory, monkeypatch) -> None:  # noqa: F811
    eq, run, spy, shim, runner = await _stand(factory, monkeypatch, refuse=True)
    run._real_consec_tick_failures = 2  # a run two failures from the stop: a successful tick must clear it

    await ticks(runner, run, 3)

    assert shim.refused == 3, f"premise: the read was refused by PostgreSQL on every tick ({shim.refused})"
    assert [c["ok"] for c in spy.calls] == [True, True, True], f"the tail after the refused read: {spy.calls}"
    assert all("total_debt" not in (c["values"] or {}).get(eq.code, {}) for c in spy.calls), spy.calls
    assert run.state == "running" and run.last_error is None, (run.state, run.last_error)
    assert run._real_consec_tick_failures == 0


@pytest.mark.asyncio
async def test_control_an_accepted_read_is_measured(factory, monkeypatch) -> None:  # noqa: F811
    """Anti-vacuum: the same stand with the real SUM measures `total_debt` (40.00) - the spy sees the values."""

    eq, run, spy, shim, runner = await _stand(factory, monkeypatch, refuse=False)

    await ticks(runner, run, 1)

    assert shim.refused == 0 and [c["ok"] for c in spy.calls] == [True], spy.calls
    assert str(spy.calls[0]["values"][eq.code]["total_debt"]) in {"40", "40.00", "40.00000000"}, spy.calls


@pytest.mark.asyncio
async def test_a_refused_decay_edge_patch_read_leaves_the_tail_usable(factory, monkeypatch) -> None:  # noqa: F811
    """The sibling on the same session: the edge patch after the trust decay is read on the tail session and its
    failure swallowed (`apply_trust_decay_and_broadcast`); a refused read must not abort the tail either."""

    lines = [("C", "D", "100.00", "active")]
    eq, people = await world(factory, ["C", "D"], lines, [("D", "C", "90.00")])  # ratio 0.9 > 0.8: the line decays
    scenario = scenario_for(
        eq, people, lines,
        {"enabled": True, "decay_rate": 0.02, "min_limit_ratio": 0.3, "overload_threshold": 0.8, "growth_rate": 0.05},
    )
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=10_000, logger=logging.getLogger("tests.p049_s2a.edge_patch"))
    runner._tick._real_db_metrics_every_n_ticks = 1
    install_tick_stand(monkeypatch, factory)
    spy = _MetricsSpy()
    monkeypatch.setattr(simulator_storage, "write_tick_metrics", spy)
    refused: list[int] = []

    async def _refused_patch(*, session, **_kwargs: Any) -> list:
        refused.append(1)
        await session.execute(sa.select(sa.func.sqrt(sa.literal(-1.0))))
        return []

    monkeypatch.setattr(runner._edge_patch_builder, "build_edge_patch_for_equivalent", _refused_patch)

    await ticks(runner, run, 1)

    assert refused == [1], f"premise: the decay touched the line and its patch was read ({refused})"
    assert [c["ok"] for c in spy.calls] == [True], f"the tail after the refused patch read: {spy.calls}"
    assert run.state == "running" and run.last_error is None, (run.state, run.last_error)
