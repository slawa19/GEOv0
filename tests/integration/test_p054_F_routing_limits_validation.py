"""054-D-7: `ROUTING_MAX_HOPS` and `ROUTING_MAX_PATHS` refuse 0 and negative values.

A route has at least one edge and a search looks for at least one path. Before the fix both keys took 0 and negatives
at start and through `PATCH /admin/config`; the payment then read 0 as the default (`or 6` / `or 3`) while
`/payments/capacity` passed it to the router as is and answered "cannot pay". The admin path is checked through HTTP
on the real application: the refusal, the unchanged visible value and the absence of an audit row.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import Settings, settings
from app.db.models.audit_log import AuditLog

ROUTING_KEYS = ("ROUTING_MAX_HOPS", "ROUTING_MAX_PATHS")


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _visible_config(client) -> dict[str, object]:
    response = await client.get("/api/v1/admin/config", headers=_admin_headers())
    assert response.status_code == 200, response.text
    return {item["key"]: item["value"] for item in response.json()["items"]}


async def _config_patch_audit_rows(db_session) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "admin.config.patch")
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates, refused_key",
    [
        ({"ROUTING_MAX_HOPS": 0}, "ROUTING_MAX_HOPS"),
        ({"ROUTING_MAX_HOPS": -1}, "ROUTING_MAX_HOPS"),
        ({"ROUTING_MAX_PATHS": 0}, "ROUTING_MAX_PATHS"),
        ({"ROUTING_MAX_PATHS": -1}, "ROUTING_MAX_PATHS"),
        # A batch is all or nothing: the valid hop limit is not applied either.
        ({"ROUTING_MAX_HOPS": 4, "ROUTING_MAX_PATHS": -1}, "ROUTING_MAX_PATHS"),
    ],
)
async def test_admin_config_patch_refuses_non_positive_routing_limit(
    client, db_session, monkeypatch, updates, refused_key
) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_HOPS", 6)
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    audit_before = await _config_patch_audit_rows(db_session)

    response = await client.patch(
        "/api/v1/admin/config",
        headers=_admin_headers(),
        json={"updates": updates, "reason": "p054-F-non-positive"},
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "E009"
    assert error["message"] == f"Invalid value for config key: {refused_key}"
    assert error["details"] == {"key": refused_key}
    assert (settings.ROUTING_MAX_HOPS, settings.ROUTING_MAX_PATHS) == (6, 3)
    visible = await _visible_config(client)
    assert (visible["ROUTING_MAX_HOPS"], visible["ROUTING_MAX_PATHS"]) == (6, 3)
    assert await _config_patch_audit_rows(db_session) == audit_before


@pytest.mark.asyncio
async def test_admin_config_patch_accepts_routing_limit_of_one(client, db_session, monkeypatch) -> None:
    # Counter-check: the bound is `>= 1`, so the smallest meaningful limit still goes through.
    monkeypatch.setattr(settings, "ROUTING_MAX_HOPS", 6)
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    audit_before = await _config_patch_audit_rows(db_session)

    response = await client.patch(
        "/api/v1/admin/config",
        headers=_admin_headers(),
        json={"updates": {"ROUTING_MAX_HOPS": 1, "ROUTING_MAX_PATHS": 1}, "reason": "p054-F-one"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": ["ROUTING_MAX_HOPS", "ROUTING_MAX_PATHS"]}
    assert (settings.ROUTING_MAX_HOPS, settings.ROUTING_MAX_PATHS) == (1, 1)
    visible = await _visible_config(client)
    assert (visible["ROUTING_MAX_HOPS"], visible["ROUTING_MAX_PATHS"]) == (1, 1)
    assert await _config_patch_audit_rows(db_session) == audit_before + 1


@pytest.mark.parametrize("key", ROUTING_KEYS)
@pytest.mark.parametrize("value", [0, -1])
def test_settings_refuse_non_positive_routing_limit(key, value) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(**{key: value})
    assert [error["loc"] for error in excinfo.value.errors()] == [(key,)]


@pytest.mark.parametrize("key", ROUTING_KEYS)
def test_settings_refuse_non_positive_routing_limit_from_environment(key, monkeypatch) -> None:
    # The start-up path: the value comes from the environment as text.
    monkeypatch.setenv(key, "0")
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    assert [error["loc"] for error in excinfo.value.errors()] == [(key,)]


@pytest.mark.parametrize("key", ROUTING_KEYS)
def test_settings_accept_routing_limit_of_one_from_environment(key, monkeypatch) -> None:
    monkeypatch.setenv(key, "1")
    assert getattr(Settings(), key) == 1
