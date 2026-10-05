"""030 S4, §15 `T3094` finding 1: the unusable-phase owner publishes the STORED refusal it yielded to, not its own.

The `T1912` schedule of `test_p019_staged_refusal_is_durable_postgres.py`, unchanged: seq 1 of the tick times out on its
commit guard behind an operator's slow edit, the owner rolls the phase back and records the timeout
(`money_replay._settle_unusable_phase`). Between that rollback and the recording, the SAME request runs again on the
staged path (the barrier sits in front of the real recorder, as in that module's `test_the_owner_yields_to_...`) after
the line's limit was lowered to 0 by a committed UPDATE, and the core - handed the route past the router, the stand style
of `test_p028_e3_freeze_boundary_postgres.py` - refuses it `insufficient_capacity` and stores that `ABORTED` row. The
recorder then yields to it, and every replay answers it; the one `tx.failed` must say the same, not `PAYMENT_TIMEOUT`.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import update

import app.core.simulator.money_replay as money_replay
from app.core.payments.service import PaymentService
from app.db.models.trustline import TrustLine
from tests.integration.p019_stand import api, factory, tx_row  # noqa: F401 - fixtures
from tests.integration.test_p019_staged_refusal_is_durable_postgres import (
    _run_the_tick_behind_a_slow_edit,
    _two_equivalent_tick,
)


@pytest.mark.asyncio
async def test_the_owner_publishes_the_stored_refusal_of_an_aborted_winner(api, factory, monkeypatch) -> None:  # noqa: F811
    t = await _two_equivalent_tick(factory, monkeypatch)
    original_record, original_create = money_replay.record_definitive_refusal, PaymentService.create_payment_internal_staged
    winners: list[tuple] = []

    async def an_aborted_winner_first(sessions, refusal):
        t.gate.release.set()
        await asyncio.wait_for(t.gate.committed.wait(), timeout=20)
        call = t.calls[1]
        async with factory() as session:
            await session.execute(update(TrustLine).where(TrustLine.equivalent_id == t.second.id).values(limit=Decimal("0")))
            await session.commit()
        async with factory() as session:
            service = PaymentService(session)
            route = [t.world.sender.pid, t.world.receiver.pid]
            service.router.find_flow_routes = lambda *_a, **_k: [(route, Decimal(call["amount"]))]
            async with session.begin_nested():
                won = await original_create(
                    service, call["sender_id"], to_pid=call["to_pid"], equivalent=call["equivalent"],
                    amount=call["amount"], allowed_participant_pids=call.get("allowed_participant_pids"),
                    idempotency_key=call["idempotency_key"],
                )
            await session.commit()
        winners.append((won.result.status, (won.result.error.details or {}).get("reason") if won.result.error else None))
        return await original_record(sessions, refusal)

    monkeypatch.setattr(money_replay, "record_definitive_refusal", an_aborted_winner_first)
    queued = await _run_the_tick_behind_a_slow_edit(api, factory, t)

    # The mechanism, before the outcome.
    assert queued, f"premise: the second payment never waited on the edit's row lock {t.subject.guard}"
    assert t.calls[1].get("raised") == "PaymentTransactionUnusable", t.calls[1]
    assert winners == [("ABORTED", "insufficient_capacity")], winners
    stored = await tx_row(factory, str(t.calls[1]["idempotency_key"]))
    assert stored is not None and stored[0] == "ABORTED", stored
    assert (stored[1] or {}).get("details", {}).get("reason") == "insufficient_capacity", stored
    # The outcome: the one observation of seq 1 is the stored refusal.
    failed = [e for e in t.sse.events if e.get("type") == "tx.failed"]
    assert len(failed) == 1, failed
    assert (failed[0].get("error") or {}).get("code") != "PAYMENT_TIMEOUT", failed
    assert "insufficient_capacity" in repr(failed[0].get("error")), failed
