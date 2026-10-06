"""028 F-028-30/31/32, pulled into E2 (orchestrator 2026-10-04): the simulator's own writers in the equivalent's step.

E2 made every door refuse an amount finer than `10**-precision` (owner В-4). The simulator wrote such amounts itself:
the inject truncated a debt to 0.01 (that effect, `inject_debt`, is gone - 030 S3b; an inject line limit is
refused in the step by the line door), trust drift grew and decayed limits at the storage grain 1E-8, and the payment
planner always picked cents. Each test names what the base did. Mode A, except where it says otherwise.
"""

from __future__ import annotations

import logging
import random
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.real_payment_planner import RealPaymentPlanner
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.unit.test_scenario_inject_topology import _make_run, _make_runner


async def _world(db_session, precisions: dict[str, int], limit: str = "100", *, with_lines: bool = True):
    n = uuid.uuid4().hex[:6].upper()
    eqs = {name: Equivalent(code=f"S{name}{n}", precision=p, is_active=True) for name, p in precisions.items()}
    c = Participant(pid=f"C_{n}", display_name="C", public_key=f"pk_c_{n}", type="person", status="active")
    d = Participant(pid=f"D_{n}", display_name="D", public_key=f"pk_d_{n}", type="person", status="active")
    db_session.add_all([*eqs.values(), c, d])
    await db_session.flush()
    for eq in eqs.values() if with_lines else ():
        db_session.add(TrustLine(from_participant_id=c.id, to_participant_id=d.id, equivalent_id=eq.id,
                                 limit=Decimal(limit), status="active", policy={}))
    await db_session.commit()
    return eqs, c, d


async def _limit(db_session, eq, c, d) -> Decimal:
    return (await db_session.execute(select(TrustLine.limit).where(
        TrustLine.equivalent_id == eq.id, TrustLine.from_participant_id == c.id))).scalar_one()


async def _lines(db_session, eq, c, d) -> list[Decimal]:
    """The limits of every line c -> d of the equivalent (a `create_trustline` inject that landed leaves one)."""
    return list((await db_session.execute(select(TrustLine.limit).where(
        TrustLine.equivalent_id == eq.id, TrustLine.from_participant_id == c.id,
        TrustLine.to_participant_id == d.id))).scalars().all())


async def _inject(db_session, eqs, c, d, effects):
    """Run one inject event. An effect is a dict (used as is) or a legacy `(eq key, amount)` pair, which is an
    `inject_debt` effect: the op no longer exists, so such an effect is skipped (kept for the importer
    `test_p028_e4_inject_criterion_b_postgres.py`)."""
    runner, artifacts = _make_runner(inject_enabled=True)
    event = {"type": "inject", "time": 0, "effects": [
        e if isinstance(e, dict) else
        {"op": "inject_debt", "from": c.pid, "to": d.pid, "equivalent": eqs[e[0]].code, "amount": e[1]}
        for e in effects]}
    scenario = {"participants": [{"id": c.pid}, {"id": d.pid}], "events": [event], "trustlines": []}
    await runner._apply_due_scenario_events(db_session, run_id="r-028-inject", scenario=scenario, run=_make_run(
        participants=[(c.id, c.pid), (d.id, d.pid)], equivalents=[eq.code for eq in eqs.values()]))
    [note] = [p for p in artifacts.payloads if p.get("type") == "note"]
    return note["scenario"]["stats"]


# ------------------------------------------------------------------ F-028-30: inject


# At precision 8 the step IS the column's grain, so a value finer than it never reaches the step door: the money door
# refuses it as unstorable, under its own reason.
@pytest.mark.parametrize("precision,limit,in_step,reason", [
    (2, "1.239", "1.24", "amount_precision_exceeded"), (8, "1.000000001", "1.00000001", "money_quantization")])
@pytest.mark.asyncio
async def test_an_inject_line_limit_finer_than_the_step_is_skipped_with_a_reason_not_truncated(
        db_session, precision, limit, in_step, reason) -> None:
    """The inject writes lines only (030 S3b: `inject_debt` is gone). A limit finer than the equivalent's step is
    skipped with its reason in the note and no line is written - never truncated to `1.23` - and the same
    effect with a limit in the step lands whole (control: the skip is the step, not a refusal of the effect)."""

    def line(lim):
        return {"op": "create_trustline", "from": c.pid, "to": d.pid, "equivalent": eqs["A"].code, "limit": lim}

    eqs, c, d = await _world(db_session, {"A": precision}, with_lines=False)  # the inject creates the line under test
    stats = await _inject(db_session, eqs, c, d, [line(limit)])
    assert await _lines(db_session, eqs["A"], c, d) == []
    assert stats["applied"] == 0 and stats["skipped"] == 1, stats
    assert stats["skipped_reasons"] == {reason: 1}, stats

    stats = await _inject(db_session, eqs, c, d, [line(in_step)])
    assert stats["applied"] == 1 and stats["skipped"] == 0, stats
    assert await _lines(db_session, eqs["A"], c, d) == [Decimal(in_step)]


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
                                    eq_code=eqs["A"].code, tick_index=1)
    assert await _limit(db_session, eqs["A"], c, d) == Decimal(expected)


@pytest.mark.asyncio
async def test_trust_decay_is_in_the_step(db_session) -> None:
    """Base: `100.33 * 0.9 = 90.297` at precision 2 was refused by the step door and the decay rolled back."""

    eqs, c, d = await _world(db_session, {"A": 2}, limit="100.33")
    run, engine = _drift(eqs, c, d, limit="100.33", original="100.33", decay_rate=0.1, overload_threshold=0.5)
    async with debt_fixture_setup(db_session, label="p028-e2"):  # 029 `T2993`: the decay reads the debt row
        db_session.add(Debt(debtor_id=d.id, creditor_id=c.id, equivalent_id=eqs["A"].id, amount=Decimal("60")))
    await db_session.commit()
    res = await engine.apply_trust_decay(run=run, session=db_session, tick_index=1, scenario=run._scenario_raw)
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
