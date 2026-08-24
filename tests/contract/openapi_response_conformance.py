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

It also says nothing about traffic that does not go through `httpx.AsyncClient.request`.
`AsyncClient.stream()` calls `send()` directly, so the eight SSE / NDJSON tests under
`tests/integration/` are invisible here: those operations appear in `unobserved_operations`
and NOT in any `unvalidated` category. Measured, not assumed. `/simulator/events` and
`/simulator/runs/{run_id}/events` are `text/event-stream` and would validate nothing if the
hook moved to `send()`; the artifact download is no longer in that sentence, because since
T1110 it serves three declared JSON documents and a streamed download of one of them WOULD be
worth validating. It is written down because "the gate cannot see this traffic" should not
have to be rediscovered.

**T1109 rewrote the bookkeeping, and that is the important part of this file now.** External
review found the gate failing open in several independent ways. Four of them shared one cause:
an operation was credited as `observed` the moment its PATH resolved, and every subsequent way
of not validating its body was a bare `return` - so a non-JSON body, a body that would not
parse, a 2xx the canon does not declare and a declared status with no JSON schema all bought
coverage they had not earned. Measured, against the old engine: each of those four left
`observed=['GET /health']` with `bodies_checked=0` and an empty finding list.

The fifth was different and worth stating precisely, because the wrong reason under the right
conclusion is how this programme keeps being caught: a request that matched NO canonical path
never entered `observed` - it returned before the `observed.add` - but it, and the undeclared
and unschemad registries, reached only the JSON report, which the aggregate assertion never
read. And a recorder that threw produced a clean-looking session too, because the wrapper
wrote `except Exception: pass`.

The rule now is **coverage means validation**: `observed` grows only when a response was really
compared to the canon, every other outcome is filed under a named category in `unvalidated`,
an exception inside the recorder lands in `recorder_errors`, and `responses_seen` /
`two_xx_seen` let a caller tell "nothing was measured" from "everything measured clean". All
of it is read by the aggregate assertion; none of it is a silent skip.

**T1110 fixed what the allowance list T1109 introduced made possible.** A named category with a
justified row is still a skip, and a row is only as good as the claim behind it. External review
found the row for `GET /simulator/runs/{run_id}/artifacts/{name}` - written as "an honest binary
download" - covering a route that serves JSON, NDJSON and ZIP under one operation. Reproduced
here before anything was changed:

    PAYLOAD b'{"status":"ok"}'  OBSERVED set() CHECKED 0  AGGREGATE PASS
    PAYLOAD b'{broken'          OBSERVED set() CHECKED 0  AGGREGATE PASS

Two causes, and the same shape as the old ones - an early return, and a registry nobody read
carefully enough:

1. `record` classified from the media type the CANON declared and returned before touching the
   body, so a JSON response from a binary-declared operation was filed as unvalidatable without
   the `content-type` header ever being consulted. The classification now follows the RESPONSE:
   `classify_response_declaration` is given the media type that arrived and answers about THAT
   entry of the canon's `content` map, and a media type the canon does not declare at all is a
   FINDING (`DECL_MEDIA_NOT_DECLARED`) rather than a category, because a category can be written
   into the allowance and "the document describes something other than what the service sends" is
   the one thing that must not be excusable.
2. `unvalidated_detail` was ASSIGNED per row, so several responses under one key kept only the
   last one's content type - and the aggregate compared the allowance by key and never read the
   detail at all. Details accumulate now, `unvalidated_media` records the normalized media type of
   every response behind a row, and the aggregate checks each row against all of them.
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


# The verdicts `classify_response_declaration` can return. Every 2xx response the wrapper sees
# lands in exactly one of them, and every one except `DECL_SCHEMA` means *the body cannot be
# validated against a schema* - which is why they are named here rather than collapsed into a
# falsy return.
DECL_UNDECLARED = "status-not-declared"
DECL_NO_CONTENT = "declared-without-a-body"
DECL_NO_JSON_MEDIA = "declared-with-no-json-media-type"
DECL_NO_SCHEMA = "declared-json-without-a-schema"
DECL_SCHEMA = "schema"
# T1110: the response arrived under a media type the canon does not declare for this operation
# and status. See `classify_response_declaration` for why this had to become a verdict of its own.
DECL_MEDIA_NOT_DECLARED = "media-type-not-declared"


def normalize_media_type(value: str | None) -> str:
    """`Application/JSON; charset=utf-8` -> `application/json`.

    Parameters are dropped and the type is lower-cased, because `text/event-stream` and
    `text/event-stream; charset=utf-8` are the same declaration and a comparison that said
    otherwise would file every real SSE response under "the canon never mentioned this".
    """

    return (value or "").split(";", 1)[0].strip().lower()


