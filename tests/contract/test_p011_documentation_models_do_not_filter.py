"""T1102 invariant: a documentation model must never become a runtime model.

Program 011 describes undescribed 2xx bodies. For a handler that returns a plain dict, the only
way to state its shape without touching behaviour is `responses={<status>: {"model": M}}`, which
FastAPI uses for the schema alone. Writing `response_model=M` instead would make FastAPI filter
the handler output down to the declared fields - a silent wire change, forbidden here.

The two spellings differ by six characters and produce identical-looking OpenAPI, so nothing about
the source makes the danger visible. This file is the thing that makes it visible.

Verified on FastAPI 0.109.0: `response_model` builds the route's `response_field`, used to
serialize the reply, while models from `responses=` are kept separately in `response_fields` and
reach only the schema generator. External review confirmed the same split in the library source.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import app

# Operations whose 2xx shape is declared for documentation only, with the model that declares it.
# Adding a row here is a promise that the handler still returns a plain object and that FastAPI
# is not filtering it.
DOCUMENTATION_ONLY = {
    ("POST", "/api/v1/admin/participants/{pid}/ban"): "AdminParticipantStatusChange",
    ("POST", "/api/v1/admin/participants/{pid}/unban"): "AdminParticipantStatusChange",
}


def _routes():
    return {
        (method, route.path): route
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }


@pytest.mark.parametrize("key", sorted(DOCUMENTATION_ONLY))
def test_documented_route_has_no_runtime_response_model(key) -> None:
    """The declaration must stay in `responses`, never migrate to `response_model`."""

    route = _routes().get(key)
    assert route is not None, f"{key} is no longer registered"

    assert route.response_model is None, (
        f"{key[0]} {key[1]} now declares response_model="
        f"{getattr(route.response_model, '__name__', route.response_model)}. FastAPI will filter "
        "the handler output to that model, dropping any key the model does not declare. The shape "
        "is documented through `responses=` precisely so that cannot happen (011/T1102)."
    )

    documented = (route.responses or {}).get(200, {}).get("model")
    assert documented is not None, (
        f"{key[0]} {key[1]} lost its documentation model; the canon still describes this body"
    )
    assert documented.__name__ == DOCUMENTATION_ONLY[key]


def test_the_two_spellings_really_do_differ() -> None:
    """Guard the guard: prove the distinction this file rests on, on this FastAPI version.

    If a future upgrade made `responses=` filter too, every assertion above would still pass
    while the wire quietly changed. So the difference is demonstrated, not assumed.
    """

    class Declared(BaseModel):
        kept: str

    probe = FastAPI()

    @probe.get("/documented", responses={200: {"model": Declared}})
    async def documented():
        return {"kept": "value", "undeclared": "survives"}

    @probe.get("/filtered", response_model=Declared)
    async def filtered():
        return {"kept": "value", "undeclared": "dropped"}

    client = TestClient(probe)

    documented_body = client.get("/documented").json()
    assert documented_body == {"kept": "value", "undeclared": "survives"}, (
        "`responses=` started filtering the handler output on this FastAPI version; every "
        "documentation-only declaration in 011/T1102 rests on it not doing that"
    )

    assert client.get("/filtered").json() == {"kept": "value"}

    schema = probe.openapi()["paths"]["/documented"]["get"]["responses"]["200"]
    assert schema["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Declared"
    }, "`responses=` stopped reaching the schema; the documentation would be silently lost"
