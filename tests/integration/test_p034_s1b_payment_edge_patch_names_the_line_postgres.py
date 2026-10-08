"""034 S1b: the `edge_patch` of a payment names the trust line whose debt the payment moved.

WHAT WAS WRONG (observed by execution in S1a, on `75dafc82`; still so on `0f248b9c`). A payment hop goes from the
payer to the payee; the trust line it draws on goes the other way, from the payee (the creditor) to the payer (the
debtor), and that is the direction of every edge the client holds (`source` = creditor, `target` = debtor). The
payment paths handed the HOP pairs to the edge-patch builder as if they were lines. For S paying R over the line
R -> S the patch then described "S -> R" - a line that does not exist, with `used` and `available` 0.00 - and the
line R -> S, whose `used` had just grown, was not patched at all.

Two payment paths build this patch and both are held here: the tick (`RealPaymentsExecutor`, read after the commit)
and the Interact route `payment-real`. The SSE shape is unchanged - the same `edge_patch` items, about the right
edges. The route (`edges`, payer -> payee), the `node_patch` and the published amounts are not this module's subject.

WHAT THESE TESTS DO NOT SEE: a multi-hop route (one hop each here), and how the client applies a patch.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

import app.api.v1.simulator as simulator_module
from app.config import settings
from app.core.payments.router import PaymentRouter
from app.db.models.trustline import TrustLine
from tests.integration.test_p015_p1_money_replay_postgres import (  # noqa: F401 - `factory` is a fixture
    _LIMIT,
    _OPENING,
    _Sse,
    _debts,
    _forget_the_route_cache,
    _install,
    _record_plans,
    _run_record,
    _runner,
    _scenario,
    _seed,
    factory,
)
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture
from tests.unit.test_interact_actions_backend_p1 import (  # noqa: F401 - `interact_actions_enabled` is a fixture
    _seed_alice_bob_uah,
    interact_actions_enabled,
)


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


@pytest.mark.asyncio
async def test_the_tick_patches_the_line_the_payment_drew_on(factory, monkeypatch) -> None:  # noqa: F811
    """One real tick: S pays R over the only line there is, R -> S (limit 1000.00, 100.00 used before)."""

    world = await _seed(factory)
    payer, payee = world.sender.pid, world.receiver.pid
    try:
        sse = _Sse()
        run = _run_record(world, f"p034-s1b-{uuid.uuid4().hex[:8]}")
        runner = _runner(run, _scenario(world), sse)
        _install(monkeypatch, factory)
        plans = _record_plans(monkeypatch, runner)
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)
        debts = await _debts(factory, world)
    finally:
        _forget_the_route_cache(world)

    # Controls: one payment was carried, payer -> payee, and it grew the payer's debt to the payee.
    assert len(plans) == 1 and len(plans[0]) == 1 and run.last_error is None, (plans, run.last_error)
    amount = Decimal(plans[0][0].amount)
    used = _OPENING + amount
    assert amount > 0 and debts == {(payer, payee): used}, debts
    updated = [e for e in sse.events if e.get("type") == "tx.updated"]
    assert len(updated) == 1 and updated[0]["edges"] == [{"from": payer, "to": payee, "style": None}], updated

    patch = [(p["source"], p["target"], p["used"], p["available"]) for p in updated[0].get("edge_patch") or []]
    assert patch == [(payee, payer, _money(used), _money(_LIMIT - used))], (
        f"{payer} paid {payee} {amount} over the line {payee} -> {payer} (limit {_LIMIT}, used {used} after it). "
        f"edge_patch (source, target, used, available): {patch}. Expected the line itself and nothing else: "
        f"{[(payee, payer, _money(used), _money(_LIMIT - used))]}"
    )


@pytest.mark.asyncio
async def test_the_interact_payment_patches_the_line_the_payment_drew_on(
    client, db_session, interact_actions_enabled, monkeypatch  # noqa: F811
) -> None:
    """The Interact route: alice pays bob 1.00 over bob's line to her (bob -> alice, limit 10.00)."""

    alice, bob, uah = await _seed_alice_bob_uah(db_session)
    db_session.add(TrustLine(from_participant_id=bob.id, to_participant_id=alice.id, equivalent_id=uah.id,
                             status="active", limit=Decimal("10")))
    await db_session.commit()
    PaymentRouter.invalidate_cache("UAH")

    emitted: list[dict] = []
    monkeypatch.setattr(simulator_module.SseEventEmitter, "emit_tx_updated",
                        lambda _self, **kwargs: emitted.append(kwargs))
    try:
        r = await client.post("/api/v1/simulator/runs/test-run/actions/payment-real",
                              headers={"X-Admin-Token": settings.ADMIN_TOKEN},
                              json={"from_pid": "alice", "to_pid": "bob", "equivalent": "UAH", "amount": "1"})
    finally:
        PaymentRouter.invalidate_cache("UAH")

    # Controls: the payment was carried and its event was emitted with the route alice -> bob.
    assert r.status_code == 200, r.text
    assert len(emitted) == 1 and emitted[0]["edges"] == [{"from": "alice", "to": "bob"}], emitted

    patch = [(p["source"], p["target"], p["used"], p["available"]) for p in emitted[0].get("edge_patch") or []]
    assert patch == [("bob", "alice", "1.00", "9.00")], (
        "alice paid bob 1.00 over the line bob -> alice (limit 10.00). edge_patch (source, target, used, "
        f"available): {patch}. Expected the line itself and nothing else: [('bob', 'alice', '1.00', '9.00')]"
    )
