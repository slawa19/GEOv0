"""028 F-028-30/31/32, pulled into E2 (orchestrator 2026-10-04): the simulator's own writers in the equivalent's step.

E2 made every door refuse an amount finer than `10**-precision` (owner В-4). The simulator wrote such amounts itself:
the inject truncated a debt to 0.01, trust drift grew and decayed limits at the storage grain 1E-8, and the payment
planner always picked cents. Each test names what the base did. Mode A, except where it says otherwise.
"""

from __future__ import annotations

import logging
import random
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.ledger.reconciliation import verify_journal_equals_change
from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.real_payment_planner import RealPaymentPlanner
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner


async def _world(db_session, precisions: dict[str, int], limit: str = "100"):
    n = uuid.uuid4().hex[:6].upper()
    eqs = {name: Equivalent(code=f"S{name}{n}", precision=p, is_active=True) for name, p in precisions.items()}
    c = Participant(pid=f"C_{n}", display_name="C", public_key=f"pk_c_{n}", type="person", status="active")
    d = Participant(pid=f"D_{n}", display_name="D", public_key=f"pk_d_{n}", type="person", status="active")
    db_session.add_all([*eqs.values(), c, d])
    await db_session.flush()
    for eq in eqs.values():
        db_session.add(TrustLine(from_participant_id=c.id, to_participant_id=d.id, equivalent_id=eq.id,
                                 limit=Decimal(limit), status="active", policy={}))
    await db_session.commit()
    return eqs, c, d


async def _limit(db_session, eq, c, d) -> Decimal:
    return (await db_session.execute(select(TrustLine.limit).where(
        TrustLine.equivalent_id == eq.id, TrustLine.from_participant_id == c.id))).scalar_one()


async def _debt(db_session, eq, c, d) -> Decimal | None:
    return (await db_session.execute(select(Debt.amount).where(
        Debt.equivalent_id == eq.id, Debt.creditor_id == c.id, Debt.debtor_id == d.id))).scalar_one_or_none()


async def _inject(db_session, eqs, c, d, effects, *, max_total=None):
    runner, artifacts = _make_runner(inject_enabled=True)
    event = {"type": "inject", "time": 0, "effects": [
        {"op": "inject_debt", "from": c.pid, "to": d.pid, "equivalent": eqs[k].code, "amount": a} for k, a in effects]}
    if max_total is not None:
        event["metadata"] = {"max_total_amount": max_total}
    scenario = {"participants": [{"id": c.pid}, {"id": d.pid}], "events": [event], "trustlines": [
        {"from": c.pid, "to": d.pid, "equivalent": eq.code, "limit": "100", "status": "active"} for eq in eqs.values()]}
    await runner._apply_due_scenario_events(db_session, run_id="r-028-inject", scenario=scenario, run=_make_run(
        participants=[(c.id, c.pid), (d.id, d.pid)], equivalents=[eq.code for eq in eqs.values()]))
    [note] = [p for p in artifacts.payloads if p.get("type") == "note"]
    return note["scenario"]["stats"]


# ------------------------------------------------------------------ F-028-30: inject


@pytest.mark.asyncio
async def test_an_inject_finer_than_the_step_is_skipped_with_a_reason_not_truncated(db_session) -> None:
    """Base: `1.239` at precision 2 was stored as `1.23`."""

    eqs, c, d = await _world(db_session, {"A": 2})
    stats = await _inject(db_session, eqs, c, d, [("A", "1.239")])
    assert await _debt(db_session, eqs["A"], c, d) is None
    assert stats["skipped"] == 1 and stats["skipped_reasons"] == {"amount_precision_exceeded": 1}, stats


@pytest.mark.asyncio
async def test_an_inject_at_precision_8_is_written_whole_and_the_verifier_agrees(db_session) -> None:
    """Base: `1.239` at precision 8 was stored as `1.23` (the 0.01 truncation). The criterion (b) subset must
    accept the whole amount: it quantized the intent to 0.01 and demanded whole cents."""

    eqs, c, d = await _world(db_session, {"A": 8})
    await _inject(db_session, eqs, c, d, [("A", "1.239")])
    assert await _debt(db_session, eqs["A"], c, d) == Decimal("1.239")
    outcome = await verify_journal_equals_change(db_session, eqs["A"].id)
    assert [f for f in outcome.findings if f["kind"].startswith("b_")] == [], outcome.findings


