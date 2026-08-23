"""T1106: no operation may answer a status its declaration does not name.

Program 011. This is the general form of a class that until now was caught one status at a time:
`RT-011-3` holds the line for `429`, `RT-011-6` for a named `503` on three routes. Both are right
and both are narrow - each names its status in advance, so a *different* status appearing on a
route is invisible to them. This guard states the class instead:

    reachable(operation)  is a subset of  declared(operation)

for every status the route table and the dependency graph can *derive*, in both documents -
`api/openapi.yaml` (authority number one) and the schema the application generates.

**One-directional, on purpose.** The guard asserts "reachable implies declared" and never the
converse, because the derivation is a lower bound and a correct declaration can sit outside it.
The worked example is `GET /admin/health/db`: it declares `403`, the declaration is *right* - a
wrong `X-Admin-Token` really answers `403`, measured - and this rule cannot see it, because the
handler awaits `deps.require_admin(request, x_admin_token)` **in its own body**
(`app/api/v1/health.py`) rather than through `Depends`. A symmetric assertion would fail on a
correct declaration, and the only way to make it pass would be to delete a true statement from
authority number one. `test_an_operation_that_over_declares_is_not_reported` pins that case by
name so nobody adds the converse later.

**The derivation, signal by signal.** Everything comes from `app.routes`, `route.dependant`,
`get_flat_params` and `route.body_field`; nothing is hand-listed, so the rule cannot drift out of
step with how the routers are mounted. Each signal was proved by an executed request in the
survey that produced the two lists below; the two that no FastAPI machinery backs are re-executed
by counter-checks in this file.

* **S1 - FastAPI request validation implies `422`.** A route with a falsifiable parameter or a
  body can raise `RequestValidationError`, which `app/main.py` turns into `422` + `ErrorEnvelope`
  (E009). *Falsifiable* is defined narrowly, and the narrowness is the whole point:

  - a **plain `str` path parameter is not falsifiable**. It can never be missing (the route would
    not have matched) and there is nothing to coerce: `GET /api/v1/participants/%20%21%40`
    answers `401`, never `422`. Without this, every `/{id}` route would be credited with a `422`
    it cannot produce. A `UUID` path parameter *is* falsifiable, and so is a `str` one carrying a
    constraint - see `test_the_str_path_parameter_exemption_is_exactly_that_wide`.
  - an **optional** parameter is falsifiable when its type is not string-like, or when it carries
    constraints: `page: int = Query(1, ge=1)` yields `422` twice over, on a bad parse and on
    `ge`. Requiring `required` alone would have missed 24 operations.
  - FastAPI's own condition - *any* flat parameter implies `422` - is deliberately **not** used.
    It over-declares on 13 operations whose only flat parameter is the optional `X-Admin-Token`
    header, which is this programme's defect facing the other way.

* **S1b - on a simulator action route the same validation answers `400`.**
  `request_validation_exception_handler` returns the flat `SimulatorActionError` envelope with
  status `400` under `/api/v1/simulator/runs/.../actions/...`. So S1 there implies `400` and
  **not** `422`. Folding this in with S1 would have produced 8 false `422` claims and lost 8
  `400`s.

* **S2 - an authentication dependency implies `401`**: `get_current_participant`,
  `require_participant_or_admin`, `require_simulator_actor`. `require_admin` is **excluded** - it
  has no `401` branch at all; every failure path raises `ForbiddenException`. This is the single
  most important exclusion in the rule: treating "auth dependency" as one category would credit
  29 admin operations with an unreachable `401`, and the fix would be to write it into the canon.
  `test_require_admin_really_answers_403_and_never_401` executes the claim rather than reading it.

* **S3 - an authorisation dependency implies `403`**: `require_admin`,
  `require_participant_or_admin`, `require_simulator_actor`, and `get_current_participant` - the
  last because a participant whose `status != 'active'` gets
  `ForbiddenException("Participant account is not active")`. That branch is not hypothetical:
  `POST /admin/participants/{pid}/ban` and `/freeze` are its writers.

* **S4 - `require_simulator_actor` implies `422`, independently of S1.** It raises
  `GeoException(..., status_code=422)` when `X-Simulator-Owner` is present and does not match
  `^[A-Za-z0-9._:-]{1,64}$` (`app/api/deps.py`). This makes `422` reachable on simulator routes
  with no falsifiable parameter at all - 10 canon entries below rest on this signal alone - and
  it survives the S1b override, because it is a `GeoException` raised in a dependency and not a
  `RequestValidationError`. It is the only signal with no FastAPI machinery behind it, so
  `test_a_malformed_simulator_owner_header_really_answers_422` executes it. Those ten operations
  are the ones the canon now answers with `SimulatorIdentityUnprocessable`: for them the identity
  header is the *only* way a `422` can happen, and a generic "validation failed" description
  would have been the same kind of untrue statement this programme is removing.

* **S5 - the rate limiter implies `429`.** Not restated here: `_declare_rate_limit_status` from
  `app/main.py` is *called* on a probe document to learn the set, so this guard and the schema
  the application publishes cannot come to disagree about which operations the limiter covers.
  Already closed on both sides - zero entries in either list - and the bite-check deletes a `429`
  to prove the wiring still carries rather than merely being imported.

**What is NOT derived, and why the lists are a lower bound.** A `GeoException` raised in a
handler, or three call frames down in `app/core/**`, is real - `404` for a missing run, `409`,
`400` for a bad equivalent - and is not derivable without a call graph over dynamic Python. An
over-eager version of that rule would demand `404`/`409`/`500` across most of the surface, and
the "fix" would be to write statuses into authority number one that the service never returns.
The two lists are therefore complete for five mechanically derivable signals and for nothing
else; a `404`/`409` census needs per-route reasoning and is a separate task.

**Two lists, not one.** The canon and the generated schema fail for different reasons: `422` is a
canon-only debt (FastAPI adds `422` itself and `_custom_openapi` re-skins it), while `401`/`403`
are a generated-only one (they come from `GeoException`s raised inside dependencies and no
decorator carries `responses=`). A single list would let one side's progress mask the other's
regression.

Both lists may only shrink, and as of 2026-08-23 both are **empty** - all 263 pairs were closed
by declaring them, on both sides, with no change to any response a client receives. This file now
exists so that nothing can put an entry back.
"""

