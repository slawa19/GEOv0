"""Programme 024, `T2414.2` (F-024-8): every error a caller sees can be found in the log by its request id.

AGENTS.md §12: "каждая пользовательская ошибка несёт идентификатор для корреляции". The request id is the
`X-Request-ID` the middleware accepts or generates (`docs/ru/04-api-reference.md` §1.6, "присутствует всегда").
Before this slice it reached neither the error envelope nor - for an unhandled exception - the response at all:
the 500 was Starlette's plain text, without the envelope and without the header. The login audit row recorded
the raw incoming header rather than the id the response carries.
"""

from __future__ import annotations

import base64
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from nacl.signing import SigningKey
from sqlalchemy import select

from app.api.deps import get_db
from app.config import settings
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.db.models.audit_log import AuditLog
from app.main import app
from tests.p019_support import TargetMismatch, require_target

_RED = pytest.mark.xfail(raises=TargetMismatch, strict=True, reason="024 T2414.2: request id not in the error")
_RID = "p024-t2414-rid"


@_RED
@pytest.mark.asyncio
async def test_a_refusal_envelope_carries_the_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/config", headers={"X-Admin-Token": "wrong", "X-Request-ID": _RID})
    assert response.status_code == 403, response.text
    assert response.headers["X-Request-ID"] == _RID
    assert response.json()["error"]["code"] == "E006"
    require_target(response.json()["error"].get("request_id") == _RID, f"envelope: {response.json()}")


@_RED
@pytest.mark.asyncio
async def test_a_validation_envelope_carries_the_request_id(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/challenge", json={}, headers={"X-Request-ID": _RID})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "E009"
    require_target(response.json()["error"].get("request_id") == _RID, f"envelope: {response.json()}")


@_RED
@pytest.mark.asyncio
async def test_an_unhandled_error_is_an_envelope_with_the_request_id_and_is_logged_under_it(caplog) -> None:
    detail = "driver said something about db.internal"

    async def broken_db():
        raise RuntimeError(detail)
        yield  # pragma: no cover - makes this a generator dependency like get_db

    app.dependency_overrides[get_db] = broken_db
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with caplog.at_level(logging.ERROR):
                response = await client.get(
                    "/api/v1/admin/participants",
                    headers={"X-Admin-Token": settings.ADMIN_TOKEN, "X-Request-ID": _RID},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500
    assert detail not in response.text
    logged = [r for r in caplog.records if r.levelno >= logging.ERROR and _RID in r.getMessage()]
    is_envelope = response.headers.get("content-type", "").startswith("application/json")
    require_target(
        is_envelope
        and response.json() == {"error": {"code": "E010", "message": "Internal server error", "request_id": _RID}}
        and response.headers.get("X-Request-ID") == _RID
        and len(logged) == 1
        and logged[0].exc_info is not None,
        f"500: headers={dict(response.headers)} body={response.text!r} logged={[r.getMessage() for r in logged]}",
    )


@_RED
@pytest.mark.asyncio
async def test_the_login_audit_records_the_request_id_the_response_carries(client: AsyncClient, db_session) -> None:
    public, private = generate_keypair()
    signing_key = SigningKey(base64.b64decode(private))
    registration = {"display_name": "Rid", "type": "person", "public_key": public, "profile": {}}
    registration["signature"] = base64.b64encode(signing_key.sign(canonical_json(registration)).signature).decode()
    response = await client.post("/api/v1/participants", json=registration)
    assert response.status_code == 201, response.text
    pid = response.json()["pid"]
    challenge = (await client.post("/api/v1/auth/challenge", json={"pid": pid})).json()["challenge"]
    signature = base64.b64encode(signing_key.sign(challenge.encode()).signature).decode()

    # Not a valid request id (spaces, `!`): the middleware replaces it, and the response carries the replacement.
    response = await client.post(
        "/api/v1/auth/login",
        json={"pid": pid, "challenge": challenge, "signature": signature},
        headers={"X-Request-ID": "not a valid id!"},
    )
    assert response.status_code == 200, response.text
    carried = response.headers["X-Request-ID"]
    assert carried != "not a valid id!"

    rows = (await db_session.execute(select(AuditLog).where(AuditLog.action == "auth.login", AuditLog.object_id == pid))).scalars().all()
    assert len(rows) == 1
    require_target(rows[0].request_id == carried, f"audit request_id={rows[0].request_id!r}, response carries {carried!r}")
