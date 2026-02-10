import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_validation_error_envelope_returns_e009(client: AsyncClient):
    # Missing required field `pid` for ChallengeRequest -> RequestValidationError (422).
    resp = await client.post("/api/v1/auth/challenge", json={})

    assert resp.status_code == 422, resp.text
    payload = resp.json()
    assert payload["error"]["code"] == "E009"
    assert payload["error"]["message"]
    assert isinstance(payload["error"].get("details"), dict)
    assert isinstance(payload["error"]["details"].get("errors"), list)

