"""Background jobs of the hub process: their recorded state, the health they imply, and the supervisor that
starts them and records how they end. Moved here from `app/main.py` unchanged (024 `T2414.2`)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def background_job_states(app: FastAPI) -> dict[str, dict[str, str]]:
    states = getattr(app.state, "background_jobs", None)
    if not isinstance(states, dict):
        states = {}
        app.state.background_jobs = states
    return states


def background_jobs_degraded(app: FastAPI) -> bool:
    return any(
        state.get("status") == "failed"
        for state in background_job_states(app).values()
    )


def background_health_status(app: FastAPI) -> str:
    return "degraded" if background_jobs_degraded(app) else "ok"


def _record_background_job_event(
    app: FastAPI,
    *,
    name: str,
    status: str,
    event: str,
    error: BaseException | None = None,
) -> None:
    state = {"status": status, "event": event}
    if error is not None:
        state["error_type"] = type(error).__name__
    background_job_states(app)[name] = state

    try:
        from app.utils.metrics import BACKGROUND_JOB_EVENTS_TOTAL

        BACKGROUND_JOB_EVENTS_TOTAL.labels(job=name, event=event).inc()
    except Exception:
        # Job state remains authoritative even if metrics collection is degraded.
        logger.debug(
            "background_job.metric_failed name=%s event=%s",
            name,
            event,
            exc_info=True,
        )


def _on_background_task_done(app: FastAPI, name: str, task: asyncio.Task) -> None:
    stop_event = getattr(app.state, "_bg_stop_event", None)
    stopping = bool(stop_event is not None and stop_event.is_set())

    if task.cancelled():
        if stopping:
            _record_background_job_event(
                app,
                name=name,
                status="stopped",
                event="cancelled_for_shutdown",
            )
        else:
            error = RuntimeError("background task was cancelled unexpectedly")
            _record_background_job_event(
                app,
                name=name,
                status="failed",
                event="unexpected_exit",
                error=error,
            )
            logger.error("background_job.unexpected_exit name=%s cancelled=true", name)
        return

    error = task.exception()
    if error is None and stopping:
        _record_background_job_event(
            app,
            name=name,
            status="stopped",
            event="stopped",
        )
        return

    if error is None:
        error = RuntimeError("background task exited before shutdown")

    _record_background_job_event(
        app,
        name=name,
        status="failed",
        event="unexpected_exit",
        error=error,
    )
    logger.error(
        "background_job.unexpected_exit name=%s",
        name,
        exc_info=(type(error), error, error.__traceback__),
    )


def _start_supervised_background_task(
    app: FastAPI,
    *,
    name: str,
    coroutine_factory: Callable[[], Awaitable[None]],
) -> asyncio.Task | None:
    coroutine: Awaitable[None] | None = None
    _record_background_job_event(
        app,
        name=name,
        status="starting",
        event="starting",
    )
    try:
        coroutine = coroutine_factory()
        task = asyncio.create_task(coroutine, name=f"geo:{name}")
    except Exception as error:
        if inspect.iscoroutine(coroutine):
            coroutine.close()
        _record_background_job_event(
            app,
            name=name,
            status="failed",
            event="start_failed",
            error=error,
        )
        logger.exception("background_job.start_failed name=%s", name)
        return None

    app.state._bg_tasks.append(task)
    _record_background_job_event(
        app,
        name=name,
        status="running",
        event="started",
    )
    task.add_done_callback(
        lambda completed, job_name=name: _on_background_task_done(
            app,
            job_name,
            completed,
        )
    )
    return task
