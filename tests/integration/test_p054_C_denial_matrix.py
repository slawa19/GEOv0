"""054-C-7 (2026-10-30): the HTTP refusal of every protected route, enumerated from `app.routes`.

The protection itself is structural - `require_admin` on the admin router (`app/api/v1/admin.py`), on four
integrity routes and in the body of `GET /admin/health/db`; `get_current_participant` on the participant routes;
`require_simulator_actor` plus `actor.is_admin` on `/simulator/admin/*` (`app/api/v1/simulator.py`) - but most
routes had no test that a request without credentials is refused. The matrix is built from the application's
routes and their dependency graphs, not from a hand list, so a route added later lands in it by itself; a route that
depends on none of the guards must be named in `PUBLIC` with its reason, or `test_every_route_is_classified` fails.

What it sees: the status (and, for the admin refusals, the refusal's own message) of a request with no credentials,
with a wrong admin token, and - on `/simulator/admin/*` - with a valid anonymous visitor cookie. What it does not
see: refusals that need a real participant (another participant's resource, an inactive account), the CSRF origin
check, and anything a route does after authentication. The class comes from the guard the route has, so the
matrix proves that each route's guard refuses, not that each route has the right guard: outside the
`/api/v1/admin` prefix (which `_classify` pins to admin by path) a route whose guard is downgraded (e.g. integrity
`require_admin` -> `get_current_participant`) is reclassified and stays green unless it is named in `MUST_CONTAIN`; an admin check in a handler body outside `/simulator/admin/*` (e.g. scenario upload, covered by
`tests/integration/test_p044_t4405_run_isolation.py`) is tested only as a simulator route; and only `APIRoute`s
are enumerated - a future mount, `add_route` or websocket route escapes `test_every_route_is_classified`.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from fastapi.routing import APIRoute

from app.api import deps
from app.config import settings
from app.core.simulator.session import COOKIE_NAME
from app.main import app

ORIGIN = "http://p054-c.test"
WRONG_ADMIN = {"X-Admin-Token": "p054-c-not-the-admin-token-0123456789"}
_PATH_VALUE = str(uuid.UUID("00000000-0000-4000-8000-000000000054"))

#: Routes that depend on none of the guards, each with the reason it is public. A new unguarded route fails
#: `test_every_route_is_classified` until it is added here - with its reason - or given a guard.
PUBLIC: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/auth/challenge"): "login step 1: issues a challenge to an unauthenticated caller",
    ("POST", "/api/v1/auth/login"): "login step 2: exchanges a signed challenge for tokens",
    ("POST", "/api/v1/auth/refresh"): "authenticated by the refresh token in its body, not by a header",
    ("POST", "/api/v1/participants"): "registration: the caller has no account yet",
    ("GET", "/api/v1/health"): "readiness probe",
    ("GET", "/api/v1/healthz"): "liveness probe",
    ("GET", "/api/v1/health/db"): "public database probe",
    ("POST", "/api/v1/simulator/session/ensure"): "issues the anonymous visitor cookie itself",
    ("GET", "/health"): "readiness probe (root alias)",
    ("GET", "/healthz"): "liveness probe (root alias)",
    ("GET", "/health/db"): "public database probe (root alias)",
    ("GET", "/metrics"): "Prometheus scrape endpoint, present only with METRICS_ENABLED (app/main.py)",
}

#: Admin routes whose refusal WITHOUT a token is not 403, each with the status and the reason. Not an exclusion:
#: the case still runs and asserts this status, and the wrong-token case still asserts 403.
NO_TOKEN_STATUS_OVERRIDES: dict[tuple[str, str], tuple[int, str]] = {
    ("GET", "/api/v1/admin/health/db"): (
        422,
        "the canon declares X-Admin-Token `required: true` (api/openapi.yaml, /admin/health/db) and the handler "
        "takes it as a required Header, calling require_admin in its body (app/api/v1/health.py): a missing header "
        "is a validation error before the handler runs; a wrong token is 403",
    ),
}

#: Known routes the matrix must contain by name: a lower bound, so an enumeration that silently lost a class (a
#: renamed guard, a broken dependency walk) fails instead of passing on fewer cases. Not a count.
MUST_CONTAIN: dict[str, set[tuple[str, str]]] = {
    "admin": {
        ("PATCH", "/api/v1/admin/config"),
        ("POST", "/api/v1/admin/participants/{pid}/freeze"),
        ("POST", "/api/v1/admin/participants/{pid}/unfreeze"),
        ("GET", "/api/v1/admin/equivalents"),
        ("POST", "/api/v1/admin/equivalents"),
        ("PATCH", "/api/v1/admin/equivalents/{code}"),
        ("DELETE", "/api/v1/admin/equivalents/{code}"),
        ("POST", "/api/v1/admin/equivalents/{code}/integrity-hold/clear"),
        ("GET", "/api/v1/admin/equivalents/{code}/usage"),
        ("GET", "/api/v1/admin/health/db"),
        ("GET", "/api/v1/integrity/status"),
        ("POST", "/api/v1/integrity/verify"),
    },
    "participant_or_admin": {("GET", "/api/v1/integrity/summary")},
    "participant": {
        ("GET", "/api/v1/balance"),
        ("GET", "/api/v1/balance/debts"),
        ("POST", "/api/v1/payments"),
        ("GET", "/api/v1/payments"),
        ("GET", "/api/v1/payments/{tx_id}"),
        ("GET", "/api/v1/clearing/cycles"),
        ("POST", "/api/v1/clearing/auto"),
        ("GET", "/api/v1/equivalents"),
        ("GET", "/api/v1/participants/me"),
        ("PATCH", "/api/v1/participants/me"),
        ("POST", "/api/v1/trustlines"),
    },
    "simulator": {("POST", "/api/v1/simulator/runs"), ("GET", "/api/v1/simulator/runs/{run_id}")},
    "simulator_admin": {("GET", "/api/v1/simulator/admin/runs"), ("POST", "/api/v1/simulator/admin/runs/stop-all")},
}


def _guards(dependant, found: set) -> set:
    if dependant.call is not None:
        found.add(dependant.call)
    for sub in dependant.dependencies:
        _guards(sub, found)
    return found


def _classify(route: APIRoute) -> str | None:
    guards = _guards(route.dependant, set())
    if route.path.startswith("/api/v1/admin") or deps.require_admin in guards:
        return "admin"
    if deps.require_participant_or_admin in guards:
        return "participant_or_admin"
    if deps.get_current_participant in guards:
        return "participant"
    if deps.require_simulator_actor in guards:
        return "simulator_admin" if route.path.startswith("/api/v1/simulator/admin/") else "simulator"
    return None


def _routes() -> list[tuple[str, str, str, APIRoute]]:
    out = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((_classify(route) or "public", method, route.path, route))
    return out


ROUTES = _routes()


def _url(route: APIRoute) -> str:
    values = {}
    for field in route.dependant.path_params:
        annotation = getattr(field.field_info, "annotation", None)
        values[field.name] = "1" if inspect.isclass(annotation) and issubclass(annotation, int) else _PATH_VALUE
    return route.path_format.format(**values)


#: (class, credentials) -> expected status. `none`: no credentials at all; `wrong_admin`: a wrong X-Admin-Token;
#: `visitor`: a valid anonymous simulator cookie (and an allowlisted Origin, so the CSRF check is not what refuses).
EXPECTED: dict[tuple[str, str], int] = {
    ("admin", "none"): 403,
    ("admin", "wrong_admin"): 403,
    ("participant_or_admin", "none"): 401,
    ("participant_or_admin", "wrong_admin"): 403,
    ("participant", "none"): 401,
    ("simulator", "none"): 401,
    ("simulator_admin", "none"): 401,
    ("simulator_admin", "visitor"): 403,
}

CASES = [
    pytest.param(cls, cred, method, path, route, id=f"{cred}-{method}-{path}")
    for cls, method, path, route in ROUTES
    for (expected_cls, cred) in EXPECTED
    if expected_cls == cls
]


def test_every_route_is_classified() -> None:
    unclassified = {(m, p) for cls, m, p, _r in ROUTES if cls == "public"} - set(PUBLIC)
    assert not unclassified, (
        "routes that depend on no guard (require_admin, require_participant_or_admin, get_current_participant, "
        f"require_simulator_actor) and are not named in PUBLIC: {sorted(unclassified)}. Give the route a guard, "
        "or add it to PUBLIC with the reason it is public"
    )


def test_the_matrix_contains_the_known_routes_by_name() -> None:
    present: dict[str, set[tuple[str, str]]] = {}
    for cls, method, path, _route in ROUTES:
        present.setdefault(cls, set()).add((method, path))
    missing = {cls: sorted(known - present.get(cls, set())) for cls, known in MUST_CONTAIN.items()}
    assert not any(missing.values()), f"the enumeration lost known routes of a class: {missing}"
    covered = {(c.values[2], c.values[3]) for c in CASES}
    assert set().union(*MUST_CONTAIN.values()) <= covered, "a known route has no refusal case"
    # The overrides must still name real admin routes, or they excuse nothing and hide a rename.
    assert set(NO_TOKEN_STATUS_OVERRIDES) <= present.get("admin", set()), NO_TOKEN_STATUS_OVERRIDES


@pytest.mark.asyncio
@pytest.mark.parametrize(("cls", "cred", "method", "path", "route"), CASES)
async def test_a_request_without_valid_credentials_is_refused(
    client, monkeypatch, cls: str, cred: str, method: str, path: str, route: APIRoute
) -> None:
    # The stand must be able to see a missing admin refusal: the dev bypass of require_admin would hide it.
    assert not (settings.ENV == "dev" and settings.ADMIN_DEV_MODE), "the admin dev bypass is on in the tier"
    monkeypatch.setattr("app.config.settings.SIMULATOR_CSRF_ORIGIN_ALLOWLIST", ORIGIN)

    client.cookies.clear()
    headers: dict[str, str] = {}
    if cred == "wrong_admin":
        headers.update(WRONG_ADMIN)
    elif cred == "visitor":
        ensured = await client.post("/api/v1/simulator/session/ensure", headers={"Origin": ORIGIN})
        assert ensured.status_code == 200 and ensured.json()["actor_kind"] == "anon", ensured.text
        assert client.cookies.get(COOKIE_NAME), "session/ensure set no cookie"
        headers["Origin"] = ORIGIN

    expected = EXPECTED[(cls, cred)]
    if cred == "none" and (method, path) in NO_TOKEN_STATUS_OVERRIDES:
        expected = NO_TOKEN_STATUS_OVERRIDES[(method, path)][0]

    response = await client.request(method, _url(route), headers=headers)

    assert response.status_code == expected, (cls, cred, method, path, response.status_code, response.text)
    if expected == 403:
        # The refusal must be the guard's, not some other 403 (the CSRF origin check, an inactive account).
        message = response.json()["error"]["message"]
        wanted = "Admin access required" if cls == "simulator_admin" else "Admin token required"
        assert message == wanted, (method, path, message)
