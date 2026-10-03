"""RT-011-4: the root routes really are outside the shared policy gate, and the exempt list is inert for them.

Program 011, finding `F-011-4`, recorded behaviour of decision `T1103b` (2026-08-23: keep as is).

`/metrics` is declared with `@app.get` and `/health`, `/healthz`, `/health/db` are the public health
router mounted at the root without dependencies (`app/main.py`, 024 `T2414.2`), so they never inherit `dependencies=[Depends(deps.rate_limit)]` from `api_router`.  The finding
worth keeping is the consequence: adding their paths to `_RATE_LIMIT_EXEMPT_PATHS` cannot change
anything, because that list is consulted *inside* `rate_limit`, which is never called for them.
A remediation written against that list would look like a fix and do nothing.

**This file is a counter-check, not a reproducer, and the distinction is the point.** The spec's
first `RT-011-4` asked for a test proving the exempt list inert - a test that is green on today's
code, which makes it a pin rather than a red. The reproducer role was reassigned to "a root route
passes through the shared gate", and `T1103b` then decided that root routes stay outside the
limiter: liveness and readiness probes and the Prometheus scrape share one per-IP bucket with user
traffic, so throttling them trades observability for nothing. So what is left to protect is the
current behaviour plus the inertness, and both are asserted here.

The third assertion is the anti-vacuum one external review asked for: a test that only ever says
"no dependency here" would stay green if the limiter were removed from the whole application. So
this file also pins that `rate_limit` is still genuinely wired on a versioned business route.
`RT-011-3` separately drives a real operation to a real `429`.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.api import deps
from app.main import app

ROOT_ROUTES = {"/metrics", "/health", "/healthz", "/health/db"}


def _route(path: str) -> APIRoute:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route
    raise AssertionError(f"route {path} is not registered")


def _has_rate_limit(route: APIRoute) -> bool:
    return any(
        dependency.call is deps.rate_limit for dependency in route.dependant.dependencies
    )


def test_adding_root_paths_to_the_exempt_list_would_change_nothing() -> None:
    """The inertness the finding is actually about, and the current behaviour T1103b pinned.

    `_RATE_LIMIT_EXEMPT_PATHS` is read inside `rate_limit`. For a route that never calls
    `rate_limit`, membership in that list is unreachable code, so a fix expressed that way is a
    fix in appearance only. (025 `T2504.2`: the separate pin "root routes do not go through
    `rate_limit`" was a duplicate of the loop below and was removed; its message is kept there.)
    """

    exempt_is_read_inside_rate_limit = "_RATE_LIMIT_EXEMPT_PATHS" in (
        deps.rate_limit.__code__.co_names
    )
    assert exempt_is_read_inside_rate_limit, (
        "rate_limit no longer consults _RATE_LIMIT_EXEMPT_PATHS; the inertness argument in "
        "F-011-4 rests on it and must be re-derived"
    )

    for path in sorted(ROOT_ROUTES):
        assert not _has_rate_limit(_route(path)), (
            f"{path} reaches rate_limit, so the exempt list is no longer inert for it. That is "
            f"also a behaviour change: this route is a probe or scrape target, and T1103b "
            f"(2026-08-23) decided it stays outside the limiter. If the decision was revisited, "
            f"update the decision, not this test."
        )

    assert not (ROOT_ROUTES & set(deps._RATE_LIMIT_EXEMPT_PATHS)), (
        "root paths were added to _RATE_LIMIT_EXEMPT_PATHS. That has no effect - the list is "
        "consulted inside a dependency these routes never invoke - and it reads as protection "
        "that is not there."
    )


def test_the_limiter_is_still_wired_where_it_should_be() -> None:
    """Anti-vacuum: without this, the test above passes on an application with no limiter."""

    versioned_health = _route("/api/v1/health")
    assert _has_rate_limit(versioned_health), (
        "the versioned health route lost its rate_limit dependency; the assertions above would "
        "then be vacuously true"
    )

    limited = sum(
        1
        for route in app.routes
        if isinstance(route, APIRoute) and _has_rate_limit(route)
    )
    assert limited > 50, (
        f"only {limited} routes are behind the limiter; F-011-4 describes an application where "
        f"nearly the whole versioned surface is"
    )
