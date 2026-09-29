"""026 S2 (`T2602`): the simulator lowers a line below its debt and serves `available` signed (fork 7).

The Interact `trustline-update` action has the same rule as the public PATCH: a limit below `used` is a trust
change and is accepted. After it, `available = limit - used` is negative in every simulator projection that
carries it - the SSE edge patch of the action (`build_edge_patch_for_pairs`), the per-equivalent edge patch
(`build_edge_patch_for_equivalent`) and the run snapshot behind `actions/trustlines-list`. The three
clamps-to-zero that hid it are gone; each assertion below is the one that reddens when its clamp returns.

The debt of 7 is set up within the limit of 10 (not an excess); the excess comes only from the action lowering
the limit to 5 (Verification plan §4). Mode A, no concurrency. The payment and clearing after the lowering are
exercised on the public path (`tests/integration/test_p026_s2_limit_below_used_postgres.py`).
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.real_scenario_seeder import simulated_public_key
from tests.p019_support import require_target
from tests.unit.test_p021_interact_trust_line_actions_wire import (  # noqa: F401 - `stand` is a fixture
    TRIPLE,
    HEADERS,
    _debt,
    _post,
    _Recorder,
    stand,
)


@pytest.mark.asyncio
async def test_update_below_used_is_accepted_and_every_projection_is_signed(client, stand) -> None:
    alice, bob, uah, db, run = stand["alice"], stand["bob"], stand["uah"], stand["db"], stand["run"]
    for p in (alice, bob):  # simulator-created rows: the run snapshot reads DB state only for those
        p.public_key = simulated_public_key(p.pid)
    await db.commit()
    assert (await _post(client, "trustline-create", {**TRIPLE, "limit": "10"})).status_code == 200
    await _debt(db, debtor=bob, creditor=alice, eq=uah, amount="7")
    _Recorder.events.clear()

    r = await _post(client, "trustline-update", {**TRIPLE, "new_limit": "5"})
    require_target(r.status_code == 200, f"Interact update 10 -> 5 below used 7 was refused: {r.status_code} {r.text}")
    assert (r.json()["old_limit"], r.json()["new_limit"]) == ("10.00000000", "5")

    [event] = _Recorder.events
    [patch] = event["payload"]["edge_patch"]
    assert (patch["used"], patch["available"]) == ("7.00", "-2.00"), patch

    [full] = await EdgePatchBuilder(logger=logging.getLogger(__name__)).build_edge_patch_for_equivalent(
        session=db, run=run, equivalent_code="UAH")
    assert (full["trust_limit"], full["used"], full["available"]) == ("5.00", "7.00", "-2.00"), full

    listed = await client.get(f"/api/v1/simulator/runs/{run.run_id}/actions/trustlines-list",
                              headers=HEADERS, params={"equivalent": "UAH"})
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    assert (Decimal(item["limit"]), Decimal(item["used"]), Decimal(item["available"])) == (5, 7, -2), item
