"""Programme 019, stage-3 review (Codex, frozen `fae2208`, P1 class 1): simulator trust DECAY lowering a
limit from a stale debt snapshot, concurrently with an API payment, commits debt above the limit.

THE SCHEDULE IS REAL; nothing is injected into the driver. Debt S->R is 90 under limit 100 (R trusts
S). An API payment of 10 (`PaymentService.pay`, one SERIALIZABLE transaction) routes and prepares -
its snapshot reads limit 100 - and is held at the entry of the engine's commit phase, before it
writes debt. Meanwhile the simulator's decay (`TrustDriftEngine.apply_trust_decay`, the tick tail's
own code) runs on its own transaction with the Python debt snapshot the tick retained (90): ratio
0.9 >= threshold 0.8, new limit max(100 * 0.98, 30, floor 90) = 98, committed. The payment is then
released.

BEFORE THE FIX (`fae2208`): the decay never read the debt row, so SERIALIZABLE saw only the payment's
read of the trust line against the decay's write (one direction, no cycle) and let both commit:
debt 100 > limit 98 - a trust-limit violation no request ever asked for. The creditor's own PATCH
(`trustlines/service.py`, `_get_used_amount` in its transaction) is not open to this.

THE INVARIANT, observed on a fresh session: every active trust line's limit is at least the debt it
carries. Controls first: the payment really was held after prepare and before its debt write while the
decay committed (barrier hit once), and the decay really lowered the limit.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.debt_setup import debt_fixture_setup
from tests.integration.p019_stand import finish
from tests.integration.test_p015_p1_money_replay_postgres import factory  # noqa: F401 - fixture
from tests.p019_support import require_target


async def _world(factory):  # noqa: F811
    n = uuid.uuid4().hex[:8].upper()
    async with factory() as s:
        eq = Equivalent(code=f"P19D{n}"[:16], precision=2, is_active=True)
        sender, receiver = [
            Participant(
                pid=f"P19D_{role}_{n}", display_name=role, public_key=f"pk_p19d_{role}_{n}",
                type="person", status="active",
            )
            for role in ("S", "R")
        ]
        s.add_all([eq, sender, receiver])
        await s.flush()
        s.add(
            TrustLine(
                from_participant_id=receiver.id, to_participant_id=sender.id,
                equivalent_id=eq.id, limit=Decimal("100.00"), status="active",
            )
        )
        async with debt_fixture_setup(s, label="setup"):
            s.add(Debt(debtor_id=sender.id, creditor_id=receiver.id, equivalent_id=eq.id, amount=Decimal("90.00")))
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, sender, receiver


def _decaying_run(eq, sender, receiver) -> tuple[RunRecord, dict]:
    run = RunRecord(run_id=f"p019-decay-{uuid.uuid4().hex[:8]}", scenario_id="p019-decay", mode="real", state="running")
    run._real_participants = [(sender.id, sender.pid), (receiver.id, receiver.pid)]
    run._trust_drift_config = TrustDriftConfig(
        enabled=True, decay_rate=0.02, min_limit_ratio=0.3, overload_threshold=0.8
    )
    run._edge_clearing_history = {
        f"{receiver.pid}:{sender.pid}:{eq.code}": EdgeClearingHistory(original_limit=Decimal("100.00"))
    }
    scenario = {
        "equivalents": [eq.code],
        "trustlines": [{"from": receiver.pid, "to": sender.pid, "equivalent": eq.code, "limit": "100.00", "status": "active"}],
    }
    return run, scenario


@pytest.mark.asyncio
async def test_a_decay_from_a_stale_snapshot_never_leaves_debt_above_the_limit(factory, monkeypatch) -> None:  # noqa: F811
    eq, sender, receiver = await _world(factory)
    run, scenario = _decaying_run(eq, sender, receiver)
    engine = TrustDriftEngine(sse=None, utc_now=None, logger=logging.getLogger("tests.p019.decay"), get_scenario_raw=lambda _s: scenario)

    reached, release, hits = asyncio.Event(), asyncio.Event(), []
    original_commit = PaymentEngine.commit

    async def held_commit(self, tx_id, *args, **kwargs):
        if not hits:
            hits.append(tx_id)
            reached.set()
            await release.wait()
        return await original_commit(self, tx_id, *args, **kwargs)

    monkeypatch.setattr(PaymentEngine, "commit", held_commit)
    request = PaymentCreateRequest(
        tx_id=str(uuid.uuid4()), to=receiver.pid, equivalent=eq.code, amount="10.00", signature="__internal__"
    )

    async def pay():
        try:
            return await PaymentService.pay(factory, sender.id, request, require_signature=False)
        except Exception as exc:  # noqa: BLE001 - either outcome is allowed; the invariant decides
            return exc

    payment = asyncio.create_task(pay())
    try:
        await asyncio.wait_for(reached.wait(), timeout=20)
        # The tick tail's decay, on its own transaction, with the debt snapshot the tick retained.
        async with factory() as tail:
            decayed = await engine.apply_trust_decay(
                run, tail, 7, {(sender.pid, receiver.pid, eq.code): Decimal("90.00")}, scenario
            )
            await tail.commit()
        release.set()
        outcome = await asyncio.wait_for(payment, timeout=60)
    finally:
        release.set()
        await finish(payment)
        PaymentRouter.invalidate_cache(eq.code)

    # ── controls ──────────────────────────────────────────────────────────────────────────────
    assert len(hits) == 1, hits
    assert decayed.updated_count == 1, decayed
    async with factory() as observer:
        limit = await observer.scalar(
            select(TrustLine.limit).where(TrustLine.equivalent_id == eq.id, TrustLine.status == "active")
        )
        debt = await observer.scalar(select(Debt.amount).where(Debt.equivalent_id == eq.id))
    assert Decimal(str(limit)) == Decimal("98.00"), limit  # the decay did lower the limit

    require_target(
        Decimal(str(debt)) <= Decimal(str(limit)),
        f"debt {debt} above the decayed limit {limit}; payment ended with {outcome!r}",
    )


@pytest.mark.asyncio
async def test_a_decay_after_a_committed_payment_floors_at_the_current_debt(factory) -> None:  # noqa: F811
    """The sequential form: the API payment of 10 has COMMITTED (debt 100) since the tick took its
    snapshot (90). The decay's floor must be the debt the database holds now, not the snapshot's - so
    the limit is not lowered below 100 (the SSI dependency of the concurrent form does not help here:
    nothing is concurrent). Kills the mutation that reads the row but floors at the snapshot."""

    eq, sender, receiver = await _world(factory)
    run, scenario = _decaying_run(eq, sender, receiver)
    engine = TrustDriftEngine(sse=None, utc_now=None, logger=logging.getLogger("tests.p019.decay"), get_scenario_raw=lambda _s: scenario)
    request = PaymentCreateRequest(
        tx_id=str(uuid.uuid4()), to=receiver.pid, equivalent=eq.code, amount="10.00", signature="__internal__"
    )
    try:
        paid = await PaymentService.pay(factory, sender.id, request, require_signature=False)
        async with factory() as tail:
            await engine.apply_trust_decay(
                run, tail, 7, {(sender.pid, receiver.pid, eq.code): Decimal("90.00")}, scenario
            )
            await tail.commit()
    finally:
        PaymentRouter.invalidate_cache(eq.code)

    assert paid.status == "COMMITTED", paid
    async with factory() as observer:
        limit = await observer.scalar(
            select(TrustLine.limit).where(TrustLine.equivalent_id == eq.id, TrustLine.status == "active")
        )
        debt = await observer.scalar(select(Debt.amount).where(Debt.equivalent_id == eq.id))
    assert Decimal(str(debt)) == Decimal("100.00"), debt
    require_target(
        Decimal(str(debt)) <= Decimal(str(limit)),
        f"the decay lowered the limit to {limit} below the committed debt {debt}",
    )
