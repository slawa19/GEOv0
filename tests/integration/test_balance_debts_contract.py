"""Reachable balance errors must be declared by the public REST contract."""

from pathlib import Path

import pytest
import yaml

from app.main import app


@pytest.mark.asyncio
async def test_unknown_balance_equivalent_returns_declared_not_found(client, auth_headers):
    response = await client.get(
        "/api/v1/balance/debts",
        headers=auth_headers,
        params={"equivalent": "ZZMISSING", "direction": "all"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "E009"

    canon = yaml.safe_load(Path("api/openapi.yaml").read_text(encoding="utf-8"))
    declared = canon["paths"]["/balance/debts"]["get"]["responses"]
    assert declared["404"] == {"$ref": "#/components/responses/NotFound"}
    generated = app.openapi()["paths"]["/api/v1/balance/debts"]["get"]["responses"]
    assert generated["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorEnvelope"
    }
