"""Post-processing of the generated OpenAPI document (moved out of `app/main.py` by 032 `S3`, unchanged).

`get_openapi` learns a response only from `responses=` on a route decorator. Three things the
application answers are not there, so they are added here to the generated document, each derived
from the route table rather than listed: the GEO error envelope for validation failures, `429`
where the rate limiter can answer, `401`/`403` where an auth dependency can.

`install_openapi(app)` is the one entry point; `declare_rate_limit_status` and
`declare_auth_statuses` are public because `tests/contract/test_p011_reachable_statuses_are_declared.py`
runs the first on a probe document to learn the set the application derives.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.schemas.common import ErrorEnvelope


def _register_openapi_model_schema(document: dict, model: type) -> None:
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    model_schema = model.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    definitions = model_schema.pop("$defs", {})
    for name, schema in definitions.items():
        schemas.setdefault(name, schema)
    schemas.setdefault(model.__name__, model_schema)


def _is_default_fastapi_validation_response(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    schema = (
        ((response.get("content") or {}).get("application/json") or {}).get("schema")
    )
    return schema == {"$ref": "#/components/schemas/HTTPValidationError"}


def _custom_openapi(app: FastAPI) -> dict:
    if app.openapi_schema is not None:
        return app.openapi_schema

    document = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    from app.schemas.simulator import SimulatorActionError

    _register_openapi_model_schema(document, ErrorEnvelope)
    _register_openapi_model_schema(document, SimulatorActionError)

    for path, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        is_simulator_action = path.startswith("/api/v1/simulator/runs/") and (
            "/actions/" in path
        )
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses") or {}
            validation_response = responses.get("422")
            if not _is_default_fastapi_validation_response(validation_response):
                continue
            responses.pop("422")
            if is_simulator_action:
                responses.setdefault(
                    "400",
                    {
                        "description": "Simulator action validation error",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/SimulatorActionError"
                                }
                            }
                        },
                    },
                )
            responses["422"] = {
                "description": (
                    "Invalid simulator identity transport"
                    if is_simulator_action
                    else "Validation error"
                ),
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                    }
                },
            }

    declare_rate_limit_status(app, document)
    declare_auth_statuses(app, document)

    app.openapi_schema = document
    return document


def declare_rate_limit_status(app: FastAPI, document: dict) -> None:
    """Declare 429 on exactly the operations the limiter can answer for (011/T1103a).

    `F-011-3`: every HTTP router is mounted with `Depends(deps.rate_limit)`, so nearly the whole
    surface can return 429, yet the canon named it once in the entire document.  The set is
    derived from the route table rather than listed, so it cannot fall out of step with how the
    routers are mounted - and `_RATE_LIMIT_EXEMPT_PATHS` is honoured, because declaring 429 for a
    route the limiter returns early on would be the very defect this program catalogues.
    """

    import re

    from fastapi.routing import APIRoute

    from app.api import deps

    def schema_path(path: str) -> str:
        # Starlette keeps the converter (`/participants/{pid:path}`); OpenAPI does not. Without
        # this the operation silently misses its 429 because the keys never match.
        return re.sub(r"\{([^{}:]+):[^{}]+\}", lambda m: "{" + m.group(1) + "}", path)

    limited: dict[str, set[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in deps._RATE_LIMIT_EXEMPT_PATHS:
            continue
        if not any(
            dependency.call is deps.rate_limit
            for dependency in route.dependant.dependencies
        ):
            continue
        limited.setdefault(schema_path(route.path), set()).update(
            method.lower() for method in route.methods
        )

    for path, path_item in (document.get("paths") or {}).items():
        methods = limited.get(path)
        if not methods or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in methods or not isinstance(operation, dict):
                continue
            # setdefault on the status too: a route that declares its own 429 keeps it. Nothing
            # does today, so this is latent rather than observable - but silently replacing a
            # route-level declaration is exactly the kind of thing that gets noticed a year later.
            operation.setdefault("responses", {}).setdefault(
                "429",
                {
                    "description": "Rate limit exceeded",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                        }
                    },
                },
            )


def declare_auth_statuses(app: FastAPI, document: dict) -> None:
    """Declare 401/403 on exactly the operations whose dependencies can answer them (011/T1106).

    The sibling of `declare_rate_limit_status`, and for the same reason. `get_openapi` learns a
    status only from `responses=` on the decorator, and `401`/`403` are never raised by a handler
    here - they come out of `GeoException` subclasses raised *inside* dependencies. So the schema
    the application publishes was silent about them on 79 operations, and an SDK generated from
    the running service had no branch for a status that surface really returns.

    The alternative was `responses={401: ..., 403: ...}` on 79 route decorators. This is one
    place, and - exactly as with the limiter - the *set of operations* is derived from the route
    table rather than listed, so it cannot fall out of step with how the routers are mounted.
    What is named literally is the same thing `declare_rate_limit_status` names literally: the
    dependency callables themselves.

    Two things about that naming are load-bearing and were measured, not read:

    * `require_admin` is in the 403 set and **not** in the 401 set. It has no `Unauthorized`
      branch at all; every failure path raises `ForbiddenException`. Measured: no token and a
      wrong token both answer `403` on `GET /api/v1/admin/participants`. Treating "auth
      dependency" as one category would stamp an unreachable `401` onto 29 admin operations -
      describing a status the service never returns, which is the worse half of this programme's
      defect.
    * `get_current_participant` is in the 403 set as well as the 401 set, because a participant
      whose `status != 'active'` gets `ForbiddenException("Participant account is not active")`.
      `POST /admin/participants/{pid}/freeze` is that branch's operator writer (ban/unban removed in 032 F-5).

    The closure is walked to any depth. `require_simulator_actor` pulls in its own
    sub-dependencies, and a router-level `Depends` sits at the same level as an endpoint-level one
    only by accident of how the routers happen to be mounted today.

    `tests/contract/test_p011_reachable_statuses_are_declared.py` re-derives this set
    independently from the same route table and fails if the two ever disagree.

    **The body of a `401` is not always the envelope** (011/T1109). A security scheme runs inside
    FastAPI's dependency solver, before any application code and therefore before the exception
    handlers that produce `ErrorEnvelope`. `deps.reusable_oauth2` is
    `OAuth2PasswordBearer(auto_error=True)`, so on the operations that depend on it a request with
    no `Authorization` header - or one whose scheme is not `Bearer` - is refused by the scheme
    itself with FastAPI's own flat `{"detail": "Not authenticated"}`, and
    `get_current_participant` never runs. Only a request that *does* carry a `Bearer` token
    reaches it and gets the envelope. Both halves were measured on all 20 of those operations,
    and declaring only the envelope made this document reject the commonest `401` the service
    sends. So those operations declare the union, and every other `401` here keeps the plain
    envelope: the scheme on the simulator and integrity surfaces is `auto_error=False`, returns
    `None`, and every `401` there comes from application code.

    That set is derived like the others - any `SecurityBase` in the closure whose `auto_error` is
    true, not a list of paths and not a name-check on `reusable_oauth2`. `api/openapi.yaml` states
    the same union at `components/responses/UnauthorizedBearer`, so the two documents converge on
    it instead of drifting apart.
    """

    import re

    from fastapi.routing import APIRoute
    from fastapi.security.base import SecurityBase

    from app.api import deps

    # Dependencies with at least one `UnauthorizedException` path -> 401.
    authentication = {
        deps.get_current_participant,
        deps.require_participant_or_admin,
        deps.require_simulator_actor,
    }
    # Dependencies with at least one `ForbiddenException` path -> 403.
    authorisation = {
        deps.require_admin,
        deps.require_participant_or_admin,
        deps.require_simulator_actor,
        deps.get_current_participant,
    }

    def schema_path(path: str) -> str:
        # Starlette keeps the converter (`/participants/{pid:path}`); OpenAPI does not.
        return re.sub(r"\{([^{}:]+):[^{}]+\}", lambda m: "{" + m.group(1) + "}", path)

    def dependency_closure(dependant) -> set:
        found: set = set()
        stack = list(dependant.dependencies)
        while stack:
            sub = stack.pop()
            if sub.call is not None:
                found.add(sub.call)
            stack.extend(sub.dependencies)
        return found

    def envelope(description: str) -> dict:
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                }
            },
        }

    def envelope_or_detail(description: str) -> dict:
        # The union `api/openapi.yaml` spells at `components/responses/UnauthorizedBearer`. The
        # generated document has no `components/responses`, so it is stated inline here; the
        # contract gate resolves `$ref`s before comparing, and the two sides normalize equal.
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/ErrorEnvelope"},
                            {
                                "type": "object",
                                "required": ["detail"],
                                "properties": {"detail": {"type": "string"}},
                            },
                        ]
                    }
                }
            },
        }

    def scheme_answers_first(calls: set) -> bool:
        # An `auto_error=True` security scheme raises FastAPI's own `HTTPException` from inside
        # the solver, so its body is the flat `{"detail": ...}` rather than an `ErrorEnvelope`.
        return any(
            isinstance(call, SecurityBase) and getattr(call, "auto_error", False)
            for call in calls
        )

    reachable: dict[str, dict[str, set[str]]] = {}
    # The subset of the `401` operations whose flat shape is reachable. Kept apart from
    # `reachable` rather than folded into it as a pseudo-status, so that the reachability rule
    # above stays a statement about statuses and this stays a statement about bodies.
    flat_401: dict[str, set[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls = dependency_closure(route.dependant)
        hard_scheme = scheme_answers_first(calls)
        statuses = set()
        if calls & authentication:
            statuses.add("401")
        if hard_scheme:
            # The scheme can answer `401` on its own, whatever the dependency behind it does.
            # Measured as a no-op today - all 20 such routes also depend on
            # `get_current_participant` - but the rule is about the scheme, not about them.
            statuses.add("401")
        if calls & authorisation:
            statuses.add("403")
        if not statuses:
            continue
        by_method = reachable.setdefault(schema_path(route.path), {})
        for method in route.methods:
            by_method.setdefault(method.lower(), set()).update(statuses)
        if hard_scheme:
            flat_401.setdefault(schema_path(route.path), set()).update(
                method.lower() for method in route.methods
            )

    for path, path_item in (document.get("paths") or {}).items():
        by_method = reachable.get(path)
        if not by_method or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            statuses = by_method.get(method)
            if not statuses or not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            # setdefault on the status, as with 429: a route that declares its own 401 or 403
            # keeps it, and this is observable rather than latent. Six of the eight Interact Mode
            # action routes declare a 403 whose body is
            # `oneOf[SimulatorActionError, ErrorEnvelope]` - the handler's own guard answers flat
            # while `require_simulator_actor` raises before it and the global handler wraps the
            # result. Overwriting that with the plain envelope below would delete a true statement
            # from the published schema.
            if "401" in statuses:
                if method in flat_401.get(path, ()):
                    responses.setdefault(
                        "401",
                        envelope_or_detail(
                            "No usable bearer credential. Two shapes are reachable: a missing "
                            "Authorization header, or one carrying a scheme other than Bearer, "
                            "is refused by the security scheme itself with the flat "
                            '{"detail": "Not authenticated"}; a Bearer token that is present '
                            "but rejected reaches get_current_participant and is answered with "
                            "ErrorEnvelope (E006)."
                        ),
                    )
                else:
                    responses.setdefault("401", envelope("Unauthorized"))
            if "403" in statuses:
                responses.setdefault(
                    "403",
                    envelope(
                        "The credentials were recognised and are not permitted here: a "
                        "missing or wrong X-Admin-Token, a cookie-auth simulator request "
                        "whose Origin is not in the CSRF allowlist, or a participant whose "
                        "account is not active."
                    ),
                )



def install_openapi(app: FastAPI) -> None:
    """Make `app.openapi()` return the post-processed document (cached on `app.openapi_schema`)."""

    app.openapi = lambda: _custom_openapi(app)
