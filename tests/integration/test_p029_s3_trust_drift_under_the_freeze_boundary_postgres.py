"""029 S3 `F-029-10` (BACKLOG № 273, 49, 272): trust drift decides from what it read under its locks - reproducers.

Drift takes the locks of a money writer (028 `F-028-28`): its participants `FOR SHARE` with the status read by that
statement, then its lines `FOR UPDATE` in id order, and only then reads the limit and the debt. Every schedule here is
real - two sessions on one mode-B clone, the wait witnessed by `pg_blocking_pids`; nothing is injected into the driver.
On `8552c989` red: (а) limit 98 instead of 200; (б) and (б′) the line of a frozen participant is rewritten and the
freeze never meets the drift; (в) `40P01`.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.config import settings
from app.core.simulator import trust_drift_engine
from app.core.simulator.models import EdgeClearingHistory, TrustDriftConfig
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.core.trustlines.service import TrustLineService
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.trustline import TrustLineUpdateRequest
from tests.integration.test_p019_t1908_lock_removal_experiments_postgres import count_conflicts
from tests.integration.test_p021_trust_drift_is_audited_postgres import (  # noqa: F401 - `factory` is a fixture
    factory,
    limits,
    run_for,
    scenario_for,
    world,
)
from tests.integration.test_p028_e3_freeze_boundary_postgres import _admin_status, _pay

LINES = [("A", "B", "100.00", "active"), ("A", "C", "100.00", "active")]  # (creditor, debtor): B and C owe A
DEBTS = [("B", "A", "90.00"), ("C", "A", "90.00")]  # ratio 0.9 >= 0.8: both decay to 98.00
CFG = dict(enabled=True, decay_rate=0.02, min_limit_ratio=0.3, overload_threshold=0.8, growth_rate=0.05, max_growth=2.0)
ENGINE = TrustDriftEngine(sse=None, utc_now=None, logger=logging.getLogger("tests.p029.drift"), get_scenario_raw=lambda _s: {})


@pytest.fixture(autouse=True)
def generous_budgets(monkeypatch):
    for name in ("PREPARE_TIMEOUT_SECONDS", "COMMIT_TIMEOUT_SECONDS", "PAYMENT_TOTAL_TIMEOUT_SECONDS"):
        monkeypatch.setattr(settings, name, 30)


async def _stand(factory, lines=LINES, debts=DEBTS, roles="ABCD"):  # noqa: F811
    eq, people = await world(factory, list(roles), lines, debts)
    run, scenario = run_for(list(people.values()), eq.code), scenario_for(eq, people, lines, CFG)
    run._trust_drift_config = TrustDriftConfig(**CFG)
    run._edge_clearing_history = {f"{people[c].pid}:{people[d].pid}:{eq.code}": EdgeClearingHistory(
        original_limit=Decimal(limit)) for c, d, limit, _ in lines}
    snapshot = {(people[d].pid, people[c].pid, eq.code): Decimal(a) for d, c, a in debts}
    return eq, people, run, scenario, snapshot


async def _drift(factory, run, scenario, snapshot, eq, *, kind="decay", pid=None):  # noqa: F811
    """One drift transaction, as its owner runs it: the engine's call, then the commit. `pid` receives the backend."""
    async with factory() as s:
        if pid is not None:
            pid.append(await s.scalar(text("SELECT pg_backend_pid()")))
        if kind == "decay":
            res = await ENGINE.apply_trust_decay(run, s, 7, snapshot, scenario)
            await s.commit()
            return res
        edges = {(t["from"], t["to"]) for t in scenario["trustlines"]}
        return await ENGINE.apply_trust_growth(run, s, edges, eq.code, 7)


async def _freeze(factory, pid: str, reached: asyncio.Event | None = None, hold: asyncio.Event | None = None):  # noqa: F811
    """The admin freeze; with `hold`, paused right before its COMMIT, its participant row lock held."""
    async with factory() as s:
        commit = s.commit

        async def held_commit():
            if hold is not None:
                reached.set()
                await hold.wait()
            return await commit()

        s.commit = held_commit
        await _admin_status(s, pid)


