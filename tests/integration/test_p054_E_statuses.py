"""054-E-1…E-10: a status the service really answers is declared in the canon, and its body validates against it.

Real HTTP through the app on PostgreSQL. Each test sends the request that reaches the status (no injection, except
where named: E-6 and the `/clearing/auto` 504 replace the routing or the pass with one raising the core's own
`TimeoutException`, E-10 breaks `get_db`) and asserts three things: the status is the intended one, the canon
(`api/openapi.yaml`) declares that status on that operation, and the body validates against the declared schema -
a response-level `$ref` (`components/responses/*`) is followed. Witnesses W1-W5 of the review of slice E
(`.local-run/codex-review/054-E-20261030/REPORT.md`), turned to the intended contract.

What this does NOT see: the generated document (`app.openapi()`), which `tests/contract/test_openapi_contract.py`
compares with the canon; statuses on paths no test here sends.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db
from app.config import settings
from app.main import app
from tests.contract import openapi_response_conformance as oc

ADMIN = {"X-Admin-Token": settings.ADMIN_TOKEN}
_CANON = oc.load_canon()


def _declared_schema_pointer(method: str, path: str, status: int, content_type: str | None) -> str:
    """The JSON pointer of the schema the canon declares for this operation, status and media type.

    Follows a response-level `$ref` (`#/components/responses/X`), which `classify_response_declaration` does not.
    Fails (with the reason) when the status, the media type or the schema is not declared.
    """

    operation = _CANON["paths"][path][method.lower()]
    responses = operation.get("responses") or {}
    assert str(status) in responses, (
        f"{method} {path}: status {status} is not declared in api/openapi.yaml; declared={sorted(responses)}"
    )
    pointer = f"/paths/{oc._escape(path)}/{method.lower()}/responses/{status}"
    response = responses[str(status)]
    while "$ref" in response:
        reference = response["$ref"]
        assert reference.startswith("#/components/responses/"), reference
        pointer = reference[1:]
        response = _CANON["components"]["responses"][reference.rsplit("/", 1)[1]]
    arrived = oc.normalize_media_type(content_type)
    media = next((m for m in response.get("content") or {} if oc.normalize_media_type(m) == arrived), None)
    assert media is not None, f"{method} {path} {status}: media type {arrived!r} is not declared"
    assert "schema" in response["content"][media], f"{method} {path} {status}: {media} has no schema"
    return f"{pointer}/content/{oc._escape(media)}/schema"


def _assert_declared(method: str, path: str, response: Any, status: int) -> None:
    assert response.status_code == status, (
        f"{method} {path}: actual={response.status_code}, intended={status}: {response.text[:300]}"
    )
    pointer = _declared_schema_pointer(method, path, status, response.headers.get("content-type"))
    errors = oc.validate_body(_CANON, pointer, response.json())
    assert not errors, f"{method} {path} {status}: body does not match the declared schema: {errors}"


# ---- 054-E-10 / E-9 -----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url", "canon_path", "headers"),
    [
        ("GET", "/api/v1/admin/participants", "/admin/participants", ADMIN),
        ("GET", "/api/v1/equivalents", "/equivalents", {}),
        ("POST", "/api/v1/integrity/verify", "/integrity/verify", ADMIN),
    ],
)
async def test_e10_an_unhandled_error_is_a_declared_500_envelope(method, url, canon_path, headers) -> None:
    """Injection: `get_db` raises, as in `test_p024_errors_carry_the_request_id.py` - no handler answers it, so
    `unhandled_exception_handler` (`app/main.py`) does, on any operation."""

    async def broken_db():
        raise RuntimeError("p054 E-10: the database is gone")
        yield  # pragma: no cover - a generator dependency like get_db

    app.dependency_overrides[get_db] = broken_db
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as raw:
            response = await raw.request(method, url, headers=headers, json={} if method == "POST" else None)
    finally:
        app.dependency_overrides.pop(get_db, None)
    _assert_declared(method, canon_path, response, 500)
    assert response.json()["error"]["code"] == "E010"


@pytest.mark.parametrize("action", ["payment-real", "clearing-real"])
def test_e9_e10_both_500_shapes_of_a_money_action_are_declared(action) -> None:
    """Schema-level: the two 500 bodies these actions really send - the flat `SimulatorActionError` of the handler
    (`PAYMENT_REJECTED` carrying a service E010, `test_p037_manual_payment_idempotency_postgres.py`; `CLEARING_FAILED`)
    and the `ErrorEnvelope` of `unhandled_exception_handler` for an error outside the handler's own `try` - both
    validate against what the canon declares for 500 on that operation."""

    path = f"/simulator/runs/{{run_id}}/actions/{action}"
    pointer = _declared_schema_pointer("POST", path, 500, "application/json")
    flat = {"code": "PAYMENT_REJECTED" if action == "payment-real" else "CLEARING_FAILED", "message": "m",
            "details": None}
    envelope = {"error": {"code": "E010", "message": "Internal server error", "request_id": "r"}}
    assert oc.validate_body(_CANON, pointer, flat) == [], action
    assert oc.validate_body(_CANON, pointer, envelope) == [], action
    # Anti-vacuum: the schema is not "anything" - a body of neither shape is refused.
    assert oc.validate_body(_CANON, pointer, {"detail": "Internal Server Error"}), action
