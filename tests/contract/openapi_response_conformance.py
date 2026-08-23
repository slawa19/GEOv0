"""The runtime half of `F-011-10`: does a real response body validate against the canon?

Program 011's whole thesis is that `api/openapi.yaml` describes what the service returns.
Until this file existed, **nothing in the repository ever compared a response body to it**.
The ~48 schemas this wave added were checked by ad-hoc runs quoted in commit messages; by
AGENTS.md section 9 that is worth nothing, because no committed check executes it.

This module is the check. It wraps `httpx.AsyncClient.request` for the whole session and
validates every observed 2xx JSON body against the schema the canon declares *for that
operation and that status*, with `openapi_schema_validator.OAS30Validator` - the same
validator the static `nullable` guard uses, and the one whose reading of OpenAPI 3.0.3 is
the reason `nullable: true` beside a non-null `enum` is a defect at all.

**Why it is driven by the existing suite rather than by hand-written calls.** Hand-written
calls describe the operations whoever wrote them thought of. Riding on the suite gets ~70
operations for free, grows every time anyone adds a test, and - crucially - exercises the
*default* argument shapes real callers use, which is exactly where the worst of the defects
it found were hiding (`GET /admin/graph/snapshot` without `?equivalent=` returns
`net_sign: null` on every participant; an ordinary successful payment sends `error: null`).

**What it does not do.** It says nothing about an operation nothing exercises. That is why
the report names the unobserved operations instead of counting them as passing: an empty
failure list over a denominator nobody printed is the vacuum this programme keeps finding.
The static guard in `test_p011_nullable_needs_a_sibling_type.py` is the complement - it
reads nodes no test ever reaches, and cannot tell whether null actually occurs there.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

import yaml

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# The canon declares one server, `/api/v1`, and the app additionally serves the three root
# health routes unprefixed (see `test_p011_root_routes_bypass_the_policy_gate`). Both
# spellings map onto the same canonical path.
_SERVER_PREFIX = "/api/v1"

REPORT_PATH = os.path.join(".local-run", "test-runs", "openapi-response-conformance.json")


def canon_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "api", "openapi.yaml")
    )


def load_canon() -> dict[str, Any]:
    with open(canon_path(), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# ---------------------------------------------------------------------------------------
# Operation resolution
# ---------------------------------------------------------------------------------------


def canon_operations(document: dict[str, Any]) -> set[tuple[str, str]]:
    """Every (METHOD, /canonical/path) the document declares."""

    found: set[tuple[str, str]] = set()
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in item:
            if method.lower() in _HTTP_METHODS:
                found.add((method.upper(), path))
    return found


def match_path(observed: str, candidates: Iterable[str]) -> str | None:
    """Map a concrete request path onto the canonical templated path.

    Exact wins over templated, and among templated candidates the one with the fewest
    `{placeholders}` wins - so `/admin/participants/stats` resolves to itself rather than to
    `/admin/participants/{pid}` when both could match.
    """

    if observed.startswith(_SERVER_PREFIX + "/") or observed == _SERVER_PREFIX:
        observed = observed[len(_SERVER_PREFIX):] or "/"

    candidate_list = list(candidates)
    if observed in candidate_list:
        return observed

    parts = observed.strip("/").split("/")
    best: tuple[int, str] | None = None
    for candidate in candidate_list:
        template = candidate.strip("/").split("/")
        if len(template) != len(parts):
            continue
        wildcards = 0
        for segment, expected in zip(parts, template):
            if expected.startswith("{") and expected.endswith("}"):
                if segment == "":
                    break
                wildcards += 1
            elif segment != expected:
                break
        else:
            if best is None or wildcards < best[0]:
                best = (wildcards, candidate)
    return best[1] if best else None


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def response_schema_pointer(
    document: dict[str, Any], method: str, path: str, status: int
) -> str | None:
    """The JSON pointer of the schema the canon declares for this operation and status.

    A pointer, not the schema itself: validating through `{"$ref": "urn:canon#<pointer>"}`
    lets every nested `$ref` resolve against the whole document, uniformly, whether the
    response schema is written as a `$ref` or inline.
    """

    operation = ((document.get("paths") or {}).get(path) or {}).get(method.lower())
    if not isinstance(operation, dict):
        return None
    responses = operation.get("responses") or {}
    key = next((k for k in (str(status), "default") if k in responses), None)
    if key is None:
        return None
    response = responses[key]
    if not isinstance(response, dict):
        return None
    content = response.get("content") or {}
    media = next((m for m in content if m == "application/json" or m.endswith("+json")), None)
    if media is None or not isinstance(content.get(media), dict):
        return None
    if "schema" not in content[media]:
        return None
    return (
        f"/paths/{_escape(path)}/{method.lower()}/responses/{_escape(key)}"
        f"/content/{_escape(media)}/schema"
    )


# ---------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------


def registry_for(document: dict[str, Any]) -> Any:
    from referencing import Registry, Resource

    return Registry().with_resource("urn:canon", Resource.opaque(document))


def normalize_node(error_path: Iterable[Any]) -> str:
    """`$.results[3].net_sign` -> `$.results[].net_sign`.

    Array indices are dropped so the same defect in element 0 and element 7 is one finding
    rather than a list whose length depends on how much data a fixture happened to insert.
    """

    node = "$"
    for token in error_path:
        node += "[]" if isinstance(token, int) else f".{token}"
    return node


def summarize(value: Any) -> Any:
    """Keep a finding's value readable without losing what it was."""

    if isinstance(value, (dict, list)) and len(value) > 8:
        return f"<{type(value).__name__} of {len(value)}>"
    if isinstance(value, str) and len(value) > 120:
        return value[:117] + "..."
    return value


