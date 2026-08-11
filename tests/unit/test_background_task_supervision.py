import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

import app.core.integrity as integrity_module
import app.core.recovery as recovery_module
import app.db.session as db_session_module
import app.main as main_module
from app.utils.background_jobs import background_jobs_degraded


def _test_app() -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            redis=None,
            _bg_stop_event=asyncio.Event(),
            _bg_tasks=[],
            background_jobs={},
        )
    )


@pytest.mark.asyncio
async def test_task_factory_failure_marks_job_degraded(caplog) -> None:
    app = _test_app()

    def broken_factory():
        raise RuntimeError("factory failed")

    with caplog.at_level(logging.ERROR):
        task = main_module._start_supervised_background_task(
            app,
            name="recovery",
            coroutine_factory=broken_factory,
        )

    assert task is None
    assert app.state.background_jobs["recovery"]["status"] == "failed"
    assert background_jobs_degraded(app) is True
    assert "background_job.start_failed name=recovery" in caplog.text


@pytest.mark.asyncio
async def test_task_creation_failure_marks_job_degraded(monkeypatch, caplog) -> None:
    app = _test_app()

    async def worker() -> None:
        await asyncio.sleep(0)

    def fail_create_task(coroutine, *, name):
        raise RuntimeError(f"cannot create {name}")

    monkeypatch.setattr(main_module.asyncio, "create_task", fail_create_task)

    with caplog.at_level(logging.ERROR):
        task = main_module._start_supervised_background_task(
            app,
            name="recovery",
            coroutine_factory=worker,
        )

    assert task is None
    assert app.state.background_jobs["recovery"]["status"] == "failed"
    assert app.state.background_jobs["recovery"]["event"] == "start_failed"
    assert "background_job.start_failed name=recovery" in caplog.text


@pytest.mark.asyncio
async def test_unexpected_task_exception_is_observable(caplog) -> None:
    app = _test_app()

    async def fail_unexpectedly() -> None:
        raise RuntimeError("worker failed")

    with caplog.at_level(logging.ERROR):
        task = main_module._start_supervised_background_task(
            app,
            name="recovery",
            coroutine_factory=fail_unexpectedly,
        )
        assert task is not None
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert app.state.background_jobs["recovery"]["status"] == "failed"
    assert app.state.background_jobs["recovery"]["event"] == "unexpected_exit"
    assert "background_job.unexpected_exit name=recovery" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_cancellation_is_not_reported_as_failure() -> None:
    app = _test_app()
    started = asyncio.Event()

    async def wait_forever() -> None:
        started.set()
        await asyncio.Event().wait()

    task = main_module._start_supervised_background_task(
        app,
        name="recovery",
        coroutine_factory=wait_forever,
    )
    assert task is not None
    await started.wait()

    app.state._bg_stop_event.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert app.state.background_jobs["recovery"]["status"] == "stopped"
    assert background_jobs_degraded(app) is False


@pytest.mark.asyncio
async def test_lifespan_stops_supervised_tasks_before_closing_resources(
    monkeypatch,
) -> None:
    import redis.asyncio as redis_asyncio

    import app.core.simulator.storage as simulator_storage_module
    import app.utils.security as security_module
    from app.core.simulator.runtime import runtime as simulator_runtime

    events: list[str] = []
    worker_started = asyncio.Event()

    class RedisSentinel:
        closed = False

        async def ping(self) -> None:
            events.append("redis_ping")

        async def aclose(self) -> None:
            self.closed = True
            events.append("redis_close")

    class EngineSentinel:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True
            events.append("db_dispose")

    redis_sentinel = RedisSentinel()
    engine_sentinel = EngineSentinel()
    test_app = FastAPI()

    async def resource_using_worker() -> None:
        worker_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("task_cleanup")
            assert redis_sentinel.closed is False
            assert engine_sentinel.disposed is False

    def start_background_tasks(app) -> None:
        main_module._start_supervised_background_task(
            app,
            name="integrity",
            coroutine_factory=resource_using_worker,
        )

    async def runtime_shutdown() -> None:
        events.append("runtime_shutdown")

    def set_security_client(client) -> None:
        events.append("security_set" if client is not None else "security_clear")

    monkeypatch.setattr(main_module.settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(main_module, "_sqlite_ensure_debts_version_column", AsyncMock())
    monkeypatch.setattr(main_module, "_start_configured_background_tasks", start_background_tasks)
    monkeypatch.setattr(main_module, "engine", engine_sentinel)
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *args, **kwargs: redis_sentinel)
    monkeypatch.setattr(
        simulator_storage_module,
        "reconcile_stale_runs",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(simulator_runtime, "shutdown", runtime_shutdown)
    monkeypatch.setattr(security_module, "set_redis_client", set_security_client)

    async with main_module.lifespan(test_app):
        await worker_started.wait()

    assert events.index("task_cleanup") < events.index("runtime_shutdown")
    assert events.index("task_cleanup") < events.index("redis_close")
    assert events.index("task_cleanup") < events.index("security_clear")
    assert events.index("task_cleanup") < events.index("db_dispose")
    assert test_app.state.background_jobs["integrity"] == {
        "status": "stopped",
        "event": "cancelled_for_shutdown",
    }


@pytest.mark.asyncio
async def test_clean_stop_is_not_reported_as_unexpected_exit() -> None:
    app = _test_app()

    async def stop_cleanly() -> None:
        await app.state._bg_stop_event.wait()

    task = main_module._start_supervised_background_task(
        app,
        name="integrity",
        coroutine_factory=stop_cleanly,
    )
    assert task is not None

    app.state._bg_stop_event.set()
    await task
    await asyncio.sleep(0)

    assert app.state.background_jobs["integrity"]["status"] == "stopped"
    assert app.state.background_jobs["integrity"]["event"] == "stopped"
    assert background_jobs_degraded(app) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["startup", "periodic"])
