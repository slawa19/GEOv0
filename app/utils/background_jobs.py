from __future__ import annotations

from fastapi import FastAPI


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
