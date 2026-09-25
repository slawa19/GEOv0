"""Programme 019, `T1902` / `T1904`: an API payment's intermediate state, observed from another transaction.

THE BARRIER. `EngineCommitBarrier` holds one `POST /payments` at the entry of `PaymentEngine.commit`:
after routing, the `Transaction` insert and `prepare` have returned, before any money is written.
Before stage 3 that was after the durable `PREPARED` commit; since stage 3 (`T1904`) it is inside the
payment's one transaction.

THE CONTROLS, asserted normally, because an observation of "nothing there" is worth nothing unless
the stand can be shown to have looked at the right moment (`AGENTS.md` §15):
* the barrier was hit exactly once, and the payment was still in flight when the observer looked;
* the observer's snapshot is provably later than the barrier: it reads a marker committed AFTER the
  barrier was reached, in the same snapshot as the payment row (`observe_after_marker`);
* after release the payment finishes COMMITTED, with money moved and a completed envelope.

THE TARGET (Verification plan §1), PASSING SINCE STAGE 3: nothing of the payment is visible before
its money commit - no transaction row, no reservation - and the payment's sessions committed once.
`TargetMismatch` after the controls.

REMOVED WITH STAGE 3: the characterization `test_today_an_api_payment_is_durably_prepared_between_its_
three_commits`, which pinned the deleted contract - three durable commits NEW -> PREPARED -> COMMITTED,
the PREPARED row and its reservation visible in between, and `GET` answering the live payment ABORTED.
Each of its three observations is now the negation asserted here and in
`test_p019_a_live_payment_is_not_reported_aborted_postgres.py`.
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
from tests.p019_support import require_target


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
async def test_no_intermediate_state_is_visible_before_the_money_commit(
    api, factory, monkeypatch  # noqa: F811
) -> None:
    """TARGET: at the barrier another transaction sees no transaction row and no reservation."""

    world, body, barrier, recorder, task = await _run_to_the_barrier(api, factory, monkeypatch)
    try:
        seen = await observe_after_marker(factory, body["tx_id"])
        assert seen.marker_seen, "the observer's snapshot predates the barrier"
        assert not task.done(), "the payment was not in flight when the observer looked"
        await _release_and_check_completion(api, factory, world, body, barrier, task)
    finally:
        barrier.release.set()
        await finish(task)

    assert recorder.commits, "the recorder saw no commit at all: it is not on the payment's session"
    require_target(
        seen.state is None
        and seen.prepare_locks == 0
        and recorder.commits == [("COMMITTED", 0)],
        f"another transaction saw state={seen.state!r} and {seen.prepare_locks} prepare lock(s) "
        f"of tx {body['tx_id']} before its money commit; the payment's sessions committed "
        f"{recorder.commits}",
    )