def validate_body(
    document: dict[str, Any],
    pointer: str,
    body: Any,
    registry: Any | None = None,
) -> list[tuple[str, str, Any]]:
    """De-duplicated (node, message, offending value) triples for one body.

    The value comes from `error.instance`, which is the sub-document the validator actually
    rejected. Re-walking the body to `node` would report the FIRST element of an array while
    the failure was at index 7 - which is exactly the wrong value to print next to a message.
    """

    from openapi_schema_validator import OAS30Validator

    validator = OAS30Validator(
        {"$ref": f"urn:canon#{pointer}"},
        registry=registry if registry is not None else registry_for(document),
    )
    found: dict[tuple[str, str], Any] = {}
    for error in validator.iter_errors(body):
        key = (normalize_node(error.absolute_path), error.message)
        if key not in found:
            found[key] = summarize(error.instance)
    return [(node, message, value) for (node, message), value in found.items()]


# ---------------------------------------------------------------------------------------
# The session-wide recorder
# ---------------------------------------------------------------------------------------


class Harness:
    """Accumulates every 2xx JSON body the suite produces, and what the canon says of it."""

    def __init__(self) -> None:
        self.installed = False
        self._original: Any = None
        self._document: dict[str, Any] | None = None
        self._registry: Any = None
        self._paths: list[str] = []
        self.observed: set[tuple[str, str]] = set()
        self.unmatched: set[tuple[str, str]] = set()
        self.undeclared: set[tuple[str, str, int]] = set()
        self.unschemad: set[tuple[str, str, int]] = set()
        self.findings: dict[tuple[str, str], tuple[str, Any]] = {}
        self.bodies_checked = 0

    # -- lifecycle ------------------------------------------------------------------

    def install(self) -> None:
        if self.installed:
            return
        import httpx

        self._document = load_canon()
        self._registry = registry_for(self._document)
        self._paths = list((self._document.get("paths") or {}).keys())

        original = httpx.AsyncClient.request
        harness = self

        async def request(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            response = await original(self, method, url, *args, **kwargs)
            try:
                harness.record(response)
            except Exception:  # pragma: no cover - see the note below
                # A recorder that can break a request is a recorder that gets deleted. The
                # non-vacuity guards - `bodies_checked`, and the round-trip test that drives
                # a real request through this wrapper - are what keep this `except` from
                # hiding a harness that has quietly stopped working.
                pass
            return response

        self._original = original
        httpx.AsyncClient.request = request  # type: ignore[method-assign]
        self.installed = True

    def uninstall(self) -> None:
        if not self.installed:
            return
        import httpx

        httpx.AsyncClient.request = self._original  # type: ignore[method-assign]
        self.installed = False

    # -- recording ------------------------------------------------------------------

    def record(self, response: Any) -> None:
        assert self._document is not None
        status = response.status_code
        if not (200 <= status < 300):
            return
        method = response.request.method.upper()
        raw_path = response.request.url.path

        path = match_path(raw_path, self._paths)
        if path is None:
            self.unmatched.add((method, raw_path))
            return
        self.observed.add((method, path))

        pointer = response_schema_pointer(self._document, method, path, status)
        if pointer is None:
            declared = (
                ((self._document["paths"].get(path) or {}).get(method.lower()) or {}).get(
                    "responses"
                )
                or {}
            )
            if str(status) in declared or "default" in declared:
                self.unschemad.add((method, path, status))
            else:
                self.undeclared.add((method, path, status))
            return

        if "json" not in response.headers.get("content-type", ""):
            return
        if status == 204 or not response.content:
            return
        try:
            body = response.json()
        except Exception:
            return

        self.bodies_checked += 1
        for node, message, value in validate_body(
            self._document, pointer, body, registry=self._registry
        ):
            key = (f"{method} {path} {status}", node)
            if key not in self.findings:
                self.findings[key] = (message, value)

    # -- reporting ------------------------------------------------------------------

    def non_conformance(self) -> list[dict[str, Any]]:
        return sorted(
            (
                {
                    "operation": operation,
                    "node": node,
                    "value": value,
                    "message": message,
                }
                for (operation, node), (message, value) in self.findings.items()
            ),
            key=lambda row: (row["operation"], row["node"]),
        )

    def report(self) -> dict[str, Any]:
        document = self._document if self._document is not None else load_canon()
        declared = canon_operations(document)
        return {
            "canon_operations": len(declared),
            "observed_operations": sorted(f"{m} {p}" for m, p in self.observed),
            "unobserved_operations": sorted(
                f"{m} {p}" for m, p in declared if (m, p) not in self.observed
            ),
            "bodies_checked": self.bodies_checked,
            "requests_with_no_canonical_path": sorted(
                f"{m} {p}" for m, p in self.unmatched
            ),
            "observed_status_not_declared": sorted(
                f"{m} {p} {s}" for m, p, s in self.undeclared
            ),
            "declared_status_without_a_json_schema": sorted(
                f"{m} {p} {s}" for m, p, s in self.unschemad
            ),
            "non_conforming": self.non_conformance(),
        }

    def write_report(self) -> str | None:
        if not self.observed:
            return None
        path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", REPORT_PATH)
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.report(), handle, indent=2, sort_keys=True, default=str)
        return path


HARNESS = Harness()

# The name of the aggregate test that reads the registry above. It has to run after every
# test that makes a request, which alphabetically it would not: `tests/contract` sorts
# before `tests/integration` and `tests/unit`.
REPORT_TEST = "test_every_observed_2xx_body_validates_against_the_canon"


def move_report_test_last(items: list[Any]) -> None:
    deferred = [item for item in items if getattr(item, "name", None) == REPORT_TEST]
    if not deferred:
        return
    for item in deferred:
        items.remove(item)
    items.extend(deferred)
