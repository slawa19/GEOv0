from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response

from app.api.router import api_router
from app.api.v1 import health
from app.config import settings
from app.db.session import engine
from app.schemas.common import ErrorEnvelope
from app.utils.error_codes import ERROR_MESSAGES, ErrorCode
from app.utils.exceptions import GeoException
from app.utils.request_id import new_request_id, request_id_var, validate_request_id
from app.core.maintenance_jobs import _start_configured_background_tasks


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = None
    app.state._bg_stop_event = asyncio.Event()
    app.state._bg_tasks = []
    app.state.background_jobs = {}

    # §12 Recovery reconciliation: mark simulator runs that were still active
    # before the previous server process died as 'error'.  Best-effort — any
    # exception here must NOT prevent the server from starting.
    try:
        from app.core.simulator.storage import reconcile_stale_runs

        _reconciled = await reconcile_stale_runs()
        if _reconciled:
            logger.warning(
                "lifespan.simulator_reconcile reconciled=%d stale run(s) on startup",
                _reconciled,
            )
    except Exception:
        logger.exception("lifespan.simulator_reconcile_failed (non-fatal)")

    if settings.REDIS_ENABLED:
        import redis.asyncio as redis
        from app.utils import security

        client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        try:
            await client.ping()
        except Exception as exc:
            await client.aclose()
            raise RuntimeError("Redis enabled but unavailable") from exc

        app.state.redis = client
        security.set_redis_client(client)

    # These maintenance jobs are degradable: startup continues, while job state,
    # logs, metrics, and /health expose their absence or unexpected exit.
    _start_configured_background_tasks(app)

    try:
        yield
    finally:
        # Mark shutdown before awaiting other components so normal task exits and
        # cancellations are not reported as unexpected failures.
        app.state._bg_stop_event.set()

        # Maintenance jobs can still be inside a DB session or Redis-backed
        # distributed lock. Cancel and join them before tearing down either
        # resource so their context-manager cleanup remains valid.
        tasks = list(getattr(app.state, "_bg_tasks", []) or [])
        if tasks:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        app.state._bg_tasks = []

        # Simulator runtime graceful shutdown (best-effort).
        try:
            from app.core.simulator.runtime import runtime

            await runtime.shutdown()
        except Exception:
            logger.exception("simulator.runtime.shutdown_failed")

        from app.utils import security

        security.set_redis_client(None)
        client = getattr(app.state, "redis", None)
        if client is not None:
            try:
                await client.aclose()
            finally:
                app.state.redis = None

        # Ensure DB connections/threads are cleaned up when the app shuts down
        # (important for pytest TestClient runs on Windows).
        try:
            await engine.dispose()
        except Exception:
            pass


app = FastAPI(title="GEO Hub Backend", debug=settings.DEBUG, lifespan=lifespan)

