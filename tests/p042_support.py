"""042 S1 (`T4202`): a real-mode run in a test, without its background tick, and a real payment on it.

NOT a test module. Since 042 the simulator has one run mode, `real`; the server mode `fixtures` (random flashes
with no money behind them) and the debug actions `tx-once` / `clearing-once` are gone. A test that used them as a
cheap trigger of a domain event now gets one from the core: a real payment through the Interact route
`POST /simulator/runs/{run_id}/actions/payment-real`, on the tier's PostgreSQL.

`no_real_tick` replaces the run's real tick with a recorder. The heartbeat still beats (it moves `tick_index`,
publishes `run_status`, stores the run), but no tick plans payments, seeds the scenario or touches the database
in the background - a real tick of a test's run would otherwise commit through `AsyncSessionLocal` wherever the
test's `client` points it, the tier included (`test_simulator_sse_real_smoke.py` measured the leak, 2026-09-23).
Every event of such a run is one the test caused.

`pay_once` makes one real payment on the run: in the scenario `minimal` (`fixtures/simulator/minimal`) the only line
is `p1 -> p2` (p1 is the creditor), so `p2` pays `p1`. The route seeds the scenario into the database on first use
(`_ensure_run_seeded`), commits the payment through `PaymentService` and publishes its `tx.updated` before it
answers - so the event is in the run's journal when this returns. Use it under `MODE_B`: the seeding and the
payment commit, and a mode-B clone is dropped after the test.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.simulator.runtime import runtime

#: The scenario these helpers run: two participants, one trust line `p1 -> p2` in UAH, limit 1000.
SCENARIO = "minimal"
EQUIVALENT = "UAH"


@pytest.fixture
def no_real_tick(monkeypatch) -> list[str]:
    """The real tick of every run becomes a recorder of the run ids it was called for (see the module docstring)."""

    ticked: list[str] = []

    async def _recorded(run_id: str) -> None:
        ticked.append(run_id)

    monkeypatch.setattr(runtime._real_runner, "tick_real_mode", _recorded)
    return ticked


async def start_real_run(client: AsyncClient, auth_headers, *, scenario_id: str = SCENARIO) -> str:
    response = await client.post(
        "/api/v1/simulator/runs",
        headers=auth_headers,
        json={"scenario_id": scenario_id, "mode": "real", "intensity_percent": 0},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["run_id"])


async def pay_once(client: AsyncClient, auth_headers, run_id: str, monkeypatch, *, amount: str = "1") -> dict[str, Any]:
    """One committed payment p2 -> p1 on the run; returns the route's answer."""

    monkeypatch.setattr("app.config.settings.SIMULATOR_ACTIONS_ENABLE", True)
    response = await client.post(
        f"/api/v1/simulator/runs/{run_id}/actions/payment-real",
        headers=auth_headers,
        json={"from_pid": "p2", "to_pid": "p1", "equivalent": EQUIVALENT, "amount": amount},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert str(body.get("status") or "").upper() == "COMMITTED", body
    return body
