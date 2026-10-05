"""Reproducer R-4 (p030 external review): the core does not check the routes against the requested amount.

The router's answer is replaced (`PaymentRouter.find_flow_routes`, consumed at `app/core/payments/service.py:1382`
and turned into routes at `:1698-1701`) by two routes summing 7.00 for a 10.00 request. The assertion is the
CORRECT behaviour: the payment is refused and no debt changes. Red on the current tree means it reproduces.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.ledger.reconciliation import open_verification_snapshot, verify_journal_equals_change
from app.core.payments.router import PaymentRouter
from app.db.models.transaction import Transaction
from tests.integration.p019_stand import (  # noqa: F401 - `api` and `factory` are fixtures
    api,
    build_api_world,
    debts,
    envelopes,
    factory,
    payment_body,
    tx_row,
)


@pytest.mark.asyncio
async def test_r4_routes_summing_less_than_the_request_are_refused(api, factory, monkeypatch) -> None:  # noqa: F811
    world = await build_api_world(api, factory)
    a, b = world.alice["pid"], world.bob["pid"]
    calls: list = []

    def short_routes(self, from_pid, to_pid, amount, **kwargs):
        calls.append((from_pid, to_pid, Decimal(amount)))
        return [([a, b], Decimal("4.00")), ([a, b], Decimal("3.00"))]

    monkeypatch.setattr(PaymentRouter, "find_flow_routes", short_routes)
    assert await debts(factory, world) == {}, "stand: no debt before"

    body = payment_body(world, world.alice, world.bob, "10.00")
    resp = await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])

    assert calls == [(a, b, Decimal("10.00"))], f"stand: the substituted router was not consulted: {calls}"
    debts_after = await debts(factory, world)
    row = await tx_row(factory, body["tx_id"])
    async with factory() as s:
        payload = (await s.execute(select(Transaction.payload).where(Transaction.tx_id == body["tx_id"]))).scalar_one_or_none()
    async with factory() as s:
        await open_verification_snapshot(s)
        outcome = await verify_journal_equals_change(s, world.equivalent_id)
        await s.rollback()
    facts = {
        "http": resp.status_code,
        "body": resp.text[:400],
        "tx_state": row,
        "tx_amount": None if payload is None else payload.get("amount"),
        "tx_routes": None if payload is None else payload.get("routes"),
        "debts_after": {f"{k[0]}->{k[1]}": str(v) for k, v in debts_after.items()},
        "envelopes": await envelopes(factory, body["tx_id"]),
        "reconcile": outcome.status,
        "findings": list(outcome.findings),
    }
    assert not (resp.status_code == 200 and resp.json().get("status") == "COMMITTED"), f"committed: {facts}"
    assert debts_after == {}, f"debts changed: {facts}"
