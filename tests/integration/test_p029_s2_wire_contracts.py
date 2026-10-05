"""Programme 029, stage S2: one amount - one spelling on the wire (F-029-5), and 404 is not "no route" (F-029-6).

DB integration tests over the public REST surface; every assertion reads the raw JSON the client receives.
The matrix of F-029-5 (`specs/029-backlog-closure/spec.md`) says which producers write a STATE amount in the
equivalent's step and which keep a RECORDED amount exactly as recorded. The first test is the reproducer of the
"changes" rows, the second is the counter-check of the "stays" rows, the third measures the Admin API (row 9).

What this module does not see: the Interact routes and the `clearing.done` event (`tests/unit/
test_interact_actions_backend_p1.py`), the staged capacity refusal (`tests/integration/
test_payment_prepare_capacity_policy.py`), `/clearing/auto` (`tests/integration/test_p023_d_auto_endpoint_postgres.py`).
"""

from __future__ import annotations

import base64
import re
import uuid

import pytest
from httpx import AsyncClient
from nacl.signing import SigningKey

from app.config import settings
from app.utils.exceptions import RoutingException
from tests.conftest import MODE_B
from tests.integration.test_p011_money_is_a_decimal_string_on_the_wire import money_scenario  # noqa: F401 (fixture)
from tests.integration.test_scenarios import (
    _sign_payment_request,
    _sign_trustline_close_request,
)

_EXPONENT = re.compile(r"^-?\d+(\.\d+)?[eE][+-]?\d+$")


def _exponent_strings(node, path="$"):
    """Every string in a JSON body that is a number in exponent notation, with its path."""

    if isinstance(node, dict):
        return [hit for key, value in node.items() for hit in _exponent_strings(value, f"{path}.{key}")]
    if isinstance(node, list):
        return [hit for index, value in enumerate(node) for hit in _exponent_strings(value, f"{path}[{index}]")]
    return [(path, node)] if isinstance(node, str) and _EXPONENT.match(node) else []


