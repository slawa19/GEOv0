"""028 `T2899.4` #3 (F-028-42 on the second entrance): the Interact payment's EARLY refusals carry the machine reason.

The Simulator UI is the repository's only client that sends payments, through `payment-real`; E8 (`T2884`) composes
its text from `code + details.reason + details`. Refusals answered before the payment service - a non-positive
amount, an unknown recipient, an unknown equivalent - must name their reason as the service's own do.
"""

from __future__ import annotations

import pytest

from tests.unit.test_p021_interact_trust_line_actions_wire import TRIPLE, _post, stand  # noqa: F401 - fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status,reason", [
    ({"amount": "0"}, 400, "amount_not_positive"),
    ({"amount": "1", "to_pid": "nobody"}, 404, "recipient_not_found"),
    ({"amount": "1", "equivalent": "NOPE"}, 404, "equivalent_not_found"),
])
async def test_an_early_interact_refusal_names_its_reason(client, stand, body, status, reason) -> None:
    resp = await _post(client, "payment-real", {**TRIPLE, **body})
    assert resp.status_code == status, resp.text
    assert (resp.json().get("details") or {}).get("reason") == reason, resp.text