async def _waits(factory, sql: str, pid: int, other: asyncio.Task) -> bool:  # noqa: F811
    """True once `sql` (a `pg_blocking_pids` question about backend `pid`) holds; False if `other` ended first."""
    async with factory() as w:
        while not other.done():
            if await w.scalar(text(sql), {"p": pid}):
                return True
            await w.rollback()
            await asyncio.sleep(0.02)
    return False


_BLOCKS_SOMEONE = "SELECT count(*) FROM pg_stat_activity WHERE :p = ANY(pg_blocking_pids(pid))"
_IS_BLOCKED = "SELECT cardinality(pg_blocking_pids(:p))"


@pytest.mark.asyncio
async def test_a_decay_does_not_overwrite_a_limit_patched_since_the_scenario_read(factory) -> None:  # noqa: F811
    """(а) № 273: the scenario holds 100 at debt 90; the creditor's PATCH to 200 holds the line; the decay waits for it."""
    eq, p, run, scenario, snapshot = await _stand(factory)
    async with factory() as patch:
        line = await patch.scalar(select(TrustLine.id).where(TrustLine.from_participant_id == p["A"].id,
                                                             TrustLine.to_participant_id == p["B"].id))
        service = TrustLineService(patch)
        batch = service.begin_internal_batch()
        await service.execute_update(batch, line, p["A"].id, TrustLineUpdateRequest(limit="200.00", signature="-"),
                                     require_signature=False)
        await batch.finish()
        decay = asyncio.create_task(_drift(factory, run, scenario, snapshot, eq))
        holder = await patch.scalar(text("SELECT pg_backend_pid()"))
        assert await _waits(factory, _BLOCKS_SOMEONE, holder, decay), "premise: the decay did not wait for the PATCH"
        await patch.commit()
    await asyncio.wait_for(decay, 40)
    after = await limits(factory, eq, p)
    assert after[("A", "C")] == (Decimal("98.00"), "active"), after  # positive control: an unpatched line decays
    assert after[("A", "B")] == (Decimal("200.00"), "active"), f"the decay wrote over the PATCH from a stale limit: {after}"


@pytest.mark.parametrize("kind, moved", [("decay", "98.00"), ("growth", "105.00")])
@pytest.mark.asyncio
async def test_drift_leaves_the_lines_of_a_suspended_participant_alone(factory, kind, moved) -> None:  # noqa: F811
    """(б) № 272, sequential. Anti-vacuum of the status rule: A -> C, both active, still drifts while B is frozen."""
    eq, p, run, scenario, snapshot = await _stand(factory)
    async with factory() as s:
        await _admin_status(s, p["B"].pid)
    await _drift(factory, run, scenario, snapshot, eq, kind=kind)
    after = await limits(factory, eq, p)
    assert after[("A", "C")] == (Decimal(moved), "active"), after
    assert after[("A", "B")] == (Decimal("100.00"), "active"), f"{kind} changed the line of suspended B: {after}"


