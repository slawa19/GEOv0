from __future__ import annotations

from datetime import datetime

import pytest

from app.config import settings


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": settings.ADMIN_TOKEN}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "field"),
    [
        (
            "POST",
            "/api/v1/admin/equivalents",
            {"code": "lowercase", "precision": 2},
            "code",
        ),
        (
            "POST",
            "/api/v1/admin/equivalents",
            {"code": "BAD-", "precision": 2},
            "code",
        ),
        (
            "POST",
            "/api/v1/admin/equivalents",
            {"code": "VALID", "precision": 19},
            "precision",
        ),
        (
            "PATCH",
            "/api/v1/admin/equivalents/VALID",
            {"precision": 19},
            "precision",
        ),
    ],
)
async def test_admin_equivalent_mutations_reject_values_outside_openapi_bounds(
    client,
    method: str,
    path: str,
    payload: dict[str, object],
    field: str,
) -> None:
    response = await client.request(
        method,
        path,
        headers=_admin_headers(),
        json=payload,
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "E009"
    assert any(detail["loc"][-1] == field for detail in error["details"]["errors"])


def _assert_rfc3339_offset(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() is not None


@pytest.mark.asyncio
async def test_admin_equivalent_mutation_responses_attach_utc_to_sqlite_timestamps(
    client,
) -> None:
    responses = [
        await client.post(
            "/api/v1/admin/equivalents",
            headers=_admin_headers(),
            json={"code": "TZTEST", "precision": 2},
        ),
        await client.patch(
            "/api/v1/admin/equivalents/TZTEST",
            headers=_admin_headers(),
            json={"precision": 3},
        ),
        await client.patch(
            "/api/v1/admin/equivalents/TZTEST",
            headers=_admin_headers(),
            json={"is_active": False},
        ),
    ]

    for response in responses:
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_rfc3339_offset(payload["created_at"])
        _assert_rfc3339_offset(payload["updated_at"])
