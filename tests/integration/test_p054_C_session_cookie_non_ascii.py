"""054-C-1: a `geo_sim_sid` cookie with a non-ASCII character is "no session", not a 500.

Starlette decodes request headers as latin-1, so a cookie byte >= 0x80 reaches
`app.core.simulator.session.validate_session` as a non-ASCII `str`. Before the fix the signature part went into
`hmac.compare_digest(str, str)`, which raises `TypeError: comparing strings with non-ASCII characters is not
supported`; `app.main` answered 500 E010, and since the cookie is HttpOnly the visitor could not recover before its TTL.

Intended (both call-sites of `validate_session`): an invalid cookie means "no session" —
`POST /simulator/session/ensure` issues a fresh cookie (200 + Set-Cookie, `app/api/v1/simulator.py`), and an
authenticated simulator route answers 401 (`app/api/deps.py`, cookie branch of the simulator actor resolver).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db
from app.core.simulator.session import COOKIE_NAME
from app.main import app

API = "/api/v1/simulator"
# Raw header bytes: the signature part ends with UTF-8 "é" (0xC3 0xA9), decoded by Starlette as latin-1 "Ã©".
BAD_COOKIE = b"geo_sim_sid=v1.abcdef.1700000000.\xc3\xa9"


@pytest.mark.asyncio
async def test_non_ascii_cookie_on_session_ensure_is_treated_as_invalid() -> None:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(f"{API}/session/ensure", headers=[(b"cookie", BAD_COOKIE)])
    assert r.status_code == 200, r.text
    assert r.json().get("owner_id", "").startswith("anon:"), r.text
    assert COOKIE_NAME in (r.headers.get("set-cookie") or ""), r.headers


@pytest.mark.asyncio
async def test_non_ascii_cookie_on_authenticated_route_is_401(db_session) -> None:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(f"{API}/runs/active", headers=[(b"cookie", BAD_COOKIE)])
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 401, r.text
