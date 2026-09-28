"""Programme 023, slice (c): the simulator isolation rule of the periodic runner (spec decision 9, form (b)).

Form (b), chosen 2026-09-28 (spec, decision 9, "Выбор формы"): the global periodic clearing is OFF on a database
that holds simulator data, and the runner REFUSES rather than silently working. The durable evidence it reads:
a `simulator_runs` row with `mode = 'real'`, read in the same SERIALIZABLE transaction as the snapshot; and the
process's own `SIMULATOR_DB_ENABLED` (when false, this process's real runs leave no row, so the absence of rows
proves nothing). Ownership is never inferred from in-memory runs.

* Positive control: on a hub database (no real simulator run) the periodic pass clears its cycle.
* Exclusion: a real-mode run row -> refused, the cycle untouched; `SIMULATOR_DB_ENABLED = false` -> refused.
* Counter-check of the filter (anti-vacuum): a `fixtures`-mode run row (which never touches money tables) does
  not refuse - the rule excludes on the evidence it names and not on "any row".
* `CLEARING_ENABLED = false`: the periodic pass clears nothing.

The periodic loop itself is not started by default in slice (c) (`tests/unit/test_p023_c_runner_is_not_wired.py`).

RED ON A TREE WITHOUT SLICE (c): the surface lookup (`tests/p023_support.py::slice_c_surface`) ends each test on
`TargetMismatch`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models.simulator_storage import SimulatorRun
from app.db.models.transaction import Transaction
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import debt_uuid, ring, seed_graph
from tests.p023_support import remaining_debts, slice_c_surface, target_xfail_023

pytestmark = [target_xfail_023("(c)", "no periodic runner with the simulator isolation rule"), MODE_B]

CODE = "PQI"
HUB = ring(["p023ia", "p023ib", "p023ic"], ["4", "4", "4"], [debt_uuid(0x2305, k) for k in range(3)])


async def _stand(db_session):
    api = slice_c_surface()
    await seed_graph(db_session, CODE, HUB)
    return api, sessionmaker_of(db_session)


async def _record_run(factory, *, mode: str) -> None:
    async with factory() as session:
        session.add(SimulatorRun(run_id=f"p023c-{mode}", scenario_id="p023c", mode=mode, state="stopped", owner_id="test"))
        await session.commit()


async def _clearings(factory) -> int:
    async with factory() as session:
        return len((await session.execute(select(Transaction.tx_id).where(Transaction.type == "CLEARING"))).all())


async def _left(factory) -> list:
    async with factory() as session:
        return await remaining_debts(session, CODE)


@pytest.mark.asyncio
async def test_positive_control_the_periodic_pass_clears_a_hub_cycle(db_session) -> None:
    api, factory = await _stand(db_session)
    results = await api.run_periodic_clearing_pass(factory, None)
    assert results[CODE].status == "complete", results
    assert len(results[CODE].committed) == 1
    assert await _left(factory) == [] and await _clearings(factory) == 1


@pytest.mark.asyncio
async def test_a_real_simulator_run_in_the_database_refuses_the_periodic_pass(db_session) -> None:
    api, factory = await _stand(db_session)
    await _record_run(factory, mode="real")
    with pytest.raises(api.ClearingPeriodicRefused) as refused:
        await api.run_periodic_clearing_pass(factory, None)
    assert refused.value.reason == "simulator_real_runs_in_database"
    assert await _clearings(factory) == 0
    assert {row[0] for row in await _left(factory)} == {str(e.debt_id) for e in HUB}


@pytest.mark.asyncio
async def test_without_simulator_persistence_the_periodic_pass_is_refused(db_session, monkeypatch) -> None:
    api, factory = await _stand(db_session)
    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", False)
    with pytest.raises(api.ClearingPeriodicRefused) as refused:
        await api.run_periodic_clearing_pass(factory, None)
    assert refused.value.reason == "simulator_persistence_disabled"
    assert await _clearings(factory) == 0


@pytest.mark.asyncio
async def test_counter_check_a_fixtures_mode_run_does_not_refuse(db_session) -> None:
    api, factory = await _stand(db_session)
    await _record_run(factory, mode="fixtures")
    results = await api.run_periodic_clearing_pass(factory, None)
    assert results[CODE].status == "complete" and await _left(factory) == []


@pytest.mark.asyncio
async def test_the_isolation_check_itself_reads_the_evidence(db_session) -> None:
    api, factory = await _stand(db_session)
    async with factory() as session:
        await api.check_periodic_isolation(session)  # a hub database: no refusal
        await session.rollback()
    await _record_run(factory, mode="real")
    async with factory() as session:
        with pytest.raises(api.ClearingPeriodicRefused):
            await api.check_periodic_isolation(session)
        await session.rollback()


@pytest.mark.asyncio
async def test_clearing_disabled_the_periodic_pass_clears_nothing(db_session, monkeypatch) -> None:
    api, factory = await _stand(db_session)
    monkeypatch.setattr(settings, "CLEARING_ENABLED", False)
    assert await api.run_periodic_clearing_pass(factory, None) == {}
    assert await _clearings(factory) == 0
