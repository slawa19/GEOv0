"""The test harness drains a run before its owner's bindings and database disappear."""

import asyncio
import json
import zipfile

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.simulator.runtime import runtime
from tests.conftest import sessionmaker_of
from tests.p042_support import no_real_tick  # noqa: F401


@pytest_asyncio.fixture
async def cleanup_observer(client, db_session):
    observed = {}
    yield observed
    run = observed["run"]
    try:
        assert observed["heartbeat"].done(), "owner teardown saw an undrained heartbeat"
        assert observed["writer"].done(), "owner teardown saw an undrained artifact writer"
        assert runtime.get_run(run.run_id) is run, "fixture finalizers still own the retained record"
        async with sessionmaker_of(db_session)() as session:
            state = await session.scalar(
                text("SELECT state FROM simulator_runs WHERE run_id = :run_id"),
                {"run_id": run.run_id},
            )
        assert state == "stopped", "the final stop must persist to the owner's database"
        assert json.loads((run.artifacts_dir / "status.json").read_text())["state"] == "stopped"
        with zipfile.ZipFile(run.artifacts_dir / "bundle.zip") as bundle:
            assert "harness-cleanup-event" in bundle.read("events.ndjson").decode()
    finally:
        # The failing-before control must not leave a background task in the test process.
        await runtime.stop(run.run_id)


@pytest.mark.parametrize("db_session", ["A", "B"], indirect=True)
async def test_stopped_run_is_drained_before_owner_fixture_teardown(
    cleanup_observer, no_real_tick  # noqa: F811
):
    assert not runtime._runs, "a prior test's run must not enter the next test"
    run_id = await runtime.create_run(
        scenario_id="minimal", mode="real", intensity_percent=0,
        owner_id="anon:harness-cleanup", owner_kind="anon",
    )
    run = runtime.get_run(run_id)
    runtime._artifacts.enqueue_event_artifact(
        run_id, {"type": "tx.updated", "event_id": "harness-cleanup-event"}
    )
    cleanup_observer.update(run=run, heartbeat=run._heartbeat_task, writer=run._artifact_events_task)
    assert not run._heartbeat_task.done() and not run._artifact_events_task.done()
    run.state = "stopped"  # Production stop sets this before awaiting final work.


async def test_the_next_database_fixture_does_not_revisit_a_prior_run(db_session):
    assert not runtime._runs, "test-owned records must be retired after their finalizers"
    await asyncio.sleep(0)


@pytest_asyncio.fixture
async def direct_clone_observer(committed_database, monkeypatch):
    """Direct clone callers never entered the old db_session cleanup path."""
    import app.db.session as app_session

    monkeypatch.setattr(app_session, "AsyncSessionLocal", committed_database.sessionmaker)
    observed = {}
    yield observed
    run = observed["run"]
    assert observed["heartbeat"].done() and observed["writer"].done()
    assert app_session.AsyncSessionLocal is committed_database.sessionmaker
    assert runtime.get_run(run.run_id) is run
    async with committed_database.sessionmaker() as session:
        assert await session.scalar(
            text("SELECT state FROM simulator_runs WHERE run_id = :run_id"), {"run_id": run.run_id}
        ) == "stopped"


async def test_direct_clone_callers_are_cleaned_before_the_clone_is_dropped(direct_clone_observer, no_real_tick):  # noqa: F811
    assert not runtime._runs
    run_id = await runtime.create_run(
        scenario_id="minimal", mode="real", intensity_percent=0,
        owner_id="anon:harness-direct-clone", owner_kind="anon",
    )
    run = runtime.get_run(run_id)
    direct_clone_observer.update(run=run, heartbeat=run._heartbeat_task, writer=run._artifact_events_task)
