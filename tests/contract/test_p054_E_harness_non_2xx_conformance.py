"""054-E-H1/H2: counter-checks for the non-2xx half of the response-conformance harness.

`tests/contract/openapi_response_conformance.py` used to return on the first line of
`Harness.record` for any non-2xx, and its classifier read a response-level `$ref`
(`{"$ref": "#/components/responses/NotFound"}`) as a response with no body. Both are fixed there;
these tests are the anti-vacuum half (AGENTS.md section 9): each one fails if the corresponding
part of the rule is rolled back.

The real 404 comes from the application, through the suite's `client`, by
`client.send(client.build_request(...))` rather than `client.get(...)`: the session-wide wrapper
patches `AsyncClient.request`, and these counter-check requests must not add to the live
harness's counters (`non_2xx_seen`, `unrouted`) that the orchestrator compares with the
prototype's full-tier measurement. Every harness below is a fresh one over a named document.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from tests.contract.openapi_response_conformance import (
    DECL_SCHEMA,
    Harness,
    classify_response_declaration,
    declared_media_types,
    load_canon,
    response_schema_pointer,
)
from tests.contract.test_p011_responses_conform_to_the_canon import (
    _assert_the_session_is_conformant,
    _recorded,
    _response,
)

# A canonical operation whose 404 the canon declares ONLY through a response-level `$ref`.
_PATH = "/balance/debts"
_NOT_FOUND_SCHEMA = "/components/responses/NotFound/content/application~1json/schema"


async def _real_not_found(client: AsyncClient, auth_headers: dict[str, str]) -> httpx.Response:
    """A real 404 from the application on `GET /balance/debts` (unknown equivalent)."""

    response = await client.send(
        client.build_request(
            "GET",
            f"/api/v1{_PATH}",
            headers=auth_headers,
            params={"equivalent": "ZZMISSING", "direction": "all"},
        )
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith("application/json"), response.headers
    assert isinstance(response.json().get("error"), dict), response.text
    return response


def _canon_without_the_404() -> dict[str, Any]:
    document = copy.deepcopy(load_canon())
    del document["paths"][_PATH]["get"]["responses"]["404"]
    return document


def _non_2xx_counts(harness: Harness) -> tuple[int, int, int, int]:
    report = harness.report()
    return (
        report["non_2xx_seen"],
        report["non_2xx_checked"],
        report["non_2xx_undeclared"],
        report["unrouted"],
    )


# (1) An undeclared non-2xx status on a canonical path fails the harness.


async def test_an_undeclared_error_status_on_a_canonical_path_fails_the_aggregate(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await _real_not_found(client, auth_headers)

    harness = _recorded(_canon_without_the_404(), response)

    assert _non_2xx_counts(harness) == (1, 0, 1, 0)
    findings = harness.non_conformance()
    assert [(row["operation"], row["node"]) for row in findings] == [(f"GET {_PATH} 404", "$")]
    assert "does not declare that status" in findings[0]["message"], findings
    # A FINDING, not a category: a category could be written into UNVALIDATED_2XX_ALLOWANCE.
    assert harness.unvalidated_keys() == []
    assert harness.two_xx_seen == 0 and harness.observed == set() and harness.bodies_checked == 0

    with pytest.raises(AssertionError, match="do not validate against api/openapi.yaml"):
        _assert_the_session_is_conformant(harness.report())


def test_a_405_for_a_method_the_canon_does_not_declare_on_a_canonical_path_is_a_finding() -> None:
    """A canonical PATH is enough to make it a finding; `unrouted` is for unknown paths only."""

    harness = _recorded(
        load_canon(),
        _response("DELETE", "/api/v1/health", 405, content=b'{"detail":"Method Not Allowed"}'),
    )

    assert _non_2xx_counts(harness) == (1, 0, 1, 0)
    assert [row["operation"] for row in harness.non_conformance()] == ["DELETE /health 405"]
    assert "does not declare this METHOD" in harness.non_conformance()[0]["message"]


# (2) + (5) A real 404 on a canonical path is checked, not unrouted, through the `$ref`.


async def test_a_real_404_on_a_canonical_path_is_validated_through_the_response_ref(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    canon = load_canon()
    assert canon["paths"][_PATH]["get"]["responses"]["404"] == {
        "$ref": "#/components/responses/NotFound"
    }, "the premise of this test: the 404 is declared only through a response-level $ref"

    # The three readers, on the document alone and on the media type that arrived.
    assert classify_response_declaration(canon, "GET", _PATH, 404, "application/json") == (
        DECL_SCHEMA,
        _NOT_FOUND_SCHEMA,
    )
    assert response_schema_pointer(canon, "GET", _PATH, 404) == _NOT_FOUND_SCHEMA
    assert declared_media_types(canon, "GET", _PATH, 404) == ["application/json"]

    response = await _real_not_found(client, auth_headers)
    harness = _recorded(canon, response)

    assert _non_2xx_counts(harness) == (1, 1, 0, 0), harness.report()
    assert harness.report()["unrouted_detail"] == []
    assert harness.non_conformance() == [], harness.non_conformance()
    assert harness.recorder_errors == [], harness.recorder_errors
    # Coverage and the vacuity counters keep their 2xx meaning.
    assert harness.observed == set() and harness.bodies_checked == 0 and harness.two_xx_seen == 0
    _assert_the_session_is_conformant(harness.report())


def test_a_404_on_a_templated_canonical_path_is_not_unrouted() -> None:
    harness = _recorded(
        load_canon(),
        _response(
            "GET",
            "/api/v1/participants/p_nobody",
            404,
            content=b'{"error":{"code":"E001","message":"Not found"}}',
        ),
    )

    assert _non_2xx_counts(harness) == (1, 1, 0, 0), harness.report()
    assert harness.non_conformance() == []


# (3) A non-canonical path is `unrouted`, not a finding.


async def test_a_non_2xx_on_a_path_the_canon_does_not_declare_is_unrouted_not_a_finding(
    client: AsyncClient,
) -> None:
    response = await client.send(client.build_request("GET", "/api/v1/nothing/like/this"))
    assert response.status_code == 404, response.text

    harness = _recorded(load_canon(), response)

    assert _non_2xx_counts(harness) == (1, 0, 0, 1)
    assert harness.report()["unrouted_detail"] == ["GET /api/v1/nothing/like/this 404"]
    assert harness.non_conformance() == []
    assert harness.unvalidated_keys() == []
    _assert_the_session_is_conformant(harness.report())


# (4) A non-2xx body that does not match the declared schema is a finding.


async def test_a_non_2xx_body_that_does_not_match_its_schema_is_a_finding(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # The real canon over a framework-shaped body: `{"detail": ...}` is not an ErrorEnvelope.
    harness = _recorded(
        load_canon(),
        _response("GET", f"/api/v1{_PATH}", 404, content=b'{"detail":"Not Found"}'),
    )
    assert _non_2xx_counts(harness) == (1, 1, 0, 0)
    assert [(row["operation"], row["node"]) for row in harness.non_conformance()] == [
        (f"GET {_PATH} 404", "$")
    ]
    assert "'error' is a required property" in harness.non_conformance()[0]["message"]
    with pytest.raises(AssertionError, match="do not validate against api/openapi.yaml"):
        _assert_the_session_is_conformant(harness.report())

    # The real body over a sabotaged component schema: the `$ref` target is what is validated.
    sabotaged = copy.deepcopy(load_canon())
    sabotaged["components"]["responses"]["NotFound"]["content"]["application/json"]["schema"] = {
        "type": "object",
        "required": ["detail"],
        "properties": {"detail": {"type": "string"}},
    }
    caught = _recorded(sabotaged, await _real_not_found(client, auth_headers))
    assert [row["message"] for row in caught.non_conformance()] == [
        "'detail' is a required property"
    ], caught.non_conformance()


def test_a_non_2xx_body_that_is_not_json_is_a_named_category_that_fails_the_aggregate() -> None:
    """A text/plain 500-style body where JSON is declared is not silently accepted either."""

    harness = _recorded(
        load_canon(),
        _response("GET", f"/api/v1{_PATH}", 404, content=b"Not Found", content_type="text/plain"),
    )
    assert harness.unvalidated_keys() == [f"body-was-not-json GET {_PATH} 404"]
    with pytest.raises(AssertionError, match=f"body-was-not-json GET {_PATH} 404"):
        _assert_the_session_is_conformant(harness.report())


# (6) A status matched only by `default` is allowed and marked.


async def test_a_status_covered_only_by_default_is_allowed_and_marked(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    document = _canon_without_the_404()
    document["paths"][_PATH]["get"]["responses"]["default"] = {
        "$ref": "#/components/responses/NotFound"
    }
    response = await _real_not_found(client, auth_headers)

    marked = _recorded(document, response)
    assert marked.report()["matched_by_default"] == [f"GET {_PATH} 404"]
    assert _non_2xx_counts(marked) == (1, 1, 0, 0)
    assert marked.non_conformance() == []
    _assert_the_session_is_conformant(marked.report())

    # ... and the same response over the real canon, which names 404 explicitly, is not marked.
    assert _recorded(load_canon(), response).report()["matched_by_default"] == []


# The resolver fails closed: an unresolvable response `$ref` is a recorder error, never a body.


def test_an_unresolvable_response_ref_is_a_recorder_error_not_a_bodyless_declaration() -> None:
    document = copy.deepcopy(load_canon())
    document["paths"][_PATH]["get"]["responses"]["404"] = {
        "$ref": "#/components/responses/DoesNotExist"
    }
    harness = _recorded(
        document,
        _response(
            "GET",
            f"/api/v1{_PATH}",
            404,
            content=b'{"error":{"code":"E001","message":"Not found"}}',
        ),
    )
    assert [row["error"].split(":", 1)[0] for row in harness.recorder_errors] == ["ValueError"]
    with pytest.raises(AssertionError, match="escaped Harness.record"):
        _assert_the_session_is_conformant(harness.report())