@pytest.mark.parametrize("order", ["drift_first", "freeze_first"])
@pytest.mark.asyncio
async def test_no_drift_write_lands_on_a_line_after_its_participant_froze(factory, monkeypatch, order) -> None:  # noqa: F811
    """(б′) concurrent, both arrival orders: the freeze and the drift MEET on B's participant row."""
    eq, p, run, scenario, snapshot = await _stand(factory)
    paused, go, pid = asyncio.Event(), asyncio.Event(), []
    write = trust_drift_engine._set_limit_internally

    async def pause_then_write(*args, **kwargs):  # every status is read, no limit is written yet
        if order == "drift_first" and not paused.is_set():
            paused.set()
            await go.wait()
        return await write(*args, **kwargs)

    monkeypatch.setattr(trust_drift_engine, "_set_limit_internally", pause_then_write)
    if order == "drift_first":
        drifting = asyncio.create_task(_drift(factory, run, scenario, snapshot, eq, pid=pid))
        await asyncio.wait_for(paused.wait(), 20)
        freezing = asyncio.create_task(_freeze(factory, p["B"].pid))
        met = await _waits(factory, _BLOCKS_SOMEONE, pid[0], freezing)
        go.set()
        expected = "98.00"  # the drift came first: its write stands, and the freeze committed after it
    else:
        reached, hold = asyncio.Event(), asyncio.Event()
        freezing = asyncio.create_task(_freeze(factory, p["B"].pid, reached, hold))
        await asyncio.wait_for(reached.wait(), 20)
        drifting = asyncio.create_task(_drift(factory, run, scenario, snapshot, eq, pid=pid))
        while not pid and not drifting.done():
            await asyncio.sleep(0.01)
        met = await _waits(factory, _IS_BLOCKED, pid[0], drifting)
        hold.set()
        expected = "100.00"  # the freeze came first: the drift waited, read `suspended` and left the line
    await asyncio.wait_for(asyncio.gather(drifting, freezing), 40)
    after = await limits(factory, eq, p)
    async with factory() as s:
        assert await s.scalar(select(Participant.status).where(Participant.id == p["B"].id)) == "suspended"
    assert after[("A", "C")] == (Decimal("98.00"), "active"), after  # positive control: the drift did write
    assert met, f"{order}: the freeze of B and the drift over A -> B never waited for one another; limits {after}"
    assert after[("A", "B")] == (Decimal(expected), "active"), after


@pytest.mark.asyncio
async def test_a_decay_takes_its_lines_in_the_order_of_a_payment(factory, monkeypatch) -> None:  # noqa: F811
    """(в) № 49: A pays C through B over both lines (id order) while the decay holds the scenario's FIRST line - the
    one with the higher id - and then goes for the other."""
    lines = [("B", "A", "100.00", "active"), ("C", "B", "100.00", "active")]
    eq, p, run, scenario, snapshot = await _stand(factory, lines, [("A", "B", "90.00"), ("B", "C", "90.00")], "ABC")
    async with factory() as s:
        ids = {(f, t): i for i, f, t in (await s.execute(select(
            TrustLine.id, TrustLine.from_participant_id, TrustLine.to_participant_id).where(
                TrustLine.equivalent_id == eq.id))).all()}
    by_pid = {x.pid: x.id for x in p.values()}
    scenario["trustlines"].sort(key=lambda t: ids[(by_pid[t["from"]], by_pid[t["to"]])], reverse=True)
    conflicts, paused, go, pid = count_conflicts(monkeypatch), asyncio.Event(), asyncio.Event(), []
    write = trust_drift_engine._set_limit_internally

    async def pause_then_write(*args, **kwargs):
        if not paused.is_set():
            paused.set()
            await go.wait()
        return await write(*args, **kwargs)

    async def pay():
        async with factory() as s:
            return (await _pay(s, eq, p, "ABC")).status

    monkeypatch.setattr(trust_drift_engine, "_set_limit_internally", pause_then_write)
    decaying = asyncio.create_task(_drift(factory, run, scenario, snapshot, eq, pid=pid))
    await asyncio.wait_for(paused.wait(), 20)
    paying = asyncio.create_task(pay())
    assert await _waits(factory, _BLOCKS_SOMEONE, pid[0], paying), "premise: the payment did not wait for the decay"
    go.set()
    outcome = await asyncio.wait_for(asyncio.gather(decaying, paying, return_exceptions=True), 60)
    assert not isinstance(outcome[0], BaseException) and outcome[1] == "COMMITTED" and "40P01" not in conflicts.payment, (
        f"decay and payment over the same two lines: {outcome!r}, payment conflicts {conflicts.payment}")
