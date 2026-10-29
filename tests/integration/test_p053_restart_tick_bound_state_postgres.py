"""053: stopped restart keeps committed money and refreshes the new launch's viz.

Real PostgreSQL, payment planner/executor, tick and public runtime restart. Only
heartbeat scheduling is held; ticks advance explicitly without wall-clock waits.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.simulator.models import EdgeClearingHistory, TrustDriftConfig
from app.core.simulator.runtime import SimulatorRuntime
from app.db.models.trustline import TrustLine
from tests.integration.test_p015_p1_money_replay_postgres import (
    _debts,
    _forget_the_route_cache,
    _install,
    _run_record,
    _scenario,
    _seed,
    factory as factory,
)
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: F401


async def _world(factory, monkeypatch):  # noqa: F811 - imported pytest fixture
    world = await _seed(factory)
    _install(monkeypatch, factory)
    monkeypatch.setattr("app.config.settings.SIMULATOR_DB_ENABLED", True)
    runtime = SimulatorRuntime()
    run = _run_record(world, f"p053-{world.equivalent.code}")
    run.intensity_percent = 5
    run.owner_id = f"anon:{run.run_id}"
    scenario = _scenario(world)
    scenario["behaviorProfiles"] = [
        {
            "id": "payer",
            "props": {
                "amount_model": {
                    world.equivalent.code: {"min": "20.00", "max": "20.00"}
                }
            },
        }
    ]
    for participant in scenario["participants"]:
        participant["behaviorProfileId"] = "payer"
    run._scenario_raw = scenario
    runtime._runs[run.run_id] = run
    # Restart creates a real heartbeat task; stop cancels/awaits it. Hold its
    # scheduler so explicit real ticks cannot race a wall-clock iteration.
    held = asyncio.Event()

    async def heartbeat(_run_id):
        await held.wait()

    monkeypatch.setattr(runtime._run_lifecycle, "_heartbeat_loop", heartbeat)
    return world, runtime, run


async def _tick(runtime, run, index):
    run.tick_index = index
    run.sim_time_ms = index * 1000
    await runtime._real_runner.tick_real_mode(run.run_id)
    assert run.state == "running" and run.last_error is None, run.last_error


async def test_stopped_public_restart_refreshes_real_quantiles_and_keeps_debts(
    factory, monkeypatch  # noqa: F811 - imported pytest fixture
):
    world, runtime, run = await _world(factory, monkeypatch)
    eq = world.equivalent.code
    try:
        await _tick(runtime, run, 91)
        old = run._real_viz_by_eq[eq]
        assert old._last_refresh_tick == 91
        assert old._mags_sorted == [12000, 12000]
        assert old.refresh_every_ticks == 10

        await _tick(runtime, run, 92)
        assert old._mags_sorted == [
            12000,
            12000,
        ], "normal cache hit must retain the ten-tick cadence"
        await _tick(runtime, run, 101)
        assert old._mags_sorted == [16000, 16000] and old._last_refresh_tick == 101
        await runtime.stop(run.run_id)
        before = await _debts(factory, world)
        assert list(before.values()) == [Decimal("160.00")]
        assert run.committed_total == 3
        await runtime.restart(run.run_id)
        assert (
            await _debts(factory, world) == before
        ), "restart must preserve committed debts"
        assert (
            run.committed_total == 3
        ), "restart must preserve the committed payment history"

        await _tick(runtime, run, 1)
        fresh = run._real_viz_by_eq[eq]
        assert fresh._mags_sorted == [18000, 18000], (
            f"first restarted tick quantiles actual={fresh._mags_sorted}, expected=[18000, 18000]; "
            f"last refresh tick={fresh._last_refresh_tick}"
        )
        assert fresh is not old and fresh._last_refresh_tick == 1
        assert list((await _debts(factory, world)).values()) == [Decimal("180.00")]
        assert run.committed_total == 4
    finally:
        await runtime.stop(run.run_id)
        _forget_the_route_cache(world)


@pytest.mark.parametrize("decay,expected", [(0, "1000.00"), (0.1, "900.00")])
async def test_restart_keeps_clearing_history_but_not_old_tick_decay_exemption(
    factory, monkeypatch, decay, expected  # noqa: F811 - imported pytest fixture
):
    world, runtime, run = await _world(factory, monkeypatch)
    run.intensity_percent = 0
    run._trust_drift_config = TrustDriftConfig(
        enabled=True, decay_rate=decay, overload_threshold=0.05
    )
    key = f"{world.receiver.pid}:{world.sender.pid}:{world.equivalent.code}"
    history = EdgeClearingHistory(
        original_limit=Decimal("1000.00"), clearing_count=7, last_clearing_tick=1
    )
    run._edge_clearing_history[key] = history
    try:
        await runtime.stop(run.run_id)
        before = await _debts(factory, world)
        await runtime.restart(run.run_id)
        assert await _debts(factory, world) == before
        assert run._edge_clearing_history[key] is history
        assert (history.original_limit, history.clearing_count) == (
            Decimal("1000.00"),
            7,
        )
        await _tick(runtime, run, 1)
        async with factory() as session:
            limit = (
                await session.execute(
                    select(TrustLine.limit).where(
                        TrustLine.equivalent_id == world.equivalent.id
                    )
                )
            ).scalar_one()
        assert limit == Decimal(
            expected
        ), f"configured decay={decay} after restart actual={limit}, expected={expected}"
        assert await _debts(factory, world) == before
        assert history.clearing_count == 7
    finally:
        await runtime.stop(run.run_id)
        _forget_the_route_cache(world)