def is_json_media_type(value: str | None) -> bool:
    """RFC 6839: `application/json` and any `*/*+json` structured suffix."""

    media = normalize_media_type(value)
    return media == "application/json" or media.endswith("+json")


def declared_media_types(
    document: dict[str, Any], method: str, path: str, status: int
) -> list[str]:
    """Every media type the canon declares for this operation and status, normalized."""

    operation = ((document.get("paths") or {}).get(path) or {}).get(method.lower())
    if not isinstance(operation, dict):
        return []
    responses = operation.get("responses") or {}
    key = next((k for k in (str(status), "default") if k in responses), None)
    if key is None or not isinstance(responses[key], dict):
        return []
    return sorted(normalize_media_type(m) for m in (responses[key].get("content") or {}))


def classify_response_declaration(
    document: dict[str, Any], method: str, path: str, status: int, content_type: str | None = None
) -> tuple[str, str | None]:
    """What the canon declares **for the media type that actually arrived**, and its schema.

    Returns `(verdict, pointer)`. The pointer is a JSON pointer, not the schema itself:
    validating through `{"$ref": "urn:canon#<pointer>"}` lets every nested `$ref` resolve
    against the whole document, uniformly, whether the response schema is written as a `$ref`
    or inline.

    The verdict exists because "the canon says nothing about this status", "the canon says this
    status carries no body", "the canon declares a non-JSON body" and "the canon declares JSON
    and forgot the schema" are four different facts about the document. The previous version
    collapsed all four into a bare `None`, which is one of the ways an unvalidated response
    still counted as coverage.

    **T1110 made the choice of media type follow the response instead of the document, and that
    is the point of this function now.** The previous version picked the JSON entry out of the
    canon's `content` map if there was one and returned `DECL_NO_JSON_MEDIA` if there was not -
    without ever looking at what the response actually carried. On a heterogeneous operation
    that is a fail-open: `GET /simulator/runs/{run_id}/artifacts/{name}` declares one binary
    media type, and the handler (`app/api/v1/simulator.py:2938`) returns a starlette
    `FileResponse` with no explicit `media_type`, so the wire type is whatever
    `mimetypes.guess_type(name)` says - `application/json` for `status.json`, `summary.json` and
    `last_tick.json`. Measured: a real JSON body, and a body of broken JSON, both came back
    `OBSERVED set() CHECKED 0` with the aggregate PASSING, because the classification never read
    the `content-type` header it was filing the row under.

    The rule now is one rule: **the canon has to declare the media type the response carries.**
    If it does and that type is JSON with a schema, the body is validated; if it does and the
    type is not JSON, there is honestly nothing to check; if it does NOT, that is
    `DECL_MEDIA_NOT_DECLARED` - a disagreement between the service and authority number one,
    which is this programme's own defect class and never a skip.

    `content_type=None` keeps the old document-only reading, which is what
    `response_schema_pointer` and the resolution counter-checks want: "where does the canon put
    the JSON schema for this operation", asked of the document alone.
    """

    operation = ((document.get("paths") or {}).get(path) or {}).get(method.lower())
    if not isinstance(operation, dict):
        return DECL_UNDECLARED, None
    responses = operation.get("responses") or {}
    key = next((k for k in (str(status), "default") if k in responses), None)
    if key is None:
        return DECL_UNDECLARED, None
    response = responses[key]
    if not isinstance(response, dict):
        return DECL_UNDECLARED, None
    content = response.get("content") or {}
    if not content:
        return DECL_NO_CONTENT, None

    if content_type is None:
        media = next((m for m in content if is_json_media_type(m)), None)
        if media is None or not isinstance(content.get(media), dict):
            return DECL_NO_JSON_MEDIA, None
    else:
        arrived = normalize_media_type(content_type)
        media = next((m for m in content if normalize_media_type(m) == arrived), None)
        if media is None or not isinstance(content.get(media), dict):
            return DECL_MEDIA_NOT_DECLARED, None
        if not is_json_media_type(media):
            return DECL_NO_JSON_MEDIA, None

    if "schema" not in content[media]:
        return DECL_NO_SCHEMA, None
    return DECL_SCHEMA, (
        f"/paths/{_escape(path)}/{method.lower()}/responses/{_escape(key)}"
        f"/content/{_escape(media)}/schema"
    )


def response_schema_pointer(
    document: dict[str, Any], method: str, path: str, status: int
) -> str | None:
    """The JSON pointer of the schema the canon declares for this operation and status.

    A thin wrapper over `classify_response_declaration`, kept because the pointer on its own is
    what the resolution counter-checks assert against.
    """

    return classify_response_declaration(document, method, path, status)[1]


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


