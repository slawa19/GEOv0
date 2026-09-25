"""Simulator trust GROWTH must never lower a limit - and so never below the debt it already secures.

Found by the Codex fix-delta review of 019 stage 3 (`P1-B`, class 1, pre-existing). The growth step
(`TrustDriftEngine.apply_trust_growth`) wrote `min(current * (1 + growth_rate), original * max_growth)`
with no lower bound. When the limit had been raised above `original * max_growth` - the simulator's
`trustline-update` action (`app/api/v1/simulator.py`, `tl.limit = new_limit_dec`) does exactly that and
does not touch the run's `original_limit` - the "growth" LOWERED it, below the committed debt. Serial,
no race.

THE PATH IS THE TICK'S OWN: `RealClearingEngine.tick_real_mode_clearing` with the real
`ClearingService` on a mode-B PostgreSQL SERIALIZABLE clone, calling the real
`TrustDriftEngine.apply_trust_growth` on its clearing session - the wiring of
`real_runner_impl.py` (`apply_trust_growth=self._trust_drift_engine.apply_trust_growth`).

THE SCHEDULE. R trusts S (edge R->S), original limit 100, so the growth cap is 200. The limit is raised
to 300 the way `trustline-update` writes it. S pays R 250 through `PaymentService.pay` (debt S->R 250).
A cycle S->R->Z->S of 10 is cleared by the tick: debt S->R becomes 240, and R->S is a touched edge, so
growth runs on it. Before the fix growth wrote min(330, 200) = 200 < 240.

The other two edges of the cycle are the counter-checks, in the same tick (original 100 each): S->Z
starts at 100 and must grow to 110 (rate 0.1); Z->R starts at 190 and must stop at the cap
original * max_growth = 200.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.edge_patch_builder import EdgePatchBuilder
from app.core.simulator.models import RunRecord
from app.core.simulator.real_clearing_engine import RealClearingEngine
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.debt_setup import debt_fixture_setup
from tests.integration.test_p015_p1_money_replay_postgres import factory  # noqa: F401 - fixture

_LOG = logging.getLogger("tests.trust_growth_never_lowers")


class _Sse:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def next_event_id(self, run: RunRecord) -> str:
        run._event_seq += 1
        return f"event-{run._event_seq}"

    def broadcast(self, _run_id: str, payload: dict) -> None:
        self.events.append(payload)


async def _world(factory):  # noqa: F811
    """Three participants S, R, Z; trust lines R->S (100), Z->R (190), S->Z (100); debts R->Z and
    Z->S of 10 each, so a payment S->R closes a cycle S->R->Z->S."""

    n = uuid.uuid4().hex[:8].upper()
    async with factory() as s:
        eq = Equivalent(code=f"PTG{n}"[:16], precision=2, is_active=True)
        s_, r_, z_ = [
            Participant(
                pid=f"PTG_{role}_{n}", display_name=role, public_key=f"pk_ptg_{role}_{n}",
                type="person", status="active",
            )
            for role in ("S", "R", "Z")
        ]
        s.add_all([eq, s_, r_, z_])
        await s.flush()
        s.add_all(
            [
                # creditor -> debtor
                TrustLine(from_participant_id=r_.id, to_participant_id=s_.id, equivalent_id=eq.id, limit=Decimal("100.00"), status="active"),
                TrustLine(from_participant_id=z_.id, to_participant_id=r_.id, equivalent_id=eq.id, limit=Decimal("190.00"), status="active"),
                TrustLine(from_participant_id=s_.id, to_participant_id=z_.id, equivalent_id=eq.id, limit=Decimal("100.00"), status="active"),
            ]
        )
        async with debt_fixture_setup(s, label="setup"):
            s.add(Debt(debtor_id=r_.id, creditor_id=z_.id, equivalent_id=eq.id, amount=Decimal("10.00")))
            s.add(Debt(debtor_id=z_.id, creditor_id=s_.id, equivalent_id=eq.id, amount=Decimal("10.00")))
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, s_, r_, z_


def _run_and_engine(eq, s_, r_, z_) -> tuple[RunRecord, dict, TrustDriftEngine]:
    run = RunRecord(run_id=f"ptg-{uuid.uuid4().hex[:8]}", scenario_id="ptg", mode="real", state="running")
    run.tick_index = 5
    run._real_participants = [(p.id, p.pid) for p in (s_, r_, z_)]
    run._edges_by_equivalent = {eq.code: [(r_.pid, s_.pid), (z_.pid, r_.pid), (s_.pid, z_.pid)]}
    scenario = {
        "equivalents": [eq.code],
        "settings": {"trust_drift": {"enabled": True, "growth_rate": 0.1, "max_growth": 2.0}},
        # The scenario's limits are what `init_trust_drift` records as `original_limit`.
        "trustlines": [
            {"from": r_.pid, "to": s_.pid, "equivalent": eq.code, "limit": "100.00", "status": "active"},
            {"from": z_.pid, "to": r_.pid, "equivalent": eq.code, "limit": "100.00", "status": "active"},
            {"from": s_.pid, "to": z_.pid, "equivalent": eq.code, "limit": "100.00", "status": "active"},
        ],
    }
    engine = TrustDriftEngine(
        sse=_Sse(), utc_now=lambda: datetime.now(timezone.utc), logger=_LOG,
        get_scenario_raw=lambda _s: scenario,
    )
    engine.init_trust_drift(run, scenario)
    assert run._trust_drift_config.enabled, run._trust_drift_config
    run._scenario_raw = scenario
    return run, scenario, engine


@pytest.mark.asyncio
async def test_growth_after_a_raise_above_the_cap_never_lowers_the_limit_below_the_debt(factory) -> None:  # noqa: F811
    eq, s_, r_, z_ = await _world(factory)
    run, scenario, drift = _run_and_engine(eq, s_, r_, z_)

    async def _limits_and_debts():
        async with factory() as observer:
            limits = {
                (row.from_participant_id, row.to_participant_id): Decimal(str(row.limit))
                for row in (
                    await observer.execute(
                        select(TrustLine).where(TrustLine.equivalent_id == eq.id, TrustLine.status == "active")
                    )
                ).scalars()
            }
            debts = {
                (row.creditor_id, row.debtor_id): Decimal(str(row.amount))
                for row in (await observer.execute(select(Debt).where(Debt.equivalent_id == eq.id))).scalars()
            }
        return limits, debts

    try:
        # 1. The simulator's `trustline-update` raises R->S to 300; the run's original stays 100.
        async with factory() as s:
            await s.execute(
                update(TrustLine)
                .where(TrustLine.from_participant_id == r_.id, TrustLine.to_participant_id == s_.id, TrustLine.equivalent_id == eq.id)
                .values(limit=Decimal("300.00"))
            )
            await s.commit()
        PaymentRouter.invalidate_cache(eq.code)

        # 2. Payments build debt S->R of 250 against the raised limit.
        paid = await PaymentService.pay(
            factory, s_.id,
            PaymentCreateRequest(tx_id=str(uuid.uuid4()), to=r_.pid, equivalent=eq.code, amount="250.00", signature="__internal__"),
            require_signature=False,
        )

        # 3. The tick clears the cycle S->R->Z->S (10) and grows the touched edges.
        clearing = RealClearingEngine(
            lock=threading.RLock(), sse=_Sse(), utc_now=lambda: datetime.now(timezone.utc), logger=_LOG,
            edge_patch_builder=EdgePatchBuilder(logger=_LOG),
            clearing_max_depth_limit=6, clearing_max_fx_edges_limit=8, real_clearing_time_budget_ms=10_000,
        )

        async def _no_patch(**_k):
            return []

        cleared = await clearing.tick_real_mode_clearing(
            None, run_id=run.run_id, run=run, equivalents=[eq.code],
            apply_trust_growth=drift.apply_trust_growth,
            build_edge_patch_for_equivalent=_no_patch,
            broadcast_topology_edge_patch=lambda **_k: None,
            async_session_local=factory,
        )
    finally:
        PaymentRouter.invalidate_cache(eq.code)

    # ── controls: the schedule really happened ────────────────────────────────────────────────
    assert paid.status == "COMMITTED", paid
    assert cleared[eq.code] == Decimal("10"), cleared
    hist = run._edge_clearing_history[f"{r_.pid}:{s_.pid}:{eq.code}"]
    assert hist.clearing_count == 1 and hist.original_limit == Decimal("100.00"), hist
    limits, debts = await _limits_and_debts()
    assert debts.get((r_.id, s_.id)) == Decimal("240.00"), debts

    # ── counter-checks, same tick: growth still raises, and the cap still stops it ─────────────
    assert limits[(s_.id, z_.id)] == Decimal("110.00"), limits  # 100 * 1.1
    assert limits[(z_.id, r_.id)] == Decimal("200.00"), limits  # min(190 * 1.1, 100 * 2.0)

    # ── the invariant ───────────────────────────────────────────────────────────────────────────
    limit_rs, debt_rs = limits[(r_.id, s_.id)], debts[(r_.id, s_.id)]
    assert limit_rs >= debt_rs, (
        f"trust growth lowered R->S to {limit_rs}, below the debt {debt_rs} it already secures"
    )
    # Growth only raises: with the cap (200) below the current limit (300) the limit is left alone.
    assert limit_rs == Decimal("300.00"), limit_rs
