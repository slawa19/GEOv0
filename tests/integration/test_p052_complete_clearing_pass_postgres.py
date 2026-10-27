"""052: real durable occurrences across the old cutoff, and bounded cancellation publication.

Only clocks / pauses / read failures are injected. The runner, occurrence executor,
PostgreSQL debts and patch calculator are real; no synthetic financial handoff.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.config import Settings, settings
from app.core.clearing import runner as clearing_runner
from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.trustline import TrustLine
import app.core.simulator.sse_broadcast as sse_broadcast
from tests.integration.test_p023_d_tick_driver_through_runner_postgres import CODE, T1, T2, _stand
from tests.simulator_tick_stand import install_tick_stand, pooled_sessionmaker_over


@pytest_asyncio.fixture
async def factory(committed_database, monkeypatch):
    for name in ("SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS", "SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)
    monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS", defaults.SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS)
    monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", defaults.SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC)
    async with pooled_sessionmaker_over(committed_database.url) as made:
        install_tick_stand(monkeypatch, made)
        # The process planner is real and warm: cold spawn has its own 023 controls.
        await asyncio.wrap_future(clearing_runner._default_planner_executor().submit(clearing_runner.plan_clearing, []))
        yield made


async def _debts(stand):
    async with stand.factory() as session:
        return dict((await session.execute(select(Debt.id, Debt.amount))).all())


def _clocked_pass(monkeypatch):
    real = clearing_runner.run_clearing_pass
    results = []

    async def clocked(*args, **kwargs):
        clock = [asyncio.get_running_loop().time()]
        handoff = kwargs["on_committed"]

        def committed(occurrence):
            handoff(occurrence)
            clock[0] += 9.0  # past BOTH old 5-second soft and 8-second emergency settings

        kwargs.update(on_committed=committed, deadline_clock=lambda: clock[0])
        result = await real(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(clearing_runner, "run_clearing_pass", clocked)
    return results


@pytest.mark.parametrize("budget", [None, -1, 250], ids=["default-disabled", "negative-disabled", "explicit-positive"])
@pytest.mark.parametrize("surface", ["periodic", "scripted"])
@pytest.mark.asyncio
async def test_default_pass_crosses_the_former_deadline_but_positive_budget_still_bounds_it(factory, monkeypatch, budget, surface):
    if budget is not None:
        monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS", budget)
    stand = await _stand(factory, T1 + T2)
    results = _clocked_pass(monkeypatch)
    if surface == "periodic":
        volumes = await stand.tick()
    else:
        notes = []
        monkeypatch.setattr(stand.runner._inject_executor, "_artifacts", SimpleNamespace(
            enqueue_event_artifact=lambda _run_id, payload: notes.append(payload),
        ))
        async with factory() as session:
            volumes, attempted = await stand.runner._tick.run_scripted_clearings(
                session=session, run_id=stand.run.run_id, run=stand.run,
                scenario={"events": [{"time": 0, "type": "clearing", "equivalent": CODE}]},
                equivalents=[CODE], planned_len=0, tick_t0=time.monotonic(), payments_result=None,
            )
        assert attempted == {CODE}
        assert len(notes) == 1
        assert (0 in stand.run._real_fired_scenario_event_indexes) == (budget != 250)
    [result] = results
    [done] = stand.done_events()
    if budget == 250:
        assert result.status == "interrupted" and result.reason.value == "budget_exhausted"
        assert await stand.clearings() == done["cleared_cycles"] == 1
        left = await stand.total()
        assert left in (Decimal("6"), Decimal("9"))
        assert volumes[CODE] == Decimal(done["cleared_amount"]) == (Decimal("15") - left) / 3
    else:
        assert result.status == "complete", (result.status, result.reason, await stand.total())
        assert await stand.clearings() == done["cleared_cycles"] == 2
        assert await stand.total() == 0 and volumes[CODE] == Decimal(done["cleared_amount"]) == Decimal("5")
    assert done["node_patch"] and done["edge_patch"]
    assert stand.run._real_clearing_task is None


@pytest.mark.parametrize("cancel_at", ["before-publication", "after-done", "after-closed"])
@pytest.mark.asyncio
async def test_cancel_preserves_closed_line_publication_without_replaying_done(factory, monkeypatch, cancel_at):
    stand = await _stand(factory, T1 + T2)
    stand.runner._tick._real_clearing_time_budget_ms = 250
    _clocked_pass(monkeypatch)
    async with factory() as session:
        await session.execute(update(TrustLine).values(limit=0, close_requested_at=datetime.now(timezone.utc)))
        await session.commit()
    reached = asyncio.Event()
    rechecks = []
    real_live = sse_broadcast._live_pairs
    real_patches = stand.runner._tick._clearing_patches

    async def patches(*args):
        if cancel_at == "before-publication" and not reached.is_set():
            reached.set()
            await asyncio.Event().wait()
        return await real_patches(*args)

    async def live(eq, pairs):
        rechecks.append(set(pairs))
        if cancel_at == "after-done" and len(rechecks) == 1:
            reached.set()
            await asyncio.Event().wait()
        return await real_live(eq, pairs)

    async def growth(*args):
        # AFTER both publications, before any optional growth actually starts.
        if cancel_at == "after-closed":
            reached.set()
            await asyncio.Event().wait()
        pytest.fail("cancelled publication started growth")

    monkeypatch.setattr(stand.runner._tick, "_clearing_patches", patches)
    monkeypatch.setattr(sse_broadcast, "_live_pairs", live)
    monkeypatch.setattr(stand.runner._tick, "_grow_trust_after_clearing", growth)
    task = asyncio.create_task(stand.tick())
    await asyncio.wait_for(reached.wait(), 2)
    if cancel_at != "before-publication":
        assert len(stand.done_events()) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await stand.clearings() == 1
    [done] = stand.done_events()
    assert Decimal(done["cleared_amount"]) == (Decimal("15") - await stand.total()) / 3
    closed_events = [e for e in stand.sse.events if e.get("reason") == "trustline_closed"]
    [closed] = closed_events
    removed = {(e["from_pid"], e["to_pid"]) for e in closed["payload"]["removed_edges"]}
    assert removed == {(e["from"], e["to"]) for e in done["cycle_edges"]}
    assert stand.sse.events.index(done) < stand.sse.events.index(closed)
    assert not removed.intersection(stand.run._edges_by_equivalent[CODE])
    assert stand.run._real_clearing_task is None


@pytest.mark.parametrize("budget", [0, 250, 5000, 60000])
@pytest.mark.asyncio
async def test_emergency_limit_is_independent_of_soft_budget_on_real_pass(factory, monkeypatch, budget):
    monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_TIME_BUDGET_MS", budget)
    stand = await _stand(factory, T1 + T2)
    # A configured emergency limit must survive even a zero / short soft budget.
    assert stand.runner._tick.clearing_hard_timeout_sec() == 60.0
    monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 0)
    assert stand.runner._tick.clearing_hard_timeout_sec() == 0.1
    monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 0.35)
    real = ClearingService.execute_occurrence
    calls = []

    async def held(self, occurrence, **kwargs):
        calls.append(occurrence)
        await asyncio.sleep(10)
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", held)
    started = time.monotonic()
    assert await stand.tick() == {CODE: Decimal("0")}
    assert time.monotonic() - started < 2
    assert len(calls) == 1 and await stand.total() == Decimal("15") and await stand.clearings() == 0
    assert stand.done_events() == [] and stand.run._real_clearing_task is None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_default_real_duration_pass_finishes_beyond_the_former_eight_seconds(factory, monkeypatch):
    stand = await _stand(factory, T1 + T2)
    real = ClearingService.execute_occurrence
    calls = []

    async def slow(self, occurrence, **kwargs):
        calls.append(occurrence)
        await asyncio.sleep(4.5)
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", slow)
    started = time.monotonic()
    volumes = await stand.tick()
    elapsed = time.monotonic() - started
    assert elapsed > 8 and len(calls) == 2
    assert await stand.total() == 0 and await stand.clearings() == 2
    [done] = stand.done_events()
    assert done["cleared_cycles"] == 2 and Decimal(done["cleared_amount"]) == volumes[CODE] == Decimal("5")
    assert stand.run._real_clearing_task is None


async def _hold_second(monkeypatch):
    real = ClearingService.execute_occurrence
    calls = []
    reached = asyncio.Event()

    async def held(self, occurrence, **kwargs):
        calls.append(occurrence)
        if len(calls) == 2:
            reached.set()
            await asyncio.Event().wait()
        return await real(self, occurrence, **kwargs)

    monkeypatch.setattr(ClearingService, "execute_occurrence", held)
    return calls, reached


async def _assert_partial(stand, calls, volumes=None, *, patches=True):
    assert len(calls) == 2 and await stand.clearings() == 1
    after = await _debts(stand)
    cleared = set(calls[0].debt_ids)
    # Fully consumed debts are deleted by the ledger, rather than retained as zero rows.
    expected = {e.debt_id: Decimal(e.amount) for e in T1 + T2 if e.debt_id not in cleared}
    assert after == expected
    amount = (Decimal("15") - sum(after.values())) / 3
    [done] = stand.done_events()
    assert done["cleared_cycles"] == 1 and Decimal(done["cleared_amount"]) == amount
    if volumes is not None:
        assert volumes == {CODE: amount}
    assert stand.run._real_clearing_task is None
    assert not [e for e in stand.sse.events if e.get("reason") == "trust_drift_growth"]
    if patches:
        assert done["node_patch"] and done["edge_patch"], done
        pairs = {(e.creditor, e.debtor) for e in T1 + T2 if e.debt_id in cleared}
        assert {(e["source"], e["target"]) for e in done["edge_patch"]} == pairs
        assert all(Decimal(e["used"]) == 0 for e in done["edge_patch"])
        assert all(Decimal(e["available"]) == Decimal("1000000") for e in done["edge_patch"])
        nodes = {pid for pair in pairs for pid in pair}
        assert {n["id"] for n in done["node_patch"]} == nodes
        assert all(Decimal(n["net_balance"]) == 0 and n["net_sign"] == 0 for n in done["node_patch"])
    else:
        assert done["node_patch"] is None and done["edge_patch"] is None


@pytest.mark.asyncio
async def test_emergency_after_commit_publishes_real_patches_and_exact_volume_once(factory, monkeypatch):
    stand = await _stand(factory, T1 + T2)
    stand.runner._tick._real_clearing_time_budget_ms = 60000
    monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 0.5)
    calls, reached = await _hold_second(monkeypatch)

    async def no_growth(*args, **kwargs):
        pytest.fail("emergency cleanup started trust growth")

    monkeypatch.setattr(stand.runner._tick, "_grow_trust_after_clearing", no_growth)
    volumes = await stand.tick()
    assert reached.is_set()
    await _assert_partial(stand, calls, volumes)


@pytest.mark.parametrize("fault", ["error", "read-error", "hang", "repeated-cancel", "pending-grace"])
@pytest.mark.asyncio
async def test_cancel_recovery_failure_is_bounded_patchless_and_drained(factory, monkeypatch, caplog, fault):
    stand = await _stand(factory, T1 + T2)
    stand.runner._tick._real_clearing_time_budget_ms = 60000
    calls, reached = await _hold_second(monkeypatch)
    reading = asyncio.Event()
    real_patches = stand.runner._tick._clearing_patches

    async def unavailable(*args, **kwargs):
        reading.set()
        if fault == "error":
            raise OSError("recovery read unavailable")
        if fault == "read-error":
            async def failed_read(*args, **kwargs):
                raise OSError("recovery query unavailable")
            monkeypatch.setattr(args[2], "execute", failed_read)
            return await real_patches(*args, **kwargs)
        await asyncio.Event().wait()

    monkeypatch.setattr(stand.runner._tick, "_clearing_patches", unavailable)
    task = asyncio.create_task(stand.tick())
    await asyncio.wait_for(reached.wait(), 2)
    started = time.monotonic()
    child = stand.run._real_clearing_task
    if fault == "pending-grace":
        child.cancel()
        await asyncio.wait_for(reading.wait(), 1)
        monkeypatch.setattr(settings, "SIMULATOR_REAL_CLEARING_HARD_TIMEOUT_SEC", 0.2)
        await stand.runner._tick._await_pending_clearing(stand.run.run_id, run=stand.run)
    else:
        task.cancel()
    if fault == "repeated-cancel":
        await asyncio.wait_for(reading.wait(), 1)
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert time.monotonic() - started < 2.7
    assert reading.is_set()
    assert child.done() and child.cancelled()
    await _assert_partial(stand, calls, patches=False)
    assert any("clearing_cancel_recovery_failed" in r.getMessage() for r in caplog.records)
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and "_run_clearing" in repr(t.get_coro())]


@pytest.mark.asyncio
async def test_cancel_during_normal_patch_read_retries_with_a_fresh_session(factory, monkeypatch):
    stand = await _stand(factory, T1 + T2)
    # Explicit bounded pass commits exactly one occurrence on this same graph.
    stand.runner._tick._real_clearing_time_budget_ms = 250
    _clocked_pass(monkeypatch)
    real_patches = stand.runner._tick._clearing_patches
    reading = asyncio.Event()
    sessions = []

    async def interrupted(*args):
        sessions.append(args[2])
        if len(sessions) == 1:
            reading.set()
            await asyncio.Event().wait()
        return await real_patches(*args)

    monkeypatch.setattr(stand.runner._tick, "_clearing_patches", interrupted)
    task = asyncio.create_task(stand.tick())
    await asyncio.wait_for(reading.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    [done] = stand.done_events()
    assert done["node_patch"] and done["edge_patch"]
    assert Decimal(done["cleared_amount"]) == (Decimal("15") - await stand.total()) / 3
    assert stand.run._real_clearing_task is None