async def test_integrity_failure_degrades_and_later_success_recovers(
    monkeypatch,
    reason,
) -> None:
    app = _test_app()

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", SessionContext)
    compute = AsyncMock(side_effect=RuntimeError("checkpoint failed"))
    monkeypatch.setattr(
        integrity_module,
        "compute_and_store_integrity_checkpoints",
        compute,
    )

    completed = await main_module._run_integrity_checkpoints_once(
        app,
        reason=reason,
    )

    assert completed is False
    assert app.state.background_jobs["integrity"]["status"] == "failed"
    assert background_jobs_degraded(app) is True

    compute.side_effect = None
    completed = await main_module._run_integrity_checkpoints_once(
        app,
        reason=reason,
    )

    assert completed is True
    assert app.state.background_jobs["integrity"]["status"] == "running"
    assert background_jobs_degraded(app) is False


@pytest.mark.asyncio
async def test_root_and_versioned_health_report_background_degradation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main_module.app.state,
        "background_jobs",
        {"integrity": {"status": "failed", "event": "periodic_error"}},
        raising=False,
    )

    async with AsyncClient(app=main_module.app, base_url="http://test") as client:
        root_health = await client.get("/health")
        versioned_health = await client.get("/api/v1/health")
        root_liveness = await client.get("/healthz")
        versioned_liveness = await client.get("/api/v1/healthz")

    assert root_health.status_code == 503
    assert root_health.json()["status"] == "degraded"
    assert versioned_health.status_code == 503
    assert versioned_health.json()["status"] == "degraded"
    assert root_liveness.status_code == 200
    assert root_liveness.json() == {"status": "ok"}
    assert versioned_liveness.status_code == 200
    assert versioned_liveness.json() == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("iteration_results", "expected_statuses", "expected_events"),
    [
        ([False, True], ["failed", "running"], ["startup_error", "periodic_success"]),
        ([True, False], ["running", "failed"], ["startup_success", "periodic_error"]),
    ],
)
async def test_recovery_loop_reports_each_iteration_and_can_recover(
    monkeypatch,
    iteration_results,
    expected_statuses,
    expected_events,
) -> None:
    app = _test_app()
    stop_event = app.state._bg_stop_event
    run_once = AsyncMock(side_effect=iteration_results)
    monkeypatch.setattr(recovery_module, "run_recovery_once", run_once)

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    async def advance_to_periodic(waitable, timeout):
        del timeout
        waitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(recovery_module.asyncio, "wait_for", advance_to_periodic)

    transitions: list[dict[str, str]] = []

    def observe(reason: str, error: BaseException | None) -> None:
        main_module._record_recovery_iteration(app, reason, error)
        transitions.append(dict(app.state.background_jobs["recovery"]))
        if len(transitions) == 2:
            stop_event.set()

    await recovery_module.recovery_loop(
        session_factory=SessionContext,
        stop_event=stop_event,
        on_iteration=observe,
    )

    assert [transition["status"] for transition in transitions] == expected_statuses
    assert [transition["event"] for transition in transitions] == expected_events
    assert run_once.await_count == 2


@pytest.mark.asyncio
async def test_recovery_iteration_cancellation_is_not_reported_as_failure(
    monkeypatch,
) -> None:
    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    run_once = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(recovery_module, "run_recovery_once", run_once)
    observed: list[tuple[str, BaseException | None]] = []

    with pytest.raises(asyncio.CancelledError):
        await recovery_module._run_recovery_iteration(
            session_factory=SessionContext,
            reason="periodic",
            on_iteration=lambda reason, error: observed.append((reason, error)),
        )

    assert observed == []
