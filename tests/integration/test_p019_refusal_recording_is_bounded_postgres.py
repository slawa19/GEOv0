"""Programme 019, stage-3 review (Codex, frozen `fae2208`, P2 #5): recording a refusal escaped the
payment's deadline.

THE SCHEDULE IS REAL; nothing is injected. A caller transaction (a simulator phase, here staged by hand
through the same entry, `create_payment_internal_staged`) has written payment X and stays open. An API
payment of the same `tx_id` (`PaymentService.pay`) inserts, queues on X's uncommitted unique-index entry
until its total deadline (`PAYMENT_TOTAL_TIMEOUT_SECONDS`, 1 s here) ends it; it was admitted, so its
timeout is recorded `ABORTED/E007` - by an insert on the same key, which queues on the same
uncommitted row. Before the fix that second wait had no bound at all (the recording is shielded from
the caller's cancellation, too): the request lived as long as the other transaction did.

CONTROL: the API insert really queued on the staged row (a non-granted `transactionid` lock while it is
in flight). TARGET: the request ends within its deadline plus a bounded grace while the other
transaction is still open, and it records nothing it could not record (no row after the other
transaction rolls back).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.config import settings
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.schemas.payment import PaymentCreateRequest
from tests.integration.p019_stand import finish, tx_row
from tests.integration.test_p015_p1_money_replay_postgres import _seed, factory  # noqa: F401 - fixture
from tests.integration.test_p019_staged_tx_id_race_is_a_declared_conflict_postgres import (
    _transactionid_waiter_exists,
)
from tests.p019_support import require_target

_HOLD_SECONDS = 8.0


@pytest.mark.asyncio
async def test_recording_a_timeout_refusal_does_not_outlive_the_deadline(factory, monkeypatch) -> None:  # noqa: F811
    world = await _seed(factory)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 1, raising=False)
    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 1, raising=False)
    tx_id = str(uuid.uuid4())

    async def api_payment():
        request = PaymentCreateRequest(
            tx_id=tx_id, to=world.receiver.pid, equivalent=world.equivalent.code, amount="1.00",
            signature="__internal__",
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            outcome: object = await PaymentService.pay(factory, world.sender.id, request, require_signature=False)
        except Exception as exc:  # noqa: BLE001 - classified by the assertions
            outcome = exc
        return outcome, loop.time() - started

    api = None
    async with factory() as holder:
        async with holder.begin_nested():
            PaymentRouter.invalidate_cache(world.equivalent.code)
            staged = await PaymentService(holder).create_payment_internal_staged(
                world.sender.id, to_pid=world.receiver.pid, equivalent=world.equivalent.code,
                amount="1.00", idempotency_key=tx_id,
            )
        assert staged.result.status == "COMMITTED", staged.result
        api = asyncio.create_task(api_payment())
        try:
            queued = await _transactionid_waiter_exists(factory)
            done, _pending = await asyncio.wait([api], timeout=_HOLD_SECONDS)
            ended_while_held = api in done
        finally:
            await holder.rollback()
            await finish(api)
    outcome, elapsed = api.result()
    PaymentRouter.invalidate_cache(world.equivalent.code)

    assert queued, "premise: the API insert never queued on the staged row"
    stored = await tx_row(factory, tx_id)
    require_target(
        ended_while_held and elapsed < 1 + 2.5 and stored is None,
        f"the API payment ended after {elapsed:.2f}s (while the other transaction was open: "
        f"{ended_while_held}) with {outcome!r}; stored afterwards: {stored!r}",
    )