# CORS middleware configuration for dev environment
app.add_middleware(
    CORSMiddleware,
    # Allow local dev servers (Vite) on any port, but only on localhost.
    # This avoids fragile port-specific CORS issues on Windows.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_with_request_id(content: dict) -> dict:
    """The GEO error envelope with the request id beside the code (024 `T2414.2`, AGENTS.md §12)."""
    rid = request_id_var.get()
    if rid is not None:
        content["error"]["request_id"] = rid
    return content


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming_rid = request.headers.get("X-Request-ID")
    rid = validate_request_id(incoming_rid) or new_request_id()
    token = request_id_var.set(rid)
    # Also on the request state: an exception no handler answered reaches `unhandled_exception_handler` in
    # Starlette's outermost middleware, after this function - and the context variable - have been left.
    request.state.request_id = rid
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)

    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if not settings.METRICS_ENABLED:
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    elapsed_s = time.perf_counter() - start

    try:
        from app.utils.metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION_SECONDS

        route = request.scope.get("route")
        # IMPORTANT: keep Prometheus label cardinality low.
        # - Matched routes: use the route template (e.g. "/api/v1/payments/{payment_id}").
        # - Unmatched routes (no route in scope / no template): use a fixed label.
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str) and route_path:
            path_label = route_path
        else:
            path_label = "__unmatched__"
        method = request.method
        status = str(getattr(response, "status_code", 0))

        HTTP_REQUESTS_TOTAL.labels(method=method, path=path_label, status=status).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path_label).observe(elapsed_s)
    except Exception:
        pass

    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """An exception no handler answered (024 `T2414.2`): logged once under the request id with its traceback; the
    caller gets the internal-error envelope with that id and none of the exception's text. Starlette still re-raises
    the exception to the server after this response, as it did before."""
    rid = getattr(request.state, "request_id", None) or new_request_id()
    logger.error(
        "http.unhandled_error request_id=%s method=%s path=%s",
        rid,
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    internal = {"error": {"code": ErrorCode.E010.value, "message": ERROR_MESSAGES[ErrorCode.E010], "request_id": rid}}
    return JSONResponse(status_code=500, content=internal, headers={"X-Request-ID": rid})


@app.exception_handler(GeoException)
async def geo_exception_handler(request: Request, exc: GeoException):
    return JSONResponse(status_code=exc.status_code, content=_error_with_request_id(exc.to_dict()))


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    path = str(getattr(request.url, "path", "") or "")

    # Simulator Interact Mode action endpoints use a different error envelope.
    # Keep schema validation errors stable and aligned with simulator-ui expectations.
    if path.startswith("/api/v1/simulator/runs/") and "/actions/" in path:
        from app.schemas.simulator import SimulatorActionError

        payload = SimulatorActionError(
            code="INVALID_REQUEST",
            message="Invalid request",
            details={"errors": exc.errors()},
        ).model_dump(mode="json")
        return JSONResponse(status_code=400, content=payload)

    # Default: unify FastAPI/Pydantic validation errors into GEO error envelope.
    # Spec: E009 (Invalid input).
    return JSONResponse(
        status_code=422,
        content=_error_with_request_id(
            {
                "error": {
                    "code": ErrorCode.E009.value,
                    "message": ERROR_MESSAGES[ErrorCode.E009],
                    "details": {"errors": exc.errors()},
                }
            }
        ),
    )


app.include_router(api_router, prefix="/api/v1")
# The same three public health routes at the root, without the /api/v1 rate limit: the container healthcheck
# and probes call /health, /healthz and /health/db. One router, `app/api/v1/health.py` (024 `T2414.2`).
app.include_router(health.router, tags=["Health"])


if settings.METRICS_ENABLED:

    @app.get("/metrics")
    async def metrics():
        from app.utils.metrics import render_metrics

        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)


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


def _custom_openapi() -> dict:
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

    _declare_rate_limit_status(document)
    _declare_auth_statuses(document)

    app.openapi_schema = document
    return document


def _declare_rate_limit_status(document: dict) -> None:
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


def _declare_auth_statuses(document: dict) -> None:
    """Declare 401/403 on exactly the operations whose dependencies can answer them (011/T1106).

    The sibling of `_declare_rate_limit_status`, and for the same reason. `get_openapi` learns a
    status only from `responses=` on the decorator, and `401`/`403` are never raised by a handler
    here - they come out of `GeoException` subclasses raised *inside* dependencies. So the schema
    the application publishes was silent about them on 79 operations, and an SDK generated from
    the running service had no branch for a status that surface really returns.

    The alternative was `responses={401: ..., 403: ...}` on 79 route decorators. This is one
    place, and - exactly as with the limiter - the *set of operations* is derived from the route
    table rather than listed, so it cannot fall out of step with how the routers are mounted.
    What is named literally is the same thing `_declare_rate_limit_status` names literally: the
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
      `POST /admin/participants/{pid}/ban` and `/freeze` are that branch's writers.

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


app.openapi = _custom_openapi
