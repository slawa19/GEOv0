"""Reproducer R-4 (p030 external review), the guard of F-030-2 since 030 S2: the core binds the routes to the request.

The router's answer is replaced (`PaymentRouter.find_flow_routes`, consumed by `PaymentService` and turned into
routes before `_bind_payment`) by routes that do not carry the request: two routes summing 7.00 for a 10.00
request; a route that ends at somebody other than the payee; one part handed over twice (20.00 for 10.00). The
assertion is the CORRECT behaviour: the payment is refused before any write - no `COMMITTED` row, no operation
envelope, no debt. A router defect is the only way in (the real router is not shown to produce these), so this is
detection of our own defect, not a defence against a client.

Counter-check: a legitimate multi-route answer - two parts over the one Alice -> Bob line, summing to the request -
still commits, and its debt is exactly the request.
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


def _substitute_router(monkeypatch, routes_of) -> list:
    calls: list = []

    def substituted(self, from_pid, to_pid, amount, **kwargs):
        calls.append((from_pid, to_pid, Decimal(amount)))
        return routes_of(from_pid, to_pid)

    monkeypatch.setattr(PaymentRouter, "find_flow_routes", substituted)
    return calls


async def _pay(api, factory, world, amount: str) -> tuple[object, dict, dict]:  # noqa: F811
    body = payment_body(world, world.alice, world.bob, amount)
    resp = await api.post("/api/v1/payments", json=body, headers=world.alice["headers"])
    async with factory() as s:
        payload = (
            await s.execute(select(Transaction.payload).where(Transaction.tx_id == body["tx_id"]))
        ).scalar_one_or_none()
    async with factory() as s:
        await open_verification_snapshot(s)
        outcome = await verify_journal_equals_change(s, world.equivalent_id)
        await s.rollback()
    facts = {
        "http": resp.status_code,
        "body": resp.text[:400],
        "tx_state": await tx_row(factory, body["tx_id"]),
        "tx_amount": None if payload is None else payload.get("amount"),
        "tx_routes": None if payload is None else payload.get("routes"),
        "debts_after": {f"{k[0]}->{k[1]}": str(v) for k, v in (await debts(factory, world)).items()},
        "envelopes": await envelopes(factory, body["tx_id"]),
        "reconcile": outcome.status,
        "findings": list(outcome.findings),
    }
    return resp, body, facts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cell",
    ["routes_short_of_the_request", "a_route_ending_elsewhere", "a_part_handed_over_twice"],
)
async def test_r4_routes_that_do_not_carry_the_request_are_refused(api, factory, monkeypatch, cell) -> None:  # noqa: F811
    world = await build_api_world(api, factory)
    a, b, c = world.alice["pid"], world.bob["pid"], world.carol["pid"]
    answer = {
        "routes_short_of_the_request": [([a, b], Decimal("4.00")), ([a, b], Decimal("3.00"))],
        "a_route_ending_elsewhere": [([a, b], Decimal("5.00")), ([a, c], Decimal("5.00"))],
        "a_part_handed_over_twice": [([a, b], Decimal("10.00")), ([a, b], Decimal("10.00"))],
    }[cell]
    calls = _substitute_router(monkeypatch, lambda _f, _t: answer)
    assert await debts(factory, world) == {}, "stand: no debt before"

    resp, body, facts = await _pay(api, factory, world, "10.00")

    assert calls == [(a, b, Decimal("10.00"))], f"stand: the substituted router was not consulted: {calls}"
    assert not (resp.status_code == 200 and resp.json().get("status") == "COMMITTED"), f"committed: {facts}"
    assert facts["debts_after"] == {}, f"debts changed: {facts}"
    assert facts["envelopes"] == [], f"an operation was recorded: {facts}"
    # 030 S2, §15 `T3092` finding 2: the admitted request's refusal is stored as the idempotent `ABORTED` row of the
    # stored-refusal contract - that row, if any, and nothing else: no money effect, no envelope, no `COMMITTED`.
    assert facts["tx_state"] is None or facts["tx_state"][0] == "ABORTED", f"a row other than ABORTED: {facts}"


@pytest.mark.asyncio
async def test_r4_control_a_legitimate_multi_route_answer_commits(api, factory, monkeypatch) -> None:  # noqa: F811
    world = await build_api_world(api, factory)
    a, b = world.alice["pid"], world.bob["pid"]
    calls = _substitute_router(monkeypatch, lambda _f, _t: [([a, b], Decimal("6.00")), ([a, b], Decimal("4.00"))])

    resp, body, facts = await _pay(api, factory, world, "10.00")

    assert calls, "control: the substituted router was not consulted"
    assert resp.status_code == 200 and resp.json().get("status") == "COMMITTED", facts
    assert {k: Decimal(v) for k, v in facts["debts_after"].items()} == {f"{a}->{b}": Decimal("10.00")}, facts
    assert facts["reconcile"] == "PASSED", facts
