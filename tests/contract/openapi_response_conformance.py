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
and NOT in any `unvalidated` category. Measured, not assumed - and left as it is, because
`/simulator/events`, `/simulator/runs/{run_id}/events` and the artifact download are declared
`text/event-stream` and `application/octet-stream`, so moving the hook to `send()` would
add three allowance rows and validate exactly zero extra bodies. It is written down because
"the gate cannot see this traffic" should not have to be rediscovered.

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


def classify_response_declaration(
    document: dict[str, Any], method: str, path: str, status: int
) -> tuple[str, str | None]:
    """What the canon declares for this operation and status, and where its schema is.

    Returns `(verdict, pointer)`. The pointer is a JSON pointer, not the schema itself:
    validating through `{"$ref": "urn:canon#<pointer>"}` lets every nested `$ref` resolve
    against the whole document, uniformly, whether the response schema is written as a `$ref`
    or inline.

    The verdict exists because "the canon says nothing about this status", "the canon says this
    status carries no body", "the canon declares a non-JSON body" and "the canon declares JSON
    and forgot the schema" are four different facts about the document. The previous version
    collapsed all four into a bare `None`, which is one of the ways an unvalidated response
    still counted as coverage.
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
    media = next((m for m in content if m == "application/json" or m.endswith("+json")), None)
    if media is None or not isinstance(content.get(media), dict):
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
        self.unvalidated_detail: dict[tuple[str, str, str, int], str] = {}

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

    def _skip(self, category: str, method: str, path: str, status: int, detail: str = "") -> None:
        self.unvalidated[category].add((method, path, status))
        if detail:
            self.unvalidated_detail[(category, method, path, status)] = detail

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

        path = match_path(raw_path, self._paths)
        if path is None:
            self._skip(UNVALIDATED_NO_CANONICAL_PATH, method, raw_path, status)
            return

        verdict, pointer = classify_response_declaration(self._document, method, path, status)

        if verdict == DECL_UNDECLARED:
            self._skip(UNVALIDATED_STATUS_NOT_DECLARED, method, path, status)
            return

        content_type = response.headers.get("content-type", "")

        # These two are facts about the DOCUMENT and need no body, so they are settled before
        # the body is touched: a streamed SSE response cannot be read, and asking for its bytes
        # first would file it under the wrong category.
        if verdict == DECL_NO_JSON_MEDIA:
            self._skip(UNVALIDATED_NO_JSON_MEDIA, method, path, status, content_type)
            return
        if verdict == DECL_NO_SCHEMA:
            self._skip(UNVALIDATED_NO_SCHEMA, method, path, status, content_type)
            return

        # `content` is what httpx has buffered. A response that was streamed and never read
        # raises instead of returning bytes, and that is a category of its own rather than an
        # error: nothing can be validated, and the allowance has to say why that is acceptable.
        try:
            content = response.content
        except Exception as exc:
            self._skip(UNVALIDATED_BODY_NOT_READ, method, path, status, type(exc).__name__)
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
        if "json" not in content_type:
            self._skip(
                UNVALIDATED_BODY_NOT_JSON,
                method,
                path,
                status,
                content_type or "no content-type header",
            )
            return
        if not content:
            self._skip(UNVALIDATED_BODY_EMPTY, method, path, status, content_type)
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
        return [
            {
                "key": f"{category} {method} {path} {status}",
                "category": category,
                "operation": f"{method} {path}",
                "status": status,
                "detail": self.unvalidated_detail.get((category, method, path, status), ""),
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