from __future__ import annotations

import copy
import inspect
import re
import typing
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends
from fastapi import Path as PathParam
from fastapi import Query
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import get_flat_params
from fastapi.routing import APIRoute

from app.api import deps
from app.config import settings
from app.main import _declare_rate_limit_status, app
from tests.contract.test_openapi_contract import (
    _load_fastapi_openapi,
    _load_openapi_yaml,
)

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Annotations a parameter can carry without becoming falsifiable: a string that is already a
# string cannot fail to parse. `inspect._empty` is an unannotated parameter, which FastAPI also
# treats as a string; `Any` accepts anything by construction.
_STRING_LIKE: set[Any] = {str, typing.Optional[str], typing.Any, inspect._empty, None}

# S2 - dependencies with at least one `UnauthorizedException` path. `require_admin` is NOT here,
# and that omission is load-bearing; see the module docstring and the executed counter-check.
_AUTHENTICATION_DEPENDENCIES = {
    deps.get_current_participant,
    deps.require_participant_or_admin,
    deps.require_simulator_actor,
}

# S3 - dependencies with at least one `ForbiddenException` path.
_AUTHORISATION_DEPENDENCIES = {
    deps.require_admin,
    deps.require_participant_or_admin,
    deps.require_simulator_actor,
    deps.get_current_participant,
}


def _schema_path(path: str) -> str:
    """Starlette keeps the converter (`/participants/{pid:path}`); OpenAPI does not."""

    return re.sub(r"\{([^{}:]+):[^{}]+\}", lambda match: "{" + match.group(1) + "}", path)


def dependency_closure(dependant: Dependant) -> set[Any]:
    """Every callable reachable through `Depends`, at any depth.

    Depth matters. `require_simulator_actor` itself pulls in `get_db` and `optional_oauth2`, and
    a router-level `Depends` sits at the same level as an endpoint-level one only by accident of
    how the routers happen to be mounted today.
    """

    found: set[Any] = set()
    stack = list(dependant.dependencies)
    while stack:
        sub = stack.pop()
        if sub.call is not None:
            found.add(sub.call)
        stack.extend(sub.dependencies)
    return found