@pytest.mark.asyncio
async def test_the_inject_total_is_bounded_per_equivalent(db_session) -> None:
    """Base: two effects of different equivalents, each under `max_total_amount`, summed across them - the
    second was refused (owner В-3: equivalents are independent)."""

    eqs, c, d = await _world(db_session, {"A": 2, "B": 2})
    stats = await _inject(db_session, eqs, c, d, [("A", "6"), ("B", "6")], max_total="10")
    assert (await _debt(db_session, eqs["A"], c, d), await _debt(db_session, eqs["B"], c, d)) == (6, 6), stats
    assert stats["total_amount"] == {eqs["A"].code: "6", eqs["B"].code: "6"}, stats


# ------------------------------------------------------------------ F-028-31: trust drift


def _drift(eqs, c, d, *, limit: str, original: str, **cfg):
    eq = eqs["A"]
    run = RunRecord(run_id="r-028-drift", scenario_id="s-028-drift", mode="real", state="running")
    run._real_participants = [(c.id, c.pid), (d.id, d.pid)]
    run._trust_drift_config = TrustDriftConfig(enabled=True, **cfg)
    run._scenario_raw = {"trustlines": [{"from": c.pid, "to": d.pid, "equivalent": eq.code, "limit": limit,
                                         "status": "active"}]}
    run._edge_clearing_history = {f"{c.pid}:{d.pid}:{eq.code}": EdgeClearingHistory(original_limit=Decimal(original))}
    engine = TrustDriftEngine(sse=None, utc_now=lambda: None, logger=logging.getLogger("p028"),
                              get_scenario_raw=lambda _sid: run._scenario_raw)
    return run, engine


@pytest.mark.parametrize("limit,precision,growth,max_growth,expected", [
    ("100.33", 2, 0.10, 2.0, "110.36"),     # base: 110.363 refused by the step door, growth rolled back
    ("10.005", 8, 5.0, 2.0, "20.01"),       # base: the ceiling was built from 10.00 (truncated original)
])
@pytest.mark.asyncio
async def test_trust_growth_is_in_the_step_from_the_whole_original_limit(
        db_session, limit, precision, growth, max_growth, expected) -> None:
    eqs, c, d = await _world(db_session, {"A": precision}, limit=limit)
    run, engine = _drift(eqs, c, d, limit=limit, original=limit, growth_rate=growth, max_growth=max_growth)
    await engine.apply_trust_growth(run=run, clearing_session=db_session, touched_edges={(c.pid, d.pid)},
                                    eq_code=eqs["A"].code, tick_index=1, cleared_amount_per_edge={})
    assert await _limit(db_session, eqs["A"], c, d) == Decimal(expected)


@pytest.mark.asyncio
async def test_trust_decay_is_in_the_step(db_session) -> None:
    """Base: `100.33 * 0.9 = 90.297` at precision 2 was refused by the step door and the decay rolled back."""

    eqs, c, d = await _world(db_session, {"A": 2}, limit="100.33")
    run, engine = _drift(eqs, c, d, limit="100.33", original="100.33", decay_rate=0.1, overload_threshold=0.5)
    res = await engine.apply_trust_decay(run=run, session=db_session, tick_index=1, scenario=run._scenario_raw,
                                         debt_snapshot={(d.pid, c.pid, eqs["A"].code): Decimal("60")})
    assert (res.updated_count, await _limit(db_session, eqs["A"], c, d)) == (1, Decimal("90.29"))


# ------------------------------------------------------------------ F-028-32: the payment planner


@pytest.mark.parametrize("precision", [0, 1, 2, 8])
def test_the_planner_picks_amounts_in_the_equivalent_step(precision) -> None:
    """Base: always cents - `x.yy` at precision 0 and 1, which the payment door now refuses."""

    planner = RealPaymentPlanner(actions_per_tick_max=1, amount_cap_limit=Decimal("57.555555555"), action_factory=lambda *a: a,
                                 logger=logging.getLogger("p028"))
    rng, step = random.Random(28032), Decimal(1).scaleb(-precision)
    amounts = [planner.pick_amount(rng, Decimal("100"), precision=precision) for _ in range(200)]
    picked = [Decimal(a) for a in amounts if a is not None]
    assert picked and all(a % step == 0 and 0 < a <= Decimal("57.555555555") for a in picked), picked[:5]
