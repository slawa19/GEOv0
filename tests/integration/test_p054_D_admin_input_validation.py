"""054-D-8 / 054-E-11 and 054-D-9: admin request bodies are validated to the canon before they reach the DB.

- ``symbol`` is stored in ``equivalents.symbol String(16)``: a longer value used to pass the schema and fail in
  PostgreSQL (22001 -> ``DataError`` -> 500 E010).  It must be a declared 422 E009 naming ``symbol``; exactly 16
  characters must still be accepted (the boundary, not a blanket refusal).
- ``PATCH /admin/config`` requires ``updates`` (``api/openapi.yaml``, ``AdminConfigPatchRequest.required``): an
  empty body used to answer 200 with an empty audit row.  A body with ``updates`` behaves as before.
"""

from __future__ import annotations

import pytest

from app.config import settings

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}


def _assert_rejected(response, field: str) -> None:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "E009", error
    assert any(detail["loc"][-1] == field for detail in error["details"]["errors"]), error


@pytest.mark.asyncio
async def test_p054_d8_admin_equivalent_symbol_longer_than_the_column_is_a_422(client) -> None:
    too_long, at_limit = "S" * 17, "S" * 16

    created_long = await client.post(
        "/api/v1/admin/equivalents", headers=ADMIN, json={"code": "P054DLONG", "precision": 2, "symbol": too_long}
    )
    _assert_rejected(created_long, "symbol")
    listed = await client.get("/api/v1/admin/equivalents", headers=ADMIN, params={"include_inactive": True})
    assert listed.status_code == 200, listed.text
    assert "P054DLONG" not in {e["code"] for e in listed.json()["items"]}, "a rejected create must not store a row"

    created = await client.post(
        "/api/v1/admin/equivalents", headers=ADMIN, json={"code": "P054DSYM", "precision": 2, "symbol": at_limit}
    )
    assert created.status_code == 200, created.text
    assert created.json()["symbol"] == at_limit

    patched_long = await client.patch("/api/v1/admin/equivalents/P054DSYM", headers=ADMIN, json={"symbol": too_long})
    _assert_rejected(patched_long, "symbol")

    patched = await client.patch("/api/v1/admin/equivalents/P054DSYM", headers=ADMIN, json={"symbol": "T" * 16})
    assert patched.status_code == 200, patched.text
    assert patched.json()["symbol"] == "T" * 16


@pytest.mark.asyncio
async def test_p054_d9_admin_config_patch_requires_updates(client) -> None:
    empty = await client.patch("/api/v1/admin/config", headers=ADMIN, json={})
    _assert_rejected(empty, "updates")

    original = settings.ROUTING_MAX_PATHS
    try:
        response = await client.patch(
            "/api/v1/admin/config", headers=ADMIN, json={"updates": {"ROUTING_MAX_PATHS": original}, "reason": "p054"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["updated"] == ["ROUTING_MAX_PATHS"]
    finally:
        settings.ROUTING_MAX_PATHS = original