async def _request_close(client: AsyncClient, scenario: dict) -> dict:
    """Bob asks to close his line while Alice still owes on it: the line stays live with limit zero."""

    bob = scenario["bob"]
    response = await client.request(
        "DELETE",
        f"/api/v1/trustlines/{scenario['trustline_id']}",
        headers=bob["headers"],
        json={"signature": _sign_trustline_close_request(
            signing_key=SigningKey(base64.b64decode(bob["priv"])), trustline_id=scenario["trustline_id"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["trustline"]["close_requested_at"], "setup: the close must be a request, not a closure"
    return response.json()["trustline"]


@MODE_B
@pytest.mark.asyncio
async def test_state_amounts_are_written_in_the_equivalents_step(client: AsyncClient, money_scenario) -> None:  # noqa: F811
    alice, bob = money_scenario["alice"], money_scenario["bob"]
    # USD has precision 2; the line was opened with "100.50" and carries a debt of "10.25".
    assert money_scenario["trustline_created"]["limit"] == "100.50"

    listed = (await client.get("/api/v1/trustlines", headers=bob["headers"], params={"direction": "outgoing"})).json()
    line = listed["items"][0]
    assert (line["limit"], line["used"], line["available"]) == ("100.50", "10.25", "90.25")
    one = (await client.get(f"/api/v1/trustlines/{money_scenario['trustline_id']}", headers=bob["headers"])).json()
    assert (one["limit"], one["used"], one["available"]) == ("100.50", "10.25", "90.25")

    # The same quantity read through the payment routes is the same string.
    flow = await client.get("/api/v1/payments/max-flow", headers=alice["headers"],
                            params={"to": bob["pid"], "equivalent": "USD"})
    assert flow.status_code == 200, flow.text
    assert flow.json()["max_amount"] == line["available"]
    capacity = await client.get("/api/v1/payments/capacity", headers=alice["headers"],
                                params={"to": bob["pid"], "equivalent": "USD", "amount": "1.5"})
    assert capacity.status_code == 200, capacity.text
    assert capacity.json() == {**capacity.json(), "can_pay": True, "max_amount": "1.50"}

    # A requested close sets the limit to zero: plain decimal, never `0E-8`.
    closing = await _request_close(client, money_scenario)
    assert (closing["limit"], closing["used"], closing["available"]) == ("0.00", "10.25", "-10.25")


@MODE_B
@pytest.mark.asyncio
async def test_a_recorded_payment_keeps_its_spelling_and_replays_byte_for_byte(client: AsyncClient, money_scenario) -> None:  # noqa: F811
    """Rows 4-5 of the matrix: the signed input and the stored payment are never re-spelled."""

    alice, bob = money_scenario["alice"], money_scenario["bob"]
    tx_id = str(uuid.uuid4())
    body = {
        "tx_id": tx_id, "to": bob["pid"], "equivalent": "USD", "amount": "1.5",
        "signature": _sign_payment_request(
            signing_key=SigningKey(base64.b64decode(alice["priv"])), tx_id=tx_id, from_pid=alice["pid"],
            to_pid=bob["pid"], equivalent="USD", amount="1.5"),
    }
    first = await client.post("/api/v1/payments", headers=alice["headers"], json=body)
    assert first.status_code == 200 and first.json()["status"] == "COMMITTED", first.text
    replay = await client.post("/api/v1/payments", headers=alice["headers"], json=body)
    read = await client.get(f"/api/v1/payments/{tx_id}", headers=alice["headers"])
    assert replay.status_code == 200 and read.status_code == 200, (replay.text, read.text)

    assert first.json()["amount"] == "1.5", "the answer re-spelled the amount the client signed"
    assert first.json()["routes"], "anti-vacuum: a payment without routes proves nothing about `routes[].amount`"
    for name, again in (("replay", replay.json()), ("GET /payments/{tx_id}", read.json())):
        assert again["amount"] == "1.5", name
        assert again["routes"] == first.json()["routes"], f"{name}: the stored routes changed their spelling"


@MODE_B
@pytest.mark.asyncio
async def test_not_found_is_e009_and_only_routing_answers_e001(client: AsyncClient, money_scenario) -> None:  # noqa: F811
    alice = money_scenario["alice"]
    missing = await client.get(f"/api/v1/trustlines/{uuid.uuid4()}", headers=alice["headers"])
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "E009", missing.text
    assert missing.json()["error"]["message"] == "Trustline not found"

    # `E001` stays the routing code (its real-path control: `tests/unit/test_p1_payment_run_perimeter.py`).
    assert (RoutingException().code, RoutingException(insufficient_capacity=True).code) == ("E001", "E002")


@MODE_B
@pytest.mark.asyncio
async def test_the_admin_api_never_writes_an_exponent(client: AsyncClient, money_scenario) -> None:  # noqa: F811
    """Row 9: the Admin API keeps its scale; the measurement is that no number arrives as `0E-8`."""

    await _request_close(client, money_scenario)  # a live line with limit zero, and zero-valued sums
    admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
    bob_pid = money_scenario["bob"]["pid"]
    seen, hits = 0, []
    for path, params in (
        ("/api/v1/admin/trustlines", {}),
        ("/api/v1/admin/graph/snapshot", {}),
        ("/api/v1/admin/liquidity/summary", {"equivalent": "USD"}),
        (f"/api/v1/admin/participants/{bob_pid}/metrics", {"equivalent": "USD"}),
    ):
        response = await client.get(path, headers=admin, params=params)
        assert response.status_code == 200, (path, response.text)
        hits += [(path, *hit) for hit in _exponent_strings(response.json())]
        seen += '"0.00000000"' in response.text or '"0"' in response.text
    assert hits == []
    assert _exponent_strings({"a": [{"limit": "0E-8"}]}) == [("$.a[0].limit", "0E-8")]  # the scanner can fail
    assert seen, "anti-vacuum: no Admin answer carried a zero amount, so the measurement saw nothing"