def is_falsifiable(field: Any) -> bool:
    """Can a client send a request that fails validation on this parameter?

    Stated here rather than delegated to FastAPI, because FastAPI's own answer - "any flat
    parameter" - is measurably wrong in the direction that matters.
    """

    info = field.field_info
    annotation = getattr(info, "annotation", inspect._empty)
    location = getattr(getattr(info, "in_", None), "value", None)
    constrained = bool(getattr(info, "metadata", None))
    coercible = annotation not in _STRING_LIKE

    if location == "path":
        # A path parameter is never absent - the route would not have matched - so `required`
        # says nothing about it. Only a type that can fail to parse, or an explicit constraint,
        # makes it falsifiable.
        return coercible or constrained
    return bool(field.required) or coercible or constrained


def is_simulator_action_path(path: str) -> bool:
    """The paths where `app/main.py` re-skins a validation failure as `400` rather than `422`."""

    return path.startswith("/api/v1/simulator/runs/") and "/actions/" in path


def reachable_statuses(route: APIRoute) -> dict[str, str]:
    """S1, S1b, S2, S3 and S4 for one route, as `{status: the signal that derived it}`.

    S5 is deliberately absent here: it comes from the application's own derivation in
    `_reachable_by_operation`, so this guard cannot form its own opinion about which operations
    the limiter covers.
    """

    statuses: dict[str, str] = {}
    calls = dependency_closure(route.dependant)

    validatable = route.body_field is not None or any(
        is_falsifiable(field) for field in get_flat_params(route.dependant)
    )
    if validatable:
        if is_simulator_action_path(route.path):
            statuses["400"] = "S1b"
        else:
            statuses["422"] = "S1"

    if calls & _AUTHENTICATION_DEPENDENCIES:
        statuses["401"] = "S2"
    if calls & _AUTHORISATION_DEPENDENCIES:
        statuses["403"] = "S3"
    if deps.require_simulator_actor in calls:
        statuses["422"] = "S1+S4" if statuses.get("422") == "S1" else "S4"

    return statuses


@dataclass(frozen=True)
class _Operation:
    key: str  # "GET /simulator/events/poll" - the canon's spelling, and the report identity
    method: str
    schema_path: str  # "/api/v1/simulator/events/poll" - the generated document's key
    canon_path: str | None  # None for the four unversioned root routes


def rate_limited_operations() -> set[tuple[str, str]]:
    """S5, obtained by RUNNING `app/main.py`'s derivation rather than restating it.

    A probe document holding every operation and nothing else is handed to
    `_declare_rate_limit_status`; whichever operations come back carrying a `429` are the ones
    the limiter covers. Restating the condition here would give this guard its own copy of
    `_RATE_LIMIT_EXEMPT_PATHS`, and the two could then drift apart in silence - which is the
    defect this whole programme is about.
    """

    probe: dict[str, Any] = {"paths": {}}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        item = probe["paths"].setdefault(_schema_path(route.path), {})
        for method in route.methods & _HTTP_METHODS:
            item[method.lower()] = {}

    _declare_rate_limit_status(probe)

    return {
        (method.upper(), path)
        for path, item in probe["paths"].items()
        for method, operation in item.items()
        if "429" in (operation.get("responses") or {})
    }


def _reachable_by_operation() -> dict[_Operation, dict[str, str]]:
    """The whole rule, applied to the whole route table."""

    rate_limited = rate_limited_operations()
    measured: dict[_Operation, dict[str, str]] = {}

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        schema_path = _schema_path(route.path)
        # The canon documents the versioned surface, and `_operation_pairs` maps a canonical path
        # to `/api/v1{path}`. The four unversioned root routes therefore have no canonical
        # counterpart; mapping `/health` onto the canon's `/health` would silently merge them
        # with their versioned twins, which are different routes with different dependencies.
        canon_path = (
            schema_path[len("/api/v1") :] if schema_path.startswith("/api/v1") else None
        )
        statuses = reachable_statuses(route)
        for method in sorted(route.methods & _HTTP_METHODS):
            operation_statuses = dict(statuses)
            if (method, schema_path) in rate_limited:
                operation_statuses["429"] = "S5"
            measured[
                _Operation(
                    key=f"{method} {canon_path if canon_path is not None else schema_path}",
                    method=method,
                    schema_path=schema_path,
                    canon_path=canon_path,
                )
            ] = operation_statuses

    return measured


