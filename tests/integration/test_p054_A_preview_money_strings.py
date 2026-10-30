"""054-A-15 / 054-E-15 (code half): the graph's money leaves as decimal strings before the database is read.

Before: `scenario_to_snapshot` built every link with the scenario's `limit` as it was in the JSON (a number in every
shipped fixture) and `used=0`, and `SimulatorGraphLink` admitted a float - so the scenario preview, and a run's
snapshot before its first tick seeds the run's participants, put money on the wire as JSON numbers (AGENTS.md §8).

The canon (`api/openapi.yaml`, `oneOf [string, number]`) is NOT narrowed here: wave E narrows it with its consumers.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.simulator import SimulatorGraphLink
from tests.conftest import MODE_B
from tests.p042_support import EQUIVALENT, SCENARIO, no_real_tick, pay_once, start_real_run  # noqa: F401

_MONEY = ("trust_limit", "used", "available")


def _money(body: dict) -> list[dict]:
    links = body.get("links") or []
    assert links, f"premise: the {SCENARIO} graph has a {EQUIVALENT} line: {body}"
    return [{k: link.get(k) for k in _MONEY} for link in links]


@pytest.mark.asyncio
async def test_w6_preview_money_is_a_decimal_string(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.config.settings.SIMULATOR_SCENARIO_ALLOWLIST", SCENARIO)
    response = await client.get(
        f"/api/v1/simulator/scenarios/{SCENARIO}/graph/preview",
        headers=auth_headers,
        params={"equivalent": EQUIVALENT},
    )
    assert response.status_code == 200, response.text
    # `minimal`: one line p1 -> p2, limit 1000 (a JSON number in the fixture).
    assert _money(response.json()) == [{"trust_limit": "1000.00", "used": "0.00", "available": "1000.00"}]


@MODE_B
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_real_tick")
async def test_a_run_snapshot_before_and_after_seeding_spells_money_the_same_way(client, auth_headers, monkeypatch):
    run_id = await start_real_run(client, auth_headers)

    async def _snapshot() -> list[dict]:
        response = await client.get(
            f"/api/v1/simulator/runs/{run_id}/graph/snapshot",
            headers=auth_headers,
            params={"equivalent": EQUIVALENT},
        )
        assert response.status_code == 200, response.text
        return _money(response.json())

    # Before the run's participants exist (no tick has run): the scenario's own picture.
    before = await _snapshot()
    assert before == [{"trust_limit": "1000.00", "used": "0.00", "available": "1000.00"}], before

    # After a real payment seeded the run and moved 1 UAH: the database's picture, the same spelling.
    await pay_once(client, auth_headers, run_id, monkeypatch, amount="1")
    after = await _snapshot()
    assert after == [{"trust_limit": "1000.00", "used": "1.00", "available": "999.00"}], after


def test_the_link_schema_refuses_a_json_number_as_money():
    for field in _MONEY:
        with pytest.raises(ValidationError, match="money must be a decimal string"):
            SimulatorGraphLink(source="a", target="b", **{field: 1000.0})
        with pytest.raises(ValidationError, match="money must be a decimal string"):
            SimulatorGraphLink(source="a", target="b", **{field: 0})
    # Counter-check: a decimal string and an absent amount are what the producers send, and stay accepted.
    link = SimulatorGraphLink(source="a", target="b", trust_limit="1000.00", used="0.00", available=None)
    assert (link.trust_limit, link.used, link.available) == ("1000.00", "0.00", None)
