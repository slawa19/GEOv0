"""RT-011-2: every callable operation is described by the canon, or is a named exception.

Program 011, finding `F-011-2`.

The contract gate compares the canon against the schema FastAPI generates.  That comparison is
blind by construction to any route the schema does not contain, and until 2026-08-23 eight
money-moving Interact Mode operations were exactly that: registered, reachable, and invisible.
`api/openapi.yaml` is authority number one (`AGENTS.md` section 8), so the honest denominator is
what the application will actually answer, not what it chooses to document.

This test takes that denominator from `app.routes` - the registry the router dispatches on - and
holds it against the canon.  It is the check that would have caught the eight before a defect
(`F-009-4`, a P1 on `trustline-create`) landed on a route no contract test could see.

**The environment is pinned, not assumed.** Two of the four counts in the spec depend on it:
`include_in_schema` used to read `SIMULATOR_ACTIONS_ENABLE`, and `/metrics` is conditional on
`METRICS_ENABLED`.  A test that silently re-derived its expectation from whatever environment it
found would go green on a surface nobody intended to publish, so the flags are asserted first and
the test fails loudly when they move.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml
from fastapi.routing import APIRoute

from app.config import settings
from app.main import app

# Root-level operations declared with @app.get, outside api_router and outside the canon.
# They are a known, dated exception rather than an oversight: `F-011-4` establishes that they
# also bypass the shared policy gate, and `T1103b` recorded on 2026-08-23 that this stays as it
# is. Publishing them is a separate decision; leaving them undeclared is the recorded one.
ROOT_OPERATIONS_OUTSIDE_CANON = {
    ("GET", "/health"),
    ("GET", "/health/db"),
    ("GET", "/healthz"),
    ("GET", "/metrics"),
}

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _strip_converters(path: str) -> str:
    """`/participants/{pid:path}` in the route table is `/participants/{pid}` in the schema.

    Starlette keeps the converter in the registered path; OpenAPI does not. Comparing the two
    without normalizing reports a declared route as missing - a false finding, checked against
    `api/openapi.yaml:2070` and against what `app.openapi()` emits for the same route.
    """

    return re.sub(r"\{([^{}:]+):[^{}]+\}", lambda m: "{" + m.group(1) + "}", path)


def _callable_operations() -> set[tuple[str, str]]:
    return {
        (method, _strip_converters(route.path))
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method in _HTTP_METHODS
    }


def _canonical_operations() -> set[tuple[str, str]]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "api", "openapi.yaml")
    with open(os.path.abspath(path), "r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return {
        (method.upper(), "/api/v1" + declared)
        for declared, item in document["paths"].items()
        for method in item
        if method.upper() in _HTTP_METHODS
    }


def test_environment_of_the_measurement_is_the_one_this_test_was_written_for() -> None:
    """Pin the flags the counts depend on, so the surface cannot change unnoticed."""

    assert settings.METRICS_ENABLED is True, (
        "METRICS_ENABLED changes whether /metrics is registered; "
        "the expected surface below was measured with it enabled"
    )
    # Interact Mode visibility no longer depends on this flag (011/T1101) - asserted here so the
    # day someone reintroduces the coupling, this test says so instead of quietly shrinking.
    for value in (None, "1"):
        if value is None:
            os.environ.pop("SIMULATOR_ACTIONS_ENABLE", None)
        else:
            os.environ["SIMULATOR_ACTIONS_ENABLE"] = value
        assert {
            method_path
            for method_path in _callable_operations()
            if "/actions/" in method_path[1]
        }, "Interact Mode routes vanished from the route table"
    os.environ.pop("SIMULATOR_ACTIONS_ENABLE", None)


def test_every_callable_operation_is_in_the_canon_or_a_named_exception() -> None:
    """No undeclared operation, and the exceptions are listed by name rather than counted."""

    undeclared = _callable_operations() - _canonical_operations()
    unexpected = undeclared - ROOT_OPERATIONS_OUTSIDE_CANON

    assert not unexpected, (
        "Operations are callable but absent from api/openapi.yaml, and are not a recorded "
        f"exception: {sorted(unexpected)}"
    )

    # Guard the guard: if the root routes ever do get declared, this list is stale and must be
    # shrunk deliberately - an exception list nobody prunes stops being an exception list.
    stale = ROOT_OPERATIONS_OUTSIDE_CANON - undeclared
    assert not stale, (
        f"These are now declared in the canon; drop them from the exception list: {sorted(stale)}"
    )


def test_the_eight_interact_mode_operations_are_declared() -> None:
    """The specific regression this reproducer exists for (011/T1101)."""

    canon = _canonical_operations()
    published = {
        ("POST", "/api/v1/simulator/runs/{run_id}/actions/trustline-create"),
        ("POST", "/api/v1/simulator/runs/{run_id}/actions/trustline-update"),
        ("POST", "/api/v1/simulator/runs/{run_id}/actions/trustline-close"),
        ("POST", "/api/v1/simulator/runs/{run_id}/actions/payment-real"),
        ("POST", "/api/v1/simulator/runs/{run_id}/actions/clearing-real"),
        ("GET", "/api/v1/simulator/runs/{run_id}/actions/participants-list"),
        ("GET", "/api/v1/simulator/runs/{run_id}/actions/trustlines-list"),
        ("GET", "/api/v1/simulator/runs/{run_id}/payment-targets"),
    }
    assert published <= canon, f"Not declared in the canon: {sorted(published - canon)}"
    assert published <= _callable_operations()
