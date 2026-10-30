"""054-E-15 (canon half, after 054-A-15): graph money is a decimal string in the canon, not `oneOf [string, number]`.

Witness W6 (`.local-run/codex-review/054-E-20261030/REPORT.md`): the scenario preview sent `trust_limit`/`used`/
`available` as JSON numbers and the canon accepted them with 0 schema errors, because `SimulatorGraphLink` and
`SimulatorGraphLinkPatch` declared `oneOf [string, number]` - so the response-conformance harness could not see
money leaving as a float (AGENTS.md §8). 054-A-15 made the producers send strings; this narrows the canon so a
number is a finding again. The real preview body is validated against the operation's own 200 schema here, and
the same body with one amount turned into a JSON number must be refused - the narrowing is what bites, not a
schema that admits everything.
"""

from __future__ import annotations

import copy

import pytest

from tests.contract import openapi_response_conformance as oc
from tests.p042_support import EQUIVALENT, SCENARIO

_CANON = oc.load_canon()
_MONEY = ("trust_limit", "used", "available")
_LINK_PATCH = "/components/schemas/SimulatorGraphLinkPatch"


def _preview_pointer() -> str:
    pointer = oc.response_schema_pointer(_CANON, "get", "/simulator/scenarios/{scenario_id}/graph/preview", 200)
    assert pointer, "premise: the canon declares a JSON 200 for the scenario preview"
    return pointer


@pytest.mark.asyncio
async def test_the_preview_money_is_a_string_and_a_number_is_refused_by_the_canon(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.SIMULATOR_SCENARIO_ALLOWLIST", SCENARIO)
    response = await client.get(
        f"/api/v1/simulator/scenarios/{SCENARIO}/graph/preview",
        headers=auth_headers,
        params={"equivalent": EQUIVALENT},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    links = body.get("links") or []
    assert links, f"premise: the {SCENARIO} graph has a {EQUIVALENT} line: {body}"
    pointer = _preview_pointer()

    assert oc.validate_body(_CANON, pointer, body) == []
    for field in _MONEY:
        as_number = copy.deepcopy(body)
        as_number["links"][0][field] = float(as_number["links"][0][field])
        errors = oc.validate_body(_CANON, pointer, as_number)
        assert errors, f"the canon still admits a JSON number as `{field}` of a preview link"


@pytest.mark.parametrize("field", _MONEY)
def test_a_link_patch_refuses_a_json_number_as_money(field: str) -> None:
    patch = {"source": "a", "target": "b", "trust_limit": "1000.00", "used": "0.00", "available": "1000.00"}
    assert oc.validate_body(_CANON, _LINK_PATCH, patch) == []
    patch[field] = 1000.0
    assert oc.validate_body(_CANON, _LINK_PATCH, patch), f"the canon still admits a JSON number as `{field}`"
