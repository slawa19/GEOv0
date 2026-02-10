from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.main import request_id_middleware
from app.utils.request_id import validate_request_id


def _make_request(*, raw_headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/healthz",
        "raw_path": b"/healthz",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_request_id_invalid_header_is_ignored_and_regenerated():
    injected = "\nINJECT"
    request = _make_request(raw_headers=[(b"x-request-id", injected.encode("latin-1"))])

    async def call_next(_: Request) -> Response:
        return Response("ok")

    response = await request_id_middleware(request, call_next)

    reflected = response.headers.get("X-Request-ID")
    assert reflected is not None

    # Invalid header MUST NOT be reflected back.
    assert reflected != injected

    # Response must always contain a valid/safe request id.
    assert validate_request_id(reflected) == reflected


@pytest.mark.asyncio
async def test_request_id_valid_header_is_reflected_unchanged():
    provided = "abcDEF-0123._"
    request = _make_request(raw_headers=[(b"x-request-id", provided.encode("ascii"))])

    async def call_next(_: Request) -> Response:
        return Response("ok")

    response = await request_id_middleware(request, call_next)

    assert response.headers.get("X-Request-ID") == provided

