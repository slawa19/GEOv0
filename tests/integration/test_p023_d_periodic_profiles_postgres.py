"""Programme 023, slice (d), decision R1: the periodic clearing loop by deployment profile.

R1 (2026-09-28): `CLEARING_PERIODIC_ENABLED` stays `false` by default - general, dev/test and simulator stands; a
SEPARATE hub deployment sets it `true` explicitly through its deployment entrypoint (`docker-compose.yml` passes
the variable). It is never inferred from `ENV=prod`. Three profiles, each through the application's REAL loop
(`app.main._start_configured_background_tasks` -> `_clearing_loop` -> the runner) on a disposable clone:

* hub profile - the variable set to `true` in the environment reaches `Settings`, the loop starts and clears an
  eligible debt;
* simulator profile - defaults: no `clearing` task is started, the cycle stays;
* wrong explicit enablement - `true` on a database holding a real simulator run: the loop runs, every pass is
  REFUSED (decision 9), the refusal is recorded and health is `degraded`, the cycle stays.

`ENV=prod` alone starts nothing (counter-check of "not inferred").
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings, settings
from app.db.models.simulator_storage import SimulatorRun
from tests.conftest import MODE_B, sessionmaker_of
from tests.p020_support import debt_uuid, ring, seed_graph
from tests.p023_support import positive_debt_total

pytestmark = MODE_B

CODE = "PQP"
HUB = ring(["p023pa", "p023pb", "p023pc"], ["4", "4", "4"], [debt_uuid(0x23D4, k) for k in range(3)])


def _app():
    return SimpleNamespace(
        state=SimpleNamespace(redis=None, _bg_stop_event=asyncio.Event(), _bg_tasks=[], background_jobs={})
    )


async def _stop(app) -> None:
    app.state._bg_stop_event.set()
    for task in app.state._bg_tasks:
        task.cancel()
    await asyncio.gather(*app.state._bg_tasks, return_exceptions=True)


async def _profile(monkeypatch, db_session, env: dict[str, str]):
    """Settings as the process would build them from `env`; the loop's sessions on the clone."""

    import app.db.session as app_db_session

    for key in ("CLEARING_PERIODIC_ENABLED", "ENV", "ENVIRONMENT"):
        monkeypatch.delenv(key, raising=False)
    # ENV must be explicit (`app/config.py`); a production profile also needs non-placeholder secrets.
    profile = {"ENV": "test", **env}
    if profile["ENV"] == "prod":
        profile.update(
            {
                "JWT_SECRET": "p023d-" + "j" * 40,
                "ADMIN_TOKEN": "p023d-" + "a" * 40,
                "SIMULATOR_SESSION_SECRET": "p023d-" + "s" * 40,
                "SIMULATOR_CSRF_ORIGIN_ALLOWLIST": "https://hub.example",
            }
        )
    for key, value in profile.items():
        monkeypatch.setenv(key, value)
    built = Settings()
    monkeypatch.setattr(settings, "CLEARING_PERIODIC_ENABLED", built.CLEARING_PERIODIC_ENABLED)
    monkeypatch.setattr(settings, "CLEARING_PERIODIC_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(settings, "INTEGRITY_CHECKPOINT_ENABLED", False)
    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True)
    factory = sessionmaker_of(db_session)
    monkeypatch.setattr(app_db_session, "AsyncSessionLocal", factory)
    await seed_graph(db_session, CODE, HUB)
    return built, factory


async def _total(factory):
    async with factory() as session:
        return await positive_debt_total(session, CODE)


async def _until(condition, timeout: float = 60.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await condition():
            return True
        await asyncio.sleep(0.1)
    return False


@pytest.mark.asyncio
async def test_hub_profile_starts_the_loop_and_clears_an_eligible_debt(db_session, monkeypatch) -> None:
    import app.main as main

    built, factory = await _profile(monkeypatch, db_session, {"CLEARING_PERIODIC_ENABLED": "true"})
    assert built.CLEARING_PERIODIC_ENABLED is True, "the deployment variable reaches Settings"
    app = _app()
    try:
        main._start_configured_background_tasks(app)
        assert [t.get_name() for t in app.state._bg_tasks] == ["geo:clearing"]
        cleared = await _until(lambda: _is_zero(factory))
    finally:
        await _stop(app)
    assert cleared, f"the hub loop never cleared the cycle ({app.state.background_jobs})"


async def _is_zero(factory) -> bool:
    return await _total(factory) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("env", [{}, {"ENV": "prod"}], ids=["defaults", "env_prod_is_not_an_enablement"])
async def test_simulator_profile_does_not_start_the_loop(db_session, monkeypatch, env) -> None:
    import app.main as main

    built, factory = await _profile(monkeypatch, db_session, env)
    assert built.CLEARING_PERIODIC_ENABLED is False
    app = _app()
    try:
        main._start_configured_background_tasks(app)
        assert app.state._bg_tasks == [] and "clearing" not in app.state.background_jobs
        await asyncio.sleep(0.5)
    finally:
        await _stop(app)
    assert await _total(factory) == 12


@pytest.mark.asyncio
async def test_a_wrong_explicit_enablement_keeps_the_refusal_and_degraded_health(db_session, monkeypatch) -> None:
    import app.main as main
    from app.utils.background_jobs import background_health_status

    built, factory = await _profile(monkeypatch, db_session, {"CLEARING_PERIODIC_ENABLED": "true"})
    async with factory() as session:
        session.add(SimulatorRun(run_id="p023d-real", scenario_id="p023d", mode="real", state="stopped", owner_id="test"))
        await session.commit()
    app = _app()

    async def refused() -> bool:
        return app.state.background_jobs.get("clearing", {}).get("event") == "refused_simulator_real_runs_in_database"

    try:
        main._start_configured_background_tasks(app)
        seen = await _until(refused)
        health = background_health_status(app)  # while the loop is still running, before shutdown
    finally:
        await _stop(app)
    assert seen, app.state.background_jobs
    assert health == "degraded"
    assert await _total(factory) == 12
