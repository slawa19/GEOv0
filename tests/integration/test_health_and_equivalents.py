import base64

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

import app.main as main_module
from app.api.v1 import health as health_module
from app.config import settings
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.db.models.equivalent import Equivalent


async def _register_and_login(client: AsyncClient, name: str) -> dict:
    pub, priv = generate_keypair()

    signing_key = SigningKey(base64.b64decode(priv))
    reg_message = canonical_json(
        {
            "display_name": name,
            "type": "person",
            "public_key": pub,
            "profile": {},
        }
    )
    reg_sig_b64 = base64.b64encode(signing_key.sign(reg_message).signature).decode("utf-8")

    reg_data = {
        "display_name": name,
        "type": "person",
        "public_key": pub,
        "signature": reg_sig_b64,
        "profile": {},
    }
    resp = await client.post("/api/v1/participants", json=reg_data)
    assert resp.status_code == 201, resp.text

    pid = resp.json()["pid"]

    resp = await client.post("/api/v1/auth/challenge", json={"pid": pid})
    assert resp.status_code == 200, resp.text
    challenge_str = resp.json()["challenge"]

    signature_b64 = base64.b64encode(signing_key.sign(challenge_str.encode("utf-8")).signature).decode(
        "utf-8"
    )

    login_data = {"pid": pid, "challenge": challenge_str, "signature": signature_b64}
    resp = await client.post("/api/v1/auth/login", json=login_data)
    assert resp.status_code == 200, resp.text
    tokens = resp.json()

    return {"headers": {"Authorization": f"Bearer {tokens['access_token']}"}}


@pytest.mark.asyncio
async def test_health_endpoints(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get("/healthz")
    assert resp.status_code == 200

    # /api/v1 health aliases exist for clients that use the API base URL.
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200

    resp = await client.get("/api/v1/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get("/api/v1/health/db")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get(
        "/api/v1/admin/health/db",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


class _FailingConnectionContext:
    def __init__(self, message: str) -> None:
        self.message = message

    async def __aenter__(self):
        raise RuntimeError(self.message)

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FailingEngine:
    def __init__(self, message: str) -> None:
        self.message = message

    def connect(self) -> _FailingConnectionContext:
        return _FailingConnectionContext(self.message)


@pytest.mark.asyncio
async def test_public_db_health_hides_exception_details(
    client: AsyncClient,
    monkeypatch,
):
    leaked_details = "postgresql+asyncpg://private_user@db.internal/private_database"
    failing_engine = _FailingEngine(leaked_details)
    monkeypatch.setattr(main_module, "engine", failing_engine)
    monkeypatch.setattr(health_module, "engine", failing_engine)

    for path in ("/health/db", "/api/v1/health/db"):
        public_response = await client.get(path)
        assert public_response.status_code == 503
        assert public_response.json() == {
            "status": "error",
            "db": {"reachable": False, "latency_ms": None},
            "timestamp": public_response.json()["timestamp"],
        }
        assert leaked_details not in public_response.text


@pytest.mark.asyncio
async def test_db_health_diagnostic_requires_admin(
    client: AsyncClient,
    monkeypatch,
):
    leaked_details = "postgresql+asyncpg://private_user@db.internal/private_database"
    failing_engine = _FailingEngine(leaked_details)
    monkeypatch.setattr(health_module, "engine", failing_engine)

    unauthenticated_diagnostic = await client.get("/api/v1/admin/health/db")
    assert unauthenticated_diagnostic.status_code == 422
    assert leaked_details not in unauthenticated_diagnostic.text

    invalid_diagnostic = await client.get(
        "/api/v1/admin/health/db",
        headers={"X-Admin-Token": "invalid"},
    )
    assert invalid_diagnostic.status_code == 403

    diagnostic_response = await client.get(
        "/api/v1/admin/health/db",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN},
    )
    assert diagnostic_response.status_code == 503
    assert diagnostic_response.json()["details"] == leaked_details


@pytest.mark.asyncio
async def test_equivalents_list(client: AsyncClient, db_session):
    # Ensure USD exists.
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    auth = await _register_and_login(client, "Equivalents_User")

    resp = await client.get("/api/v1/equivalents", headers=auth["headers"])
    assert resp.status_code == 200, resp.text

    payload = resp.json()
    assert "items" in payload
    codes = {item["code"] for item in payload["items"]}
    assert "USD" in codes
