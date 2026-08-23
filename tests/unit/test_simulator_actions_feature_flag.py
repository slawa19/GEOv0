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


def test_simulator_actions_are_documented_regardless_of_the_feature_flag() -> None:
    """Interact Mode operations are published; the flag gates execution, not documentation.

    Until 2026-08-23 these eight routes carried `include_in_schema=_actions_enabled()`, so the
    schema hid them whenever the flag was off.  That hid nothing from a caller - the routes are
    registered and reachable in every deployment, and the guard above answers 403
    ACTIONS_DISABLED on its own - it only hid them from the canon and from the contract gate,
    which is how five money-moving operations came to have no contract check at all.

    Published by 011/T1101 after the owner referred the decision to external review.  The flag
    keeps its real job, asserted by the test above: execution still refuses when it is off.
    """

    import app.api.v1.simulator as simulator_module

    action_paths = {
        "/api/v1/simulator/runs/{run_id}/actions/trustline-create",
        "/api/v1/simulator/runs/{run_id}/actions/trustline-update",
        "/api/v1/simulator/runs/{run_id}/actions/trustline-close",
        "/api/v1/simulator/runs/{run_id}/actions/payment-real",
        "/api/v1/simulator/runs/{run_id}/actions/clearing-real",
        "/api/v1/simulator/runs/{run_id}/actions/participants-list",
        "/api/v1/simulator/runs/{run_id}/actions/trustlines-list",
        "/api/v1/simulator/runs/{run_id}/payment-targets",
    }

    for value in (None, "1"):
        # include_in_schema is evaluated at import, so the flag is exercised by reloading the
        # module rather than by monkeypatching the environment of an already-imported one.
        if value is None:
            os.environ.pop("SIMULATOR_ACTIONS_ENABLE", None)
        else:
            os.environ["SIMULATOR_ACTIONS_ENABLE"] = value
        importlib.reload(simulator_module)
        app = FastAPI()
        app.include_router(simulator_module.router, prefix="/api/v1")
        published = set((app.openapi() or {}).get("paths", {}).keys())
        missing = action_paths - published
        assert not missing, (
            f"Interact Mode operations missing from the schema with "
            f"SIMULATOR_ACTIONS_ENABLE={value!r}: {sorted(missing)}"
        )

    os.environ.pop("SIMULATOR_ACTIONS_ENABLE", None)
    importlib.reload(simulator_module)
