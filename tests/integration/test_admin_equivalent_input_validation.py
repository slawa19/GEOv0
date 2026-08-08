from __future__ import annotations

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