def undeclared_reachable(
    canonical: dict[str, Any], generated: dict[str, Any]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """`(canon side, generated side)`: every `(operation, status)` reachable and not declared.

    Both documents are arguments rather than loads, so a counter-check can hand in a mutated
    copy. Nothing in this module ever writes to `api/openapi.yaml`.
    """

    canon_side: set[tuple[str, str]] = set()
    generated_side: set[tuple[str, str]] = set()

    for operation, statuses in _reachable_by_operation().items():
        canonical_operation = (
            None
            if operation.canon_path is None
            else (canonical.get("paths") or {})
            .get(operation.canon_path, {})
            .get(operation.method.lower())
        )
        generated_operation = (
            (generated.get("paths") or {})
            .get(operation.schema_path, {})
            .get(operation.method.lower())
        )

        for document_operation, sink in (
            (canonical_operation, canon_side),
            (generated_operation, generated_side),
        ):
            if document_operation is None:
                # An operation missing from a document is a different finding with its own guard
                # (`RT-011-2`, the callable surface). Reporting every status of it here would
                # drown this list in a defect it does not describe.
                continue
            declared = {str(status) for status in (document_operation.get("responses") or {})}
            for status in statuses:
                if status not in declared:
                    sink.add((operation.key, status))

    return canon_side, generated_side


def _measure() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    return undeclared_reachable(_load_openapi_yaml(), _load_fastapi_openapi())


# ------------------------------------------------------------------------------------------
# The two fixed lists, and they are EMPTY. Closed 2026-08-23 by T1106's second half:
# 136 canon-side pairs over 78 operations and 127 generated-side over 79, all of them declared
# without one line of behaviour changing.
#
#   * the canon side by `api/openapi.yaml` itself - 70 x 403 through a new shared
#     `#/components/responses/Forbidden`, 54 x 422 through a new `UnprocessableEntity`,
#     10 x 422 through a new `SimulatorIdentityUnprocessable` (the S4-only operations, which
#     have no falsifiable parameter and so can answer 422 for exactly one reason), and the two
#     remaining 401s through the existing `Unauthorized`. Every body is `ErrorEnvelope`; no new
#     shape was invented.
#   * the generated side by ONE function - `_declare_auth_statuses` in `app/main.py`, the
#     sibling of `_declare_rate_limit_status`, deriving 401/403 from the same dependency closure
#     this file derives them from and `setdefault`-ing them into the emitted document. Not 79
#     decorator edits, and nothing a client receives changed.
#
# What the closure deliberately did NOT do, because it would have written falsehoods into
# authority number one: `app.openapi()` declares `422` on 13 operations where it is unreachable
# (FastAPI stamps it on any operation with any flat parameter, and on those the only parameter
# is the optional `X-Admin-Token`). Copying the generated document into the canon would have
# closed this list and made the canon wrong. Those 13 remain a generated-side over-declaration
# and a named cause in `ERROR_RESPONSE_DRIFT`; this guard is one-directional and cannot see
# them, which is the same property that protects `GET /admin/health/db` below.
#
# Empty is the point. Both lists may only shrink, so from here any new entry is a regression and
# the assertion names it. Do not repopulate either list to make a change pass.
#
# NOT in these lists, and it must stay that way: `GET /admin/health/db`. It declares `403`
# correctly and the rule is blind to it (see the module docstring). A later pass reading these
# lists as "the statuses to add" must not read the guard as "the statuses to remove".
# ------------------------------------------------------------------------------------------

UNDECLARED_REACHABLE_CANON: set[tuple[str, str]] = set()

# `app.openapi()` learns a status only from `responses=` on the decorator, and `401`/`403` are
# raised inside dependencies, so it knew about neither. Closed by `_declare_auth_statuses` in
# `app/main.py` rather than by 79 decorator edits - see the header above.
UNDECLARED_REACHABLE_GENERATED: set[tuple[str, str]] = set()


# ------------------------------------------------------------------------------------------
# The guard
# ------------------------------------------------------------------------------------------


def test_no_new_undeclared_reachable_status_in_the_canon() -> None:
    """The canon list may shrink. It may not grow."""

    measured, _generated = _measure()
    appeared = sorted(measured - UNDECLARED_REACHABLE_CANON)
    assert not appeared, (
        f"{len(appeared)} operation/status pair(s) are reachable and api/openapi.yaml does not "
        "declare them, so a client generated from authority number one has no branch for a "
        "status the service really returns. Declare each one with an `ErrorEnvelope` body - do "
        "not silence this by copying app.openapi(), which over-declares 422.\n  "
        + "\n  ".join(f"{key} -> {status}" for key, status in appeared)
    )


def test_the_canon_list_has_no_stale_entries() -> None:
    """Declared operations must leave the list, or it stops meaning anything."""

    measured, _generated = _measure()
    stale = sorted(UNDECLARED_REACHABLE_CANON - measured)
    assert not stale, (
        f"{len(stale)} entr(ies) in UNDECLARED_REACHABLE_CANON are no longer gaps; remove them "
        "so the list keeps stating a real debt:\n  "
        + "\n  ".join(f"{key} -> {status}" for key, status in stale)
    )


def test_no_new_undeclared_reachable_status_in_the_generated_schema() -> None:
    """The generated list may shrink. It may not grow.

    Kept separate from the canon list on purpose: `422` is a canon-only debt and `401` a
    generated-only one, so a single list would let one side's progress mask the other's
    regression.
    """

    _canon, measured = _measure()
    appeared = sorted(measured - UNDECLARED_REACHABLE_GENERATED)
    assert not appeared, (
        f"{len(appeared)} operation/status pair(s) are reachable and the schema the application "
        "generates does not declare them, so an SDK built from the running service has no "
        "branch for them:\n  "
        + "\n  ".join(f"{key} -> {status}" for key, status in appeared)
    )


def test_the_generated_list_has_no_stale_entries() -> None:
    _canon, measured = _measure()
    stale = sorted(UNDECLARED_REACHABLE_GENERATED - measured)
    assert not stale, (
        f"{len(stale)} entr(ies) in UNDECLARED_REACHABLE_GENERATED are no longer gaps; remove "
        "them:\n  " + "\n  ".join(f"{key} -> {status}" for key, status in stale)
    )


# ------------------------------------------------------------------------------------------
# Counter-checks. A ratchet nobody has tried to break is a list of strings.
# ------------------------------------------------------------------------------------------


def _synthetic_route(
    path: str,
    endpoint: Any,
    dependencies: list[Any] | None = None,
    methods: tuple[str, ...] = ("GET",),
) -> APIRoute:
    """A real `APIRoute`, so the counter-checks exercise the real dependant, not a stand-in."""

    return APIRoute(path, endpoint, methods=list(methods), dependencies=dependencies)


def test_the_predicate_detects_the_shapes_each_signal_names() -> None:
    """Guard the guard: every signal in the docstring must actually fire on its own shape."""

    def nothing_falsifiable() -> dict:
        return {}

    def required_query(equivalent: str) -> dict:
        return {}

    # S1: a required query parameter, and nothing else.
    assert reachable_statuses(_synthetic_route("/api/v1/x", required_query)) == {"422": "S1"}

    # S1b: the identical endpoint under an action path answers 400 and NOT 422.
    action = _synthetic_route(
        "/api/v1/simulator/runs/{run_id}/actions/x", required_query, methods=("POST",)
    )
    assert reachable_statuses(action) == {"400": "S1b"}, (
        "a simulator action route must derive 400 from validation, never 422"
    )

    # S2/S3: require_admin is 403 and never 401. This asymmetry is the rule's load-bearing
    # exclusion - `test_require_admin_really_answers_403_and_never_401` executes the claim.
    admin = _synthetic_route(
        "/api/v1/x", nothing_falsifiable, dependencies=[Depends(deps.require_admin)]
    )
    assert reachable_statuses(admin) == {"403": "S3"}, (
        "require_admin has no 401 branch; crediting one would add 29 unreachable statuses"
    )

    # S2 + S3 + S4: the simulator actor carries all three, with no falsifiable parameter of the
    # endpoint's own - the shape of the ten canon entries that rest on S4 alone.
    actor = _synthetic_route(
        "/api/v1/simulator/x",
        nothing_falsifiable,
        dependencies=[Depends(deps.require_simulator_actor)],
    )
    assert reachable_statuses(actor) == {"401": "S2", "403": "S3", "422": "S4"}

    # S1 and S4 together are one 422 with both provenances recorded.
    actor_with_query = _synthetic_route(
        "/api/v1/simulator/x",
        required_query,
        dependencies=[Depends(deps.require_simulator_actor)],
    )
    assert actor_with_query and reachable_statuses(actor_with_query)["422"] == "S1+S4"

    # S4 survives the S1b override: the action route's 400 is a RequestValidationError, the
    # simulator-owner 422 is a GeoException raised in a dependency, and they coexist.
    actor_action = _synthetic_route(
        "/api/v1/simulator/runs/{run_id}/actions/x",
        required_query,
        dependencies=[Depends(deps.require_simulator_actor)],
        methods=("POST",),
    )
    assert reachable_statuses(actor_action) == {
        "400": "S1b",
        "401": "S2",
        "403": "S3",
        "422": "S4",
    }

    # And a route with nothing at all derives nothing at all. Without this the rule could be
    # vacuously true-for-everything and every list entry would be noise.
    assert reachable_statuses(_synthetic_route("/api/v1/x", nothing_falsifiable)) == {}


def test_the_str_path_parameter_exemption_is_exactly_that_wide() -> None:
    """The one loophole-shaped exemption in the rule, held to its stated width.

    A plain `str` path parameter is exempt because it can neither be missing nor fail to coerce.
    Anything else in that position - a parsed type, or a constraint - is falsifiable, and if this
    exemption ever widened by accident the rule would quietly stop deriving `422` for the whole
    `/{id}` surface.
    """

    def plain(id: str) -> dict:
        return {}

    def parsed(id: uuid.UUID) -> dict:
        return {}

    def constrained(id: str = PathParam(min_length=2)) -> dict:
        return {}

    assert reachable_statuses(_synthetic_route("/api/v1/x/{id}", plain)) == {}, (
        "a plain str path parameter can never fail validation; crediting it with 422 would "
        "invent a status for every /{id} route in the surface"
    )
    assert reachable_statuses(_synthetic_route("/api/v1/x/{id}", parsed)) == {"422": "S1"}
    assert reachable_statuses(_synthetic_route("/api/v1/x/{id}", constrained)) == {"422": "S1"}

    # The mirror case in query position: optional and string-like is not falsifiable, optional
    # and coercible is, optional and constrained is. Requiring `required` alone missed 24 ops.
    def optional_string(q: str | None = None) -> dict:
        return {}

    def optional_int(page: int = 1) -> dict:
        return {}

    def optional_constrained_string(q: str = Query("x", min_length=2)) -> dict:
        return {}

    assert reachable_statuses(_synthetic_route("/api/v1/x", optional_string)) == {}
    assert reachable_statuses(_synthetic_route("/api/v1/x", optional_int)) == {"422": "S1"}
    assert reachable_statuses(
        _synthetic_route("/api/v1/x", optional_constrained_string)
    ) == {"422": "S1"}


def test_an_operation_that_over_declares_is_not_reported() -> None:
    """The guard is one-directional, and `GET /admin/health/db` is why.

    Its `403` is correct - the handler awaits `require_admin` in its own body, so a wrong
    `X-Admin-Token` really answers `403` - and the dependency graph cannot see it. A converse
    assertion ("declared implies reachable") would fail on that correct declaration, and the only
    way to make it pass would be to delete a true statement from authority number one.
    """

    canonical = _load_openapi_yaml()
    operation = canonical["paths"]["/admin/health/db"]["get"]
    assert "403" in (operation.get("responses") or {}), (
        "this counter-check is measuring nothing unless /admin/health/db still declares 403"
    )

    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/admin/health/db"
    )
    assert "403" not in reachable_statuses(route), (
        "the rule now sees the in-body require_admin call; if that is real, this counter-check "
        "has lost its example and the one-directional argument needs a new one"
    )

    canon_side, generated_side = _measure()
    assert not [key for key, _status in canon_side | generated_side if "health/db" in key], (
        "an over-declaring operation must never be reported - this guard asserts reachable "
        "implies declared and never the converse"
    )


