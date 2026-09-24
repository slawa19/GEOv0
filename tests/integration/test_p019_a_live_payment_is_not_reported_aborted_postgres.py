"""Programme 019, `T1902`: `GET /payments/{tx_id}` on a payment that is still in flight.

TODAY (pinned by the characterization in `test_p019_no_durable_intermediate_state_postgres.py`): the
live payment, held at `EngineCommitBarrier` after its durable `PREPARED`, is answered `200` with
`status: ABORTED` - `_tx_to_payment_result` maps every non-terminal state to `ABORTED`
(`app/core/payments/service.py:1397`) - and a moment later the same payment is `COMMITTED`.

TARGET (spec, Verification plan §1): before the money commit the payment does not exist for a reader
(`404`), after it it is `COMMITTED`. Controls first - barrier hit once, the reader's snapshot provably
later than the barrier, the payment in flight while it was read, then finished with money moved - and
only then the comparison, as `TargetMismatch`. `xfail(strict)` until stage 3.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    EngineCommitBarrier,
    api,
    build_api_world,
    debts,
    factory,
    finish,
    observe_after_marker,
    payment_body,
)
from tests.p019_support import require_target, target_xfail


@target_xfail("stage 3 (T1904)", "GET answers a live payment as ABORTED (service.py:1397)")
@pytest.mark.asyncio
async def test_a_live_payment_is_not_found_before_its_commit_and_committed_after(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    world = await build_api_world(api, factory)
    body = payment_body(world, world.alice, world.bob, "10.00")
    barrier = EngineCommitBarrier(body["tx_id"])
    barrier.install(monkeypatch)
    task = asyncio.create_task(
        api.post("/api/v1/payments", json=body, headers=world.alice["headers"])
    )
    try:
        await asyncio.wait_for(barrier.reached.wait(), timeout=20)
        # Freshness: a marker committed after the barrier is visible to a new snapshot, so the GET
        # below (a new request, a new session) reads no earlier than that.
        assert (await observe_after_marker(factory, body["tx_id"])).marker_seen
        live = await api.get(f"/api/v1/payments/{body['tx_id']}", headers=world.alice["headers"])
        assert not task.done(), "the payment finished before it was read"
        assert live.status_code in (200, 404), live.text

        barrier.release.set()
        resp = await asyncio.wait_for(task, timeout=30)
    finally:
        barrier.release.set()
        await finish(task)

    assert barrier.hits == 1
    assert resp.status_code == 200 and resp.json()["status"] == "COMMITTED", resp.text
    assert await debts(factory, world) == {
        (world.alice["pid"], world.bob["pid"]): Decimal("10.00")
    }
    after = await api.get(f"/api/v1/payments/{body['tx_id']}", headers=world.alice["headers"])
    assert after.status_code == 200 and after.json()["status"] == "COMMITTED", after.text

    require_target(
        live.status_code == 404,
        f"a live payment was answered {live.status_code} "
        f"{live.json().get('status') if live.status_code == 200 else live.text}",
    )
