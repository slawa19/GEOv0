"""RT-011-3: the status the limiter really returns must appear in the declaration.

Program 011, finding `F-011-3`.

Every HTTP router is mounted with `Depends(deps.rate_limit)` (`app/api/router.py:10-21`), so
almost the whole surface can answer `429`.  Before `T1103a` the canon mentioned `429` exactly
once in the entire document, which means a client generated from it had no branch for a status
most operations can return.

**Reading `router.py` is not allowed to be the proof.** The spec forbids it by name, and program
008 already got this class wrong by inspection: a dependency that is wired is not necessarily a
dependency that fires.  So the first test drives a real operation until the limiter actually
answers, and asserts on the response rather than on the wiring.

The second test then holds that reachability against the declaration, deriving the affected set
from the route table - `deps.rate_limit` present in the dependency tree, path not exempt - so the
expectation cannot drift out of step with how the routers are mounted.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml
from fastapi.routing import APIRoute

from app.api import deps
from app.config import settings
from app.main import app

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _reset_rate_limit_counters() -> None:
    """The counters are module-global; leaving them dirty would leak into other tests."""

    deps._rate_limit_counters.clear()


def _schema_path(path: str) -> str:
    """Drop the Starlette converter, so `/participants/{pid:path}` matches the canon's key.

    011/T1108: without this the one operation declared with a `:path` converter never matched a
    canonical key, and the lookup below skipped it silently - a guard that claimed 96 operations
    and could not see one of them. The newer general status guard already normalised this; this
    file did not, because it was written first.
    """

    return re.sub(r":[^{}]+\}", "}", path)


def _rate_limited_operations() -> set[tuple[str, str]]:
    return {
        (method, _schema_path(route.path))
        for route in app.routes
        if isinstance(route, APIRoute)
        if any(
            dependency.call is deps.rate_limit
            for dependency in route.dependant.dependencies
        )
        if route.path not in deps._RATE_LIMIT_EXEMPT_PATHS
        for method in route.methods
        if method in _HTTP_METHODS
    }


def _canonical_operations() -> dict[tuple[str, str], dict]:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "api", "openapi.yaml")
    with open(os.path.abspath(path), "r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return {
        (method.upper(), "/api/v1" + declared): operation
        for declared, item in document["paths"].items()
        for method, operation in item.items()
        if method.upper() in _HTTP_METHODS
    }


@pytest.mark.asyncio
async def test_a_real_operation_really_answers_429(client, monkeypatch) -> None:
    """Anti-vacuum: the status is observed on a real response, not inferred from wiring."""

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_WINDOW", 3)
    _reset_rate_limit_counters()
    try:
        statuses = []
        for _ in range(6):
            response = await client.get("/api/v1/health")
            statuses.append(response.status_code)
            if response.status_code == 429:
                break

        assert 429 in statuses, (
            f"the limiter never answered 429 over {len(statuses)} calls with a limit of 3; "
            f"statuses={statuses}"
        )
        assert response.json()["error"]["code"] == "E009"
    finally:
        _reset_rate_limit_counters()


def test_the_exempt_path_is_really_exempt() -> None:
    """Counter-check: the one exemption is a real exemption, not a stale entry."""

    exempt = set(deps._RATE_LIMIT_EXEMPT_PATHS)
    assert exempt == {"/api/v1/simulator/session/ensure"}, (
        f"the exempt list moved; RT-011-3 expectations were measured against {exempt}"
    )
    assert not (exempt & {path for _method, path in _rate_limited_operations()})


def test_every_rate_limited_operation_declares_429() -> None:
    """Each operation the limiter can answer for says so in the canon."""

    canonical = _canonical_operations()
    reachable = _rate_limited_operations()

    # Anti-vacuum: an operation the limiter answers for and the canon does not document at all is
    # a hole, not a pass. The previous version reached the canon through `if key in canonical`,
    # which turned every such operation into silence - and one really was being skipped.
    missing = sorted(key for key in reachable if key not in canonical)
    assert not missing, (
        f"{len(missing)} rate-limited operations are absent from api/openapi.yaml entirely, so "
        f"this guard would have skipped them rather than checking them: {missing}"
    )

    undeclared = sorted(
        key for key in reachable if "429" not in (canonical[key].get("responses") or {})
    )
    assert not undeclared, (
        f"{len(undeclared)} operations can answer 429 but do not declare it: {undeclared[:10]}"
        + (" ..." if len(undeclared) > 10 else "")
    )


def test_the_path_converter_does_not_hide_an_operation() -> None:
    """Guard the guard: the normalisation above is the whole reason one operation is visible.

    `GET /participants/{pid}` is declared in the application as `{pid:path}`. Before T1108 the
    lookup compared the raw Starlette path against the canon key, never matched, and dropped the
    operation. The two assertions below are the before and after of that bug.
    """

    assert _schema_path("/api/v1/participants/{pid:path}") == "/api/v1/participants/{pid}"
    assert _schema_path("/api/v1/admin/participants/{pid}/ban") == (
        "/api/v1/admin/participants/{pid}/ban"
    ), "a path with no converter must pass through untouched"

    reachable = _rate_limited_operations()
    assert ("GET", "/api/v1/participants/{pid}") in reachable, (
        "the converter-declared operation must be in the measured set, not silently absent"
    )
    assert ("GET", "/api/v1/participants/{pid}") in _canonical_operations()