def test_the_guard_bites_when_a_declaration_is_deleted() -> None:
    """Delete two statuses from a currently clean operation and require both back.

    `POST /simulator/runs/{run_id}/actions/tx-once` is one of the eight Interact Mode operations
    with no gaps on either side, so anything reported for it after the mutation came from the
    mutation. `403` proves the dependency-graph half; `429` proves S5 is genuinely wired through
    `_declare_rate_limit_status` and not merely imported.

    The mutation is on a deep copy. This module never writes to `api/openapi.yaml`.
    """

    key = "POST /simulator/runs/{run_id}/actions/tx-once"
    canonical = copy.deepcopy(_load_openapi_yaml())
    generated = _load_fastapi_openapi()

    before_canon, before_generated = undeclared_reachable(canonical, generated)
    assert not [pair for pair in before_canon if pair[0] == key], (
        f"{key} was expected to be clean before the mutation; this counter-check cannot "
        "distinguish its own damage otherwise"
    )

    responses = canonical["paths"]["/simulator/runs/{run_id}/actions/tx-once"]["post"]["responses"]
    for status in ("403", "429"):
        assert status in responses, f"{key} no longer declares {status}; pick another operation"
        del responses[status]

    after_canon, after_generated = undeclared_reachable(canonical, generated)
    assert {pair for pair in after_canon if pair[0] == key} == {(key, "403"), (key, "429")}, (
        "the guard did not report exactly the two deleted statuses: "
        f"{sorted(pair for pair in after_canon if pair[0] == key)}"
    )
    assert after_generated == before_generated, (
        "mutating the canon moved the generated side; the two documents are measured "
        "independently and a canon edit must not change what app.openapi() is missing. "
        "(Both sides are empty now, so this reads as 'still empty'; it was written when tx-once "
        "carried a generated-side 401 gap and the point was that a canon edit left it alone.)"
    )

    # And the ratchet built on it fails, with the pair named in the message.
    appeared = sorted(after_canon - UNDECLARED_REACHABLE_CANON)
    assert appeared == [(key, "403"), (key, "429")], appeared