# Categories for a 2xx response whose body this engine did NOT validate. Every 2xx that comes
# through the wrapper ends up either validated or in exactly one of these, and the aggregate
# assertion in `test_p011_responses_conform_to_the_canon.py` compares the whole set against a
# named, justified allowance. Silence is what the first version of this file did; a category is
# what replaced it.
UNVALIDATED_NO_CANONICAL_PATH = "no-canonical-path"
UNVALIDATED_STATUS_NOT_DECLARED = DECL_UNDECLARED
UNVALIDATED_NO_JSON_MEDIA = DECL_NO_JSON_MEDIA
UNVALIDATED_NO_SCHEMA = DECL_NO_SCHEMA
UNVALIDATED_BODY_NOT_JSON = "body-was-not-json"
UNVALIDATED_BODY_EMPTY = "body-was-empty"
UNVALIDATED_BODY_UNPARSEABLE = "body-did-not-parse"
UNVALIDATED_BODY_NOT_READ = "body-was-not-read"

UNVALIDATED_CATEGORIES = (
    UNVALIDATED_NO_CANONICAL_PATH,
    UNVALIDATED_STATUS_NOT_DECLARED,
    UNVALIDATED_NO_JSON_MEDIA,
    UNVALIDATED_NO_SCHEMA,
    UNVALIDATED_BODY_NOT_JSON,
    UNVALIDATED_BODY_EMPTY,
    UNVALIDATED_BODY_UNPARSEABLE,
    UNVALIDATED_BODY_NOT_READ,
)


