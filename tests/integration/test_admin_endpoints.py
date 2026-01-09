from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.mark.asyncio
async def test_admin_requires_token(client: AsyncClient):
    resp = await client.get("/api/v1/admin/config")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_config_ok_with_token(client: AsyncClient):
    resp = await client.get("/api/v1/admin/config", headers={"X-Admin-Token": settings.ADMIN_TOKEN})
    assert resp.status_code == 200
    payload = resp.json()
    assert "items" in payload