async def test_require_admin_really_answers_403_and_never_401(client) -> None:
    """Anti-vacuum: the rule's most consequential exclusion, observed rather than read.

    S2 omits `require_admin`. If someone ever adds a `401` branch to it, the rule silently
    under-reports 29 admin operations and nothing else in the suite would notice - the lists
    would still be green, and would still be fiction.
    """

    for headers, description in (
        ({}, "no token"),
        ({"X-Admin-Token": "not-the-token"}, "a wrong token"),
    ):
        response = await client.get("/api/v1/admin/participants", headers=headers)
        assert response.status_code == 403, (
            f"require_admin answered {response.status_code} with {description}; the rule derives "
            "403 and NOT 401 from it, and both halves of that would now be wrong"
        )
        assert response.json()["error"]["code"] == "E006"


async def test_a_malformed_simulator_owner_header_really_answers_422(client) -> None:
    """Anti-vacuum: S4 is the one signal with no FastAPI machinery behind it.

    Twenty-six `422` declarations in `api/openapi.yaml` - and all ten that rest on S4 alone,
    which is why `SimulatorIdentityUnprocessable` exists - are true only because
    `require_simulator_actor` raises `GeoException(..., status_code=422)` on a malformed
    `X-Simulator-Owner`. If the regex or that status code changes, the canon becomes fiction at
    twenty-six sites, and a guard reading the route table would never find out.

    `GET /simulator/runs/active` is deliberate: its only inputs are two optional string headers,
    so it has no falsifiable parameter at all and S1 derives nothing for it.
    """

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN, "X-Simulator-Owner": "bad owner!"}
    response = await client.get("/api/v1/simulator/runs/active", headers=headers)

    assert response.status_code == 422, (
        f"a malformed X-Simulator-Owner answered {response.status_code}, not 422; the S4 signal "
        "and every list entry resting on it would need re-deriving"
    )
    body = response.json()["error"]
    assert body["code"] == "E009"
    assert body["details"]["header"] == "X-Simulator-Owner"

    # And the control: the same route with a well-formed owner does not answer 422, so the test
    # above is measuring the header check rather than something wrong with the route.
    ok = await client.get(
        "/api/v1/simulator/runs/active",
        headers={"X-Admin-Token": settings.ADMIN_TOKEN, "X-Simulator-Owner": "good-owner"},
    )
    assert ok.status_code != 422


def test_s5_is_the_applications_own_derivation() -> None:
    """Counter-check: the `429` set comes from `app/main.py`, exemption included.

    If this guard ever grew its own copy of the limiter condition, the two could disagree about
    `_RATE_LIMIT_EXEMPT_PATHS` and both stay green.
    """

    limited = rate_limited_operations()
    assert limited, "the limiter covers no operation at all; S5 has gone vacuous"
    assert ("POST", "/api/v1/simulator/session/ensure") not in limited, (
        "the exempt path picked up a 429; declaring one for it is the very defect 011 catalogues"
    )
    assert ("GET", "/api/v1/health") in limited
