"""Programme 019, `T1902`: an API payment's intermediate state, observed from another transaction.

THE BARRIER. `EngineCommitBarrier` holds one `POST /payments` at the entry of `PaymentEngine.commit`:
after routing, the `Transaction` insert and `prepare` have returned, before any money is written. On
the current tree that is after the durable `PREPARED` commit (`app/core/payments/engine.py:1032`).

THE CONTROLS, asserted normally in both tests, because an observation of "nothing there" is worth
nothing unless the stand can be shown to have looked at the right moment (`AGENTS.md` §15):
* the barrier was hit exactly once, and the payment was still in flight when the observer looked;
* the observer's snapshot is provably later than the barrier: it reads a marker committed AFTER the
  barrier was reached, in the same snapshot as the payment row (`observe_after_marker`);
* after release the payment finishes COMMITTED, with money moved and a completed envelope.

TWO TESTS.
* `test_today_...` is CHARACTERIZATION, green on the current tree, and it pins what stage 3 changes
  deliberately: three durable commits NEW -> PREPARED -> COMMITTED, the PREPARED row and its prepare
  lock visible to any other transaction in between, and `GET /payments/{tx_id}` answering that live
  payment as `ABORTED` (`app/core/payments/service.py:1397`). Stage 3 rewrites it.
* `test_no_intermediate_...` is the TARGET (Verification plan §1): nothing of the payment is visible
  before its money commit. `TargetMismatch` after the controls; `xfail(strict)` until stage 3.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    CommitRecorder,
    EngineCommitBarrier,
    api,
    build_api_world,
    debts,
    envelopes,
    factory,
    finish,
    observe_after_marker,
    payment_body,
    with_session_hook,
)
from tests.p019_support import require_target, target_xfail


async def _run_to_the_barrier(api, factory, monkeypatch):  # noqa: F811
    world = await build_api_world(api, factory)
    body = payment_body(world, world.alice, world.bob, "10.00")
    barrier = EngineCommitBarrier(body["tx_id"])
    barrier.install(monkeypatch)
    recorder = CommitRecorder(factory, body["tx_id"])
    with with_session_hook(recorder):
        task = asyncio.create_task(
            api.post("/api/v1/payments", json=body, headers=world.alice["headers"])
        )
    await asyncio.wait_for(barrier.reached.wait(), timeout=20)
    return world, body, barrier, recorder, task


async def _release_and_check_completion(api, factory, world, body, barrier, task):  # noqa: F811
    barrier.release.set()
    resp = await asyncio.wait_for(task, timeout=30)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMMITTED", resp.text
    assert barrier.hits == 1
    assert await debts(factory, world) == {
        (world.alice["pid"], world.bob["pid"]): Decimal("10.00")
    }
    [(env_state, declared, entries)] = await envelopes(factory, body["tx_id"])
    assert env_state == "COMPLETED" and declared == entries > 0, (env_state, declared, entries)
    after = await api.get(f"/api/v1/payments/{body['tx_id']}", headers=world.alice["headers"])
    assert after.status_code == 200 and after.json()["status"] == "COMMITTED", after.text


@pytest.mark.asyncio
async def test_today_an_api_payment_is_durably_prepared_between_its_three_commits(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """CHARACTERIZATION of the current tree (stage 3 changes all three observations on purpose)."""

    world, body, barrier, recorder, task = await _run_to_the_barrier(api, factory, monkeypatch)
    try:
        seen = await observe_after_marker(factory, body["tx_id"])
        assert seen.marker_seen, "the observer's snapshot predates the barrier"
        assert not task.done(), "the payment was not in flight when the observer looked"

        # (1) PREPARED is durable and visible, with its reservation.
        assert (seen.state, seen.prepare_locks) == ("PREPARED", 1), seen
        # (2) The live payment is reported to its own sender as ABORTED (service.py:1397).
        live = await api.get(f"/api/v1/payments/{body['tx_id']}", headers=world.alice["headers"])
        assert live.status_code == 200, live.text
        assert live.json()["status"] == "ABORTED", live.json()
        assert live.json().get("error") is None, live.json()
        assert live.json()["committed_at"] is None, live.json()

        await _release_and_check_completion(api, factory, world, body, barrier, task)
    finally:
        barrier.release.set()
        await finish(task)

    # (3) Three durable commits on the request's session, each visible to another transaction as it
    # happened: NEW, then PREPARED with one reservation, then COMMITTED with the reservation gone.
    assert recorder.commits == [("NEW", 0), ("PREPARED", 1), ("COMMITTED", 0)], recorder.commits


@target_xfail("stage 3 (T1904)", "a payment's intermediate state is visible to other transactions")
@pytest.mark.asyncio
async def test_no_intermediate_state_is_visible_before_the_money_commit(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """TARGET: at the barrier another transaction sees no transaction row and no reservation."""

    world, body, barrier, _recorder, task = await _run_to_the_barrier(api, factory, monkeypatch)
    try:
        seen = await observe_after_marker(factory, body["tx_id"])
        assert seen.marker_seen, "the observer's snapshot predates the barrier"
        assert not task.done(), "the payment was not in flight when the observer looked"
        await _release_and_check_completion(api, factory, world, body, barrier, task)
    finally:
        barrier.release.set()
        await finish(task)

    require_target(
        seen.state is None and seen.prepare_locks == 0,
        f"another transaction saw state={seen.state!r} and {seen.prepare_locks} prepare lock(s) "
        f"of tx {body['tx_id']} before its money commit",
    )
