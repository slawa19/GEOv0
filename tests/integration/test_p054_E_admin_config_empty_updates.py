"""054-E-16 (the `/admin/config` part): `PATCH /admin/config {"updates": {}}` changes nothing and must say so.

Before: 054-D-9 made `updates` required, but an EMPTY `updates` still answered 200 `{"updated": []}` and wrote an
`admin.config.patch` audit row about nothing. The canon now says `minProperties: 1` and the request model
`min_length=1`, so it is a declared 422 E009 naming `updates`, with no audit row. A non-empty `updates` behaves as
before (counter-check). Checked through HTTP on the real application.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.models.audit_log import AuditLog

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


async def _config_patch_audit_rows(db_session) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "admin.config.patch")
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_an_empty_updates_is_a_422_and_writes_no_audit_row(client, db_session) -> None:
    audit_before = await _config_patch_audit_rows(db_session)

    response = await client.patch(
        "/api/v1/admin/config", headers=ADMIN, json={"updates": {}, "reason": "p054-E-empty"}
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "E009", error
    assert any(detail["loc"][-1] == "updates" for detail in error["details"]["errors"]), error
    assert await _config_patch_audit_rows(db_session) == audit_before


@pytest.mark.asyncio
async def test_a_non_empty_updates_still_applies(client, db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ROUTING_MAX_PATHS", 3)
    audit_before = await _config_patch_audit_rows(db_session)

    response = await client.patch(
        "/api/v1/admin/config", headers=ADMIN, json={"updates": {"ROUTING_MAX_PATHS": 2}, "reason": "p054-E-one"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": ["ROUTING_MAX_PATHS"]}
    assert settings.ROUTING_MAX_PATHS == 2
    assert await _config_patch_audit_rows(db_session) == audit_before + 1
