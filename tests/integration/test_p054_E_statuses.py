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

import base64
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from nacl.signing import SigningKey

from app.api.deps import get_db
from app.config import settings
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import generate_keypair
from app.main import app
from app.utils.exceptions import TimeoutException
from tests.conftest import MODE_B
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


def _signed_registration(name: str) -> dict:
    public_key, private_key = generate_keypair()
    signing_key = SigningKey(base64.b64decode(private_key))
    body = {"display_name": name, "type": "person", "public_key": public_key, "profile": {}}
    signature = base64.b64encode(signing_key.sign(canonical_json(body)).signature).decode("utf-8")
    return {**body, "signature": signature}


# ---- 054-E-1 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e1_auth_challenge_for_an_unknown_pid_is_a_declared_404(client) -> None:
    response = await client.post("/api/v1/auth/challenge", json={"pid": "p054-no-such-participant"})
    _assert_declared("POST", "/auth/challenge", response, 404)
    assert response.json()["error"]["code"] == "E009"


# ---- 054-E-2 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2_registering_an_existing_participant_is_a_declared_409(client) -> None:
    body = _signed_registration("p054 E-2")
    first = await client.post("/api/v1/participants", json=body)
    assert first.status_code == 201, first.text
    again = await client.post("/api/v1/participants", json=body)
    _assert_declared("POST", "/participants", again, 409)
    assert again.json()["error"]["code"] == "E008"


# ---- 054-E-3 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e3_trustlines_list_unknown_and_malformed_equivalent_are_declared(client, auth_headers) -> None:
    unknown = await client.get("/api/v1/trustlines", headers=auth_headers, params={"equivalent": "ZZMISSING"})
    malformed = await client.get("/api/v1/trustlines", headers=auth_headers, params={"equivalent": "uah"})
    _assert_declared("GET", "/trustlines", unknown, 404)
    _assert_declared("GET", "/trustlines", malformed, 400)


# ---- 054-E-4 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e4_trustline_to_an_unknown_recipient_is_a_declared_404(client, auth_user) -> None:
    signing_key = SigningKey(base64.b64decode(auth_user["private_key"]))
    payload = {"to": "p054-no-such-recipient", "equivalent": "UAH", "limit": "10"}
    signature = base64.b64encode(signing_key.sign(canonical_json(payload)).signature).decode("utf-8")
    response = await client.post(
        "/api/v1/trustlines", headers=auth_user["headers"], json={**payload, "signature": signature}
    )
    _assert_declared("POST", "/trustlines", response, 404)


# ---- 054-E-5 (declaration half; the 500 -> 404 half is the MODE_B test at the end) ---------------------------------


@pytest.mark.asyncio
async def test_e5_clearing_malformed_equivalent_is_a_declared_400(client, auth_headers) -> None:
    cycles = await client.get("/api/v1/clearing/cycles", headers=auth_headers, params={"equivalent": "uah"})
    auto = await client.post("/api/v1/clearing/auto", headers=auth_headers, params={"equivalent": "uah"})
    _assert_declared("GET", "/clearing/cycles", cycles, 400)
    _assert_declared("POST", "/clearing/auto", auto, 400)


@pytest.mark.asyncio
async def test_e5_clearing_auto_timeout_is_a_declared_504(client, auth_headers, monkeypatch) -> None:
    """Injection: the pass raises the core's own `TimeoutException` (a clearing lock wait out of budget,
    `ClearingService`, 55P03), which the route does not catch."""

    async def timed_out(*_a, **_kw):
        raise TimeoutException("Clearing lock timed out")

    monkeypatch.setattr("app.api.v1.clearing.run_awaited_clearing", timed_out)
    response = await client.post("/api/v1/clearing/auto", headers=auth_headers, params={"equivalent": "UAH"})
    _assert_declared("POST", "/clearing/auto", response, 504)
    assert response.json()["error"]["code"] == "E007"


# ---- 054-E-6 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e6_payment_capacity_routing_timeout_is_a_declared_504(client, auth_headers, monkeypatch) -> None:
    """Injection: routing raises the core's own `TimeoutException` (`PaymentRouter.find_flow_routes` out of
    `ROUTING_PATH_FINDING_TIMEOUT_MS`)."""

    from app.core.payments.router import PaymentRouter

    def timed_out(self, *_a, **_kw):
        raise TimeoutException("Routing timed out")

    monkeypatch.setattr(PaymentRouter, "find_flow_routes", timed_out)
    response = await client.get(
        "/api/v1/payments/capacity",
        headers=auth_headers,
        params={"to": "p054-anyone", "equivalent": "UAH", "amount": "1"},
    )
    _assert_declared("GET", "/payments/capacity", response, 504)


# ---- 054-E-7 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e7_integrity_malformed_equivalent_is_a_declared_400(client) -> None:
    checksum = await client.get("/api/v1/integrity/checksum/uah", headers=ADMIN)
    verify = await client.post("/api/v1/integrity/verify", headers=ADMIN, json={"equivalent": "uah"})
    _assert_declared("GET", "/integrity/checksum/{equivalent}", checksum, 400)
    _assert_declared("POST", "/integrity/verify", verify, 400)


# ---- 054-E-8 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e8_simulator_run_of_an_unknown_scenario_is_a_declared_404(client, auth_headers) -> None:
    response = await client.post(
        "/api/v1/simulator/runs", headers=auth_headers, json={"scenario_id": "p054-no-such-scenario"}
    )
    _assert_declared("POST", "/simulator/runs", response, 404)


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


# ---- MODE_B: the clearing pass needs committed rows ------------------------------------------------------------


@MODE_B
@pytest.mark.asyncio
async def test_e5_clearing_unknown_equivalent_is_a_declared_404_not_a_500(db_session, client, auth_headers) -> None:
    """W3/W3b: an unknown equivalent is the client's error - 404/E009, as `/balance/debts` and `/trustlines` answer
    it - and not the internal 500/E010 the bare `GeoException` of `flow_planner.load_snapshot` produced."""

    cycles = await client.get("/api/v1/clearing/cycles", headers=auth_headers, params={"equivalent": "ZZMISSING"})
    auto = await client.post("/api/v1/clearing/auto", headers=auth_headers, params={"equivalent": "ZZMISSING"})
    _assert_declared("GET", "/clearing/cycles", cycles, 404)
    _assert_declared("POST", "/clearing/auto", auto, 404)
    assert cycles.json()["error"]["code"] == auto.json()["error"]["code"] == "E009"