class Harness:
    """Accumulates every 2xx body the suite produces, and what the canon says of it.

    **Coverage means validation.** An operation enters `observed` only when a response of its was
    actually compared to the canon - either a JSON body validated against a declared schema, or a
    status the canon declares to carry no body that really carried none. Every other outcome goes
    into `unvalidated`, keyed by category, and is visible to the aggregate assertion. The earlier
    version marked an operation observed the moment its path resolved, before the content type and
    the JSON parse were even looked at, so a body that was never validated counted as coverage.
    That is one of the fail-open modes `T1109` found, and the bookkeeping below exists to make it
    unrepresentable.

    **Nothing here is silent.** `unvalidated`, `recorder_errors`, `responses_seen` and
    `two_xx_seen` are all read by the aggregate. A skipped body has to be named and allowed; a
    recorder that throws has to be reported; a session that measured nothing has to say so.
    """

    def __init__(self) -> None:
        self.installed = False
        self._original: Any = None
        self._document: dict[str, Any] | None = None
        self._registry: Any = None
        self._paths: list[str] = []

        # Traffic, so that "nothing was measured" can be told apart from "everything measured
        # clean". Both used to look like the same green.
        self.responses_seen = 0
        self.two_xx_seen = 0

        # Coverage. Only operations a response of which was really checked.
        self.observed: set[tuple[str, str]] = set()
        self.bodies_checked = 0
        self.empty_bodies_confirmed = 0

        # Everything that was NOT checked, by category -> {(method, path, status)}.
        self.unvalidated: dict[str, set[tuple[str, str, int]]] = {
            category: set() for category in UNVALIDATED_CATEGORIES
        }
        # Free-text detail for a category row, e.g. the content type that was not JSON.
        #
        # A SET and not a string. One row is one (category, operation, status), and a single
        # operation can produce several different responses under it: the artifact download sends
        # `text/plain` for `events.ndjson` and `application/x-zip-compressed` for `bundle.zip`
        # within the same session. The previous version assigned, so the last response silently
        # overwrote every earlier one - which meant a row could be justified by a detail that was
        # no longer the whole truth about what had arrived under it.
        self.unvalidated_detail: dict[tuple[str, str, str, int], set[str]] = {}
        # The normalized media type(s) really carried by the responses behind each row. Kept apart
        # from `detail`, which is free text, because the aggregate CHECKS this one: an allowance
        # row is a claim about what arrived, and a claim nothing compares to the response is the
        # fail-open T1110 found.
        self.unvalidated_media: dict[tuple[str, str, str, int], set[str]] = {}

        # A recorder that raises is a recorder that has stopped measuring. It must not break the
        # request under test, so it is collected here and asserted at the end of the session.
        self.recorder_errors: list[dict[str, Any]] = []

        self.findings: dict[tuple[str, str], tuple[str, Any]] = {}

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
            guarded_record(harness, response)
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

    def _skip(
        self,
        category: str,
        method: str,
        path: str,
        status: int,
        detail: str = "",
        media: str | None = None,
    ) -> None:
        key = (category, method, path, status)
        self.unvalidated[category].add((method, path, status))
        if detail:
            self.unvalidated_detail.setdefault(key, set()).add(detail)
        # Recorded for EVERY skip, including the ones whose `detail` is an exception rather than a
        # media type, so the aggregate can check an allowance row against every response that fell
        # under it rather than against the row's key alone.
        self.unvalidated_media.setdefault(key, set()).add(normalize_media_type(media))

    def _finding(self, operation: str, node: str, message: str, value: Any) -> None:
        key = (operation, node)
        if key not in self.findings:
            self.findings[key] = (message, value)

    def record(self, response: Any) -> None:
        assert self._document is not None
        self.responses_seen += 1
        status = response.status_code
        if not (200 <= status < 300):
            return
        self.two_xx_seen += 1
        method = response.request.method.upper()
        raw_path = response.request.url.path

        content_type = response.headers.get("content-type", "")

        path = match_path(raw_path, self._paths)
        if path is None:
            self._skip(
                UNVALIDATED_NO_CANONICAL_PATH,
                method,
                raw_path,
                status,
                content_type,
                media=content_type,
            )
            return

        # The classification follows the RESPONSE, not the document: `content_type` is passed in,
        # and the canon entry that is consulted is the one for the media type that really arrived.
        # See `classify_response_declaration` - reading the document alone is how a JSON body from
        # a binary-declared operation used to leave the engine unvalidated and unremarked.
        verdict, pointer = classify_response_declaration(
            self._document, method, path, status, content_type
        )

        if verdict == DECL_UNDECLARED:
            self._skip(
                UNVALIDATED_STATUS_NOT_DECLARED, method, path, status, content_type,
                media=content_type,
            )
            return

        # These two are facts settled by the header alone and need no body, so they come before
        # the body is touched: a streamed SSE response cannot be read, and asking for its bytes
        # first would file it under the wrong category.
        if verdict == DECL_NO_JSON_MEDIA:
            self._skip(
                UNVALIDATED_NO_JSON_MEDIA, method, path, status, content_type,
                media=content_type,
            )
            return
        if verdict == DECL_NO_SCHEMA:
            self._skip(
                UNVALIDATED_NO_SCHEMA, method, path, status, content_type, media=content_type
            )
            return

        if verdict == DECL_MEDIA_NOT_DECLARED:
            declared = declared_media_types(self._document, method, path, status)
            if any(is_json_media_type(media) for media in declared):
                # The canon promises JSON here and something else turned up. That has always been
                # a category of its own and keeps its name: one of the two is wrong, and which one
                # is a judgement about the operation, not about the engine.
                self._skip(
                    UNVALIDATED_BODY_NOT_JSON,
                    method,
                    path,
                    status,
                    content_type or "no content-type header",
                    media=content_type,
                )
                return
            # Nothing JSON is declared and the service still sent a media type the canon never
            # mentions. This is a FINDING and deliberately not a category: a category can be
            # written into UNVALIDATED_2XX_ALLOWANCE, and "the document describes something other
            # than what the service sends" is precisely what this programme exists to remove.
            self._finding(
                f"{method} {path} {status}",
                "$",
                "the response arrived as "
                f"{normalize_media_type(content_type) or 'a response with no content-type header'}"
                ", which the canon does not declare for this operation and status; it declares "
                f"{', '.join(declared) or 'no media type at all'}. Either the canon is missing "
                "the media type the handler really sends, or the handler is sending the wrong "
                "one - and if what arrived is JSON, the canon owes it a schema rather than an "
                "allowance row",
                normalize_media_type(content_type) or None,
            )
            return

        # `content` is what httpx has buffered. A response that was streamed and never read
        # raises instead of returning bytes, and that is a category of its own rather than an
        # error: nothing can be validated, and the allowance has to say why that is acceptable.
        try:
            content = response.content
        except Exception as exc:
            self._skip(
                UNVALIDATED_BODY_NOT_READ, method, path, status, type(exc).__name__,
                media=content_type,
            )
            return

        if verdict == DECL_NO_CONTENT:
            # The canon says this status carries no body. That is a claim about the response, and
            # comparing it to the real response IS a check - so it counts as coverage - but it is
            # counted apart from `bodies_checked`, because no schema was exercised.
            if content:
                self._finding(
                    f"{method} {path} {status}",
                    "$",
                    "the canon declares this status with no `content`, but the response carried "
                    f"{len(content)} byte(s) of {content_type or 'an unstated media type'}",
                    summarize(content[:200].decode("utf-8", "replace")),
                )
                return
            self.observed.add((method, path))
            self.empty_bodies_confirmed += 1
            return

        assert pointer is not None
        # Backstop, and since T1110 it should be unreachable: `DECL_SCHEMA` is now only returned
        # for the canon entry whose media type equals the one on the wire, so a non-JSON body
        # cannot get this far - it is classified `DECL_MEDIA_NOT_DECLARED` above and filed under
        # this same category from there. Left in place because "should be unreachable" is a claim
        # about today's classifier, and the cost of being wrong is an unvalidated body.
        if not is_json_media_type(content_type):
            self._skip(
                UNVALIDATED_BODY_NOT_JSON,
                method,
                path,
                status,
                content_type or "no content-type header",
                media=content_type,
            )
            return
        if not content:
            self._skip(
                UNVALIDATED_BODY_EMPTY, method, path, status, content_type, media=content_type
            )
            return
        try:
            body = response.json()
        except Exception as exc:
            self._skip(
                UNVALIDATED_BODY_UNPARSEABLE,
                method,
                path,
                status,
                f"{type(exc).__name__}: {exc}",
                media=content_type,
            )
            return

        self.observed.add((method, path))
        self.bodies_checked += 1
        for node, message, value in validate_body(
            self._document, pointer, body, registry=self._registry
        ):
            self._finding(f"{method} {path} {status}", node, message, value)

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

    def unvalidated_keys(self) -> list[str]:
        """`"<category> METHOD /path STATUS"` for every 2xx whose body was not validated.

        This is the shape the allowance in `test_p011_responses_conform_to_the_canon.py` is keyed
        by: one line per (category, operation, status), stable across runs, and readable enough
        that an entry in the allowance can be argued with.
        """

        return sorted(
            f"{category} {method} {path} {status}"
            for category, rows in self.unvalidated.items()
            for method, path, status in rows
        )

    def unvalidated_rows(self) -> list[dict[str, Any]]:
        """One row per (category, operation, status), carrying EVERY response behind it.

        `media_types` is the load-bearing field and it is what the aggregate checks the allowance
        against. `detail` stays free text for a human reading the report.
        """

        return [
            {
                "key": f"{category} {method} {path} {status}",
                "category": category,
                "operation": f"{method} {path}",
                "status": status,
                "detail": "; ".join(
                    sorted(self.unvalidated_detail.get((category, method, path, status), ()))
                ),
                "media_types": sorted(
                    self.unvalidated_media.get((category, method, path, status), ())
                ),
            }
            for category, rows in sorted(self.unvalidated.items())
            for method, path, status in sorted(rows)
        ]

    def report(self) -> dict[str, Any]:
        document = self._document if self._document is not None else load_canon()
        declared = canon_operations(document)
        return {
            "canon_operations": len(declared),
            "responses_seen": self.responses_seen,
            "two_xx_seen": self.two_xx_seen,
            "observed_operations": sorted(f"{m} {p}" for m, p in self.observed),
            "unobserved_operations": sorted(
                f"{m} {p}" for m, p in declared if (m, p) not in self.observed
            ),
            "bodies_checked": self.bodies_checked,
            "empty_bodies_confirmed": self.empty_bodies_confirmed,
            "unvalidated_2xx": self.unvalidated_keys(),
            "unvalidated_2xx_detail": self.unvalidated_rows(),
            "recorder_errors": list(self.recorder_errors),
            "non_conforming": self.non_conformance(),
        }

    def write_report(self) -> str | None:
        if self.responses_seen == 0:
            return None
        path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", REPORT_PATH)
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.report(), handle, indent=2, sort_keys=True, default=str)
        return path


def guarded_record(harness: Harness, response: Any) -> None:
    """Run `harness.record` without letting it break the request under test - or vanish.

    A recorder that can fail a request is a recorder that gets deleted, so the exception is not
    re-raised here. It is *recorded*, and the aggregate assertion in
    `test_p011_responses_conform_to_the_canon.py` fails the session if the list is not empty.
    The previous version wrote `except Exception: pass`, which meant a recorder that had stopped
    working looked exactly like a session with nothing to report.

    `except Exception` and not `except BaseException`: `asyncio.CancelledError` and
    `KeyboardInterrupt` are not recorder defects and must keep propagating.
    """

    try:
        harness.record(response)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see the docstring
        import traceback

        try:
            where = (
                f"{response.request.method} {response.request.url.path} {response.status_code}"
            )
        except Exception:  # pragma: no cover - a response too broken to describe itself
            where = "<unreportable response>"
        harness.recorder_errors.append(
            {
                "where": where,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )[-2000:],
            }
        )


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
