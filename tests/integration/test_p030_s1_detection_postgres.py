"""030 S1: detection is not switched off silently (F-030-7, F-030-8, F-030-9; spec `specs/030-zero-sum-protection`).

THE PATH IS THE REAL ONE: the scheduled host `_run_integrity_checkpoints_once`, the real verifier and the real routes
on a clone. Only the call whose failure is the subject is replaced, and each test asserts the replacement was reached.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

import app.core.integrity as integrity_module
import app.core.maintenance_jobs as maintenance_jobs
from app.config import settings
from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import PASSED, UNVERIFIABLE, run_scheduled_reconciliation, take_baseline
from app.core.simulator.real_scenario_seeder import RealScenarioSeeder
from app.db.models.equivalent import Equivalent
from app.db.reconciliation_tables import debt_reconciliation_baselines, debt_reconciliation_results
from tests.conftest import MODE_B, sessionmaker_of
from tests.integration.test_p024_integrity_status_sees_hold_postgres import _run_the_host
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: F401 - opt-in fixture

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _baselined(factory, code: str) -> uuid.UUID:
    async with factory() as session:
        eq = Equivalent(code=code, precision=2, is_active=True, metadata_={})
        session.add(eq)
        await session.flush()
        await take_baseline(session, eq.id)
        await session.commit()
        return eq.id


async def _latest(factory, equivalent_id):
    c = debt_reconciliation_results.c
    async with factory() as session:
        return (await session.execute(select(c.id, c.status, c.last_checked_at).where(
            c.equivalent_id == equivalent_id, c.is_latest.is_(True)))).one_or_none()


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_f030_7_a_checkpoint_error_does_not_cancel_the_reconciliation(monkeypatch) -> None:
    """MUTATION: put the checkpoint call back outside its own error boundary - no new `last_checked_at`, red."""

    from tests.conftest import TestingSessionLocal as factory

    eq = await _baselined(factory, "P30SA")
    await _run_the_host(monkeypatch, factory)
    before = await _latest(factory, eq)
    assert before is not None and before.status == PASSED, before

    reached: list[int] = []

    async def compute(_session):
        reached.append(1)
        raise RuntimeError("p030 F-030-7: the checkpoints fail")

    monkeypatch.setattr(integrity_module, "compute_and_store_integrity_checkpoints", compute)
    app = await _run_the_host(monkeypatch, factory)

    assert reached == [1], "stand: the checkpoint failure was never raised"
    after = await _latest(factory, eq)
    assert after.id == before.id and after.last_checked_at > before.last_checked_at, (before, after)
    assert app.state.background_jobs["integrity"] == {
        "status": "failed", "event": "periodic_checkpoints_error", "error_type": "RuntimeError"
    }, app.state.background_jobs
    assert app.completed is False
    assert maintenance_jobs.debt_reconciliation_run_failed(app) is False, "a checkpoint-only error flags the verdict"


@MODE_B
@pytest.mark.asyncio
async def test_f030_8_an_errored_or_stale_reconciliation_is_a_warning_and_holds_nothing(
    client, db_session, monkeypatch
) -> None:
    """Fresh PASSED -> healthy (counter-check); verifier error -> warning, no hold; clean run -> healthy;
    result older than the policy threshold -> warning. MUTATION: drop either branch of the freshness view - red."""

    from app.main import app as main_app

    factory = sessionmaker_of(db_session)  # `client` already points the host's AsyncSessionLocal at this clone
    monkeypatch.setattr(main_app.state, "background_jobs", {}, raising=False)
    eq = await _baselined(factory, "P30SB")

    async def summary() -> dict:
        response = await client.get("/api/v1/integrity/summary", headers=ADMIN)
        assert response.status_code == 200, response.text
        return next(item for item in response.json()["equivalents"] if item["equivalent"] == "P30SB")

    async def alerts() -> list[str]:
        response = await client.get("/api/v1/integrity/status", headers=ADMIN)
        assert response.status_code == 200, response.text
        return [alert for alert in response.json()["alerts"] if "P30SB" in alert]

    await maintenance_jobs._run_integrity_checkpoints_once(main_app, reason="periodic")
    assert (await summary())["status"] == "healthy" and await alerts() == []
    passed = await _latest(factory, eq)

    original, reached = reconciliation.verify_journal_equals_change, []

    async def verify(session, equivalent_id):
        if equivalent_id == eq:
            reached.append(equivalent_id)
            raise RuntimeError("p030 F-030-8: the verifier fails on this equivalent")
        return await original(session, equivalent_id)

    with monkeypatch.context() as scoped:
        scoped.setattr(reconciliation, "verify_journal_equals_change", verify)
        await maintenance_jobs._run_integrity_checkpoints_once(main_app, reason="periodic")
    assert reached == [eq] and await _latest(factory, eq) == passed, "stand: the old PASSED must still be latest"
    errored = await summary()
    assert errored["status"] == "warning" and errored["hold"] is False, errored
    assert any("ended in an error" in alert for alert in await alerts()), await alerts()
    async with factory() as session:
        assert (await session.get(Equivalent, eq)).integrity_hold_result_id is None, "an error must not hold money"

    await maintenance_jobs._run_integrity_checkpoints_once(main_app, reason="periodic")
    assert (await summary())["status"] == "healthy"

    stale = reconciliation.result_freshness_threshold() + timedelta(seconds=60)
    async with factory() as session:
        await session.execute(update(debt_reconciliation_results).where(
            debt_reconciliation_results.c.id == passed.id).values(last_checked_at=passed.last_checked_at - stale))
        await session.commit()
    assert (await summary())["status"] == "warning"
    assert any("older than" in alert for alert in await alerts()), await alerts()


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_f030_9_the_simulator_seeder_baselines_a_new_equivalent_and_leaves_an_existing_one(monkeypatch) -> None:
    """MUTATION: go back to `session.add(Equivalent(...))` in the seeder - no baseline, UNVERIFIABLE, red."""

    from tests.conftest import TestingSessionLocal as factory

    tag = uuid.uuid4().hex[:5].upper()
    new_code, old_code = f"N{tag}", f"O{tag}"
    async with factory() as session:
        session.add(Equivalent(code=old_code, precision=2, is_active=True, metadata_={}))
        await session.commit()
    scenario = {
        "equivalents": [new_code, old_code],
        "participants": [{"id": f"P30_A_{tag}"}, {"id": f"P30_B_{tag}"}],
        "trustlines": [{"from": f"P30_A_{tag}", "to": f"P30_B_{tag}", "equivalent": new_code, "limit": "10"}],
    }
    async with factory() as session:
        await RealScenarioSeeder().seed_scenario_into_db(session=session, scenario=scenario)
        await session.commit()

    async with factory() as session:
        ids = dict((await session.execute(select(Equivalent.code, Equivalent.id).where(
            Equivalent.code.in_([new_code, old_code])))).all())
        baselined = set((await session.execute(select(debt_reconciliation_baselines.c.equivalent_id).where(
            debt_reconciliation_baselines.c.equivalent_id.in_(ids.values())))).scalars())
    assert baselined == {ids[new_code]}, "a new equivalent gets a baseline; an existing one never automatically"

    counts = await run_scheduled_reconciliation(factory, equivalent_ids=[ids[new_code], ids[old_code]])
    assert counts[PASSED] == 1 and counts[UNVERIFIABLE] == 1 and counts["error"] == 0, counts
    assert (await _latest(factory, ids[new_code])).status == PASSED
