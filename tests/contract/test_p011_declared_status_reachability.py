"""RT-011-6: a client generated from the app's schema must know the 503 the route returns.

Program 011, finding `F-011-6` (inherited as `T716(а)` from program 007).

The canonical contract is authority number one, and it declares `503` for the simulator
metrics and bottlenecks routes, and for the versioned DB health route.  The application's
own schema does not: those handlers carry no `responses=`, so FastAPI never learns of the
status.  A client generated from the application - which is what an SDK consumer actually
gets - has no branch for a status the service really returns.

The divergence is deliberate and ratcheted (`ERROR_RESPONSE_DRIFT_SHA256`), so it does not
fail the suite today.  That is precisely why it needs a test of its own: a locked drift is a
recorded debt, not a closed one, and nothing currently states which operations are still in
it for this reason.

Counted rather than asserted from memory: the third route was missed by the first version of
the finding, which named two.  `GET /health/db` declares 503 in the canon
(`api/openapi.yaml:108`) and returns it from the handler (`app/api/v1/health.py:83`) while
declaring nothing.
"""

from __future__ import annotations

import pytest

from tests.contract.test_openapi_contract import (
    _load_fastapi_openapi,
    _load_openapi_yaml,
    _normalized_responses,
    _operation_pairs,
)

# Operations whose canon declares 503 while the application's schema does not.
_EXPECTED = {
    "GET /health/db",
    "GET /simulator/runs/{run_id}/bottlenecks",
    "GET /simulator/runs/{run_id}/metrics",
}


def _canonical_only_503() -> set[str]:
    canonical = _load_openapi_yaml()
    generated = _load_fastapi_openapi()

    found: set[str] = set()
    for key, canonical_op, generated_op, _c_item, _g_item in _operation_pairs(
        canonical, generated
    ):
        canonical_responses = _normalized_responses(canonical_op, canonical)
        generated_responses = _normalized_responses(generated_op, generated)
        if "503" in canonical_responses and "503" not in generated_responses:
            found.add(key)
    return found


def test_no_route_declares_503_only_in_the_canon() -> None:
    """The whole of F-011-6, stated as the condition that closes it."""

    remaining = _canonical_only_503()
    assert remaining == set(), (
        "these operations return 503 and say so only in the canonical contract, so a client "
        "generated from the application has no branch for it: " + ", ".join(sorted(remaining))
    )


@pytest.mark.parametrize("key", sorted(_EXPECTED))
def test_each_route_of_the_class_declares_it_in_the_application_schema(key: str) -> None:
    """One case per route, so removing any single declaration fails on its own name.

    The set-level test above passes as long as the class is empty; it cannot say WHICH of
    the three stopped declaring. These three were closed by three separate edits in two
    files, and the /health/db pair had to move together with its root twin - exactly the
    shape where a collective assertion hides which half regressed.
    """

    generated = _load_fastapi_openapi()
    method, path = key.split(" ", 1)
    operation = generated["paths"][f"/api/v1{path}"][method.lower()]

    assert "503" in (operation.get("responses") or {}), (
        f"{key} no longer declares 503 in the schema the application generates, so a "
        "client built from it has no branch for a status the route returns"
    )
