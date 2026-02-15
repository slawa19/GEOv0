import importlib
import os

from fastapi import FastAPI
import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_simulator_actions_endpoints_disabled_returns_403_actions_disabled(client, monkeypatch):
    """When SIMULATOR_ACTIONS_ENABLE is off, any /actions/* endpoint must fail fast with a stable error envelope."""

    monkeypatch.delenv("SIMULATOR_ACTIONS_ENABLE", raising=False)

    # Use admin token to bypass participant auth so the test is only about the feature flag.
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    r = await client.get(
        "/api/v1/simulator/runs/test-run/actions/participants-list",
        headers=headers,
    )
    assert r.status_code == 403

    body = r.json()
    assert body.get("code") == "ACTIONS_DISABLED"
    assert isinstance(body.get("message"), str) and body.get("message")
    assert body.get("details", {}).get("env") == "SIMULATOR_ACTIONS_ENABLE"


def test_simulator_actions_openapi_visibility_follows_simulator_actions_enable(monkeypatch):
    """Action endpoints are registered but must be hidden from OpenAPI when the flag is off."""

    import app.api.v1.simulator as simulator_module

    action_path = "/api/v1/simulator/runs/{run_id}/actions/participants-list"

    orig = os.environ.get("SIMULATOR_ACTIONS_ENABLE")
    try:
        # Disabled: actions must not show up in schema.
        monkeypatch.delenv("SIMULATOR_ACTIONS_ENABLE", raising=False)
        importlib.reload(simulator_module)
        app_disabled = FastAPI()
        app_disabled.include_router(simulator_module.router, prefix="/api/v1")
        paths_disabled = set((app_disabled.openapi() or {}).get("paths", {}).keys())
        assert action_path not in paths_disabled

        # Enabled: actions must be present in schema.
        monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
        importlib.reload(simulator_module)
        app_enabled = FastAPI()
        app_enabled.include_router(simulator_module.router, prefix="/api/v1")
        paths_enabled = set((app_enabled.openapi() or {}).get("paths", {}).keys())
        assert action_path in paths_enabled
    finally:
        # Best-effort restore to avoid leaking a reloaded module with a different include_in_schema.
        if orig is None:
            monkeypatch.delenv("SIMULATOR_ACTIONS_ENABLE", raising=False)
        else:
            monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", orig)
        importlib.reload(simulator_module)

