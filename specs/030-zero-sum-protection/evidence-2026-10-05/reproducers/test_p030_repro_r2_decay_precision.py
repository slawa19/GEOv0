"""Reproducer R-2 (p030 external review): float precision loss in trust decay.

`app/core/simulator/trust_drift_engine.py:512` computes `Decimal(str(1 - cfg.decay_rate))`: with
`decay_rate = 0.07` the float `1 - 0.07` is `0.9299999999999999`, so `100.00 * mult` rounds DOWN to `92.99`.
The correct decayed limit is `93.00`. The stand mirrors
`tests/unit/test_trust_drift_decay_does_not_break_trust_limits.py`, at precision 2, debt 90.00, default thresholds.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.simulator.models import EdgeClearingHistory, RunRecord
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup


@pytest.mark.asyncio
async def test_r2_decay_by_seven_percent_of_100_is_93(db_session):
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(code=("R" + nonce[:15]).upper(), symbol="R", description=None, precision=2, metadata_={}, is_active=True)
    creditor = Participant(pid="C" + nonce, display_name="C", public_key="pkC-" + nonce, type="person", status="active", profile={})
    debtor = Participant(pid="B" + nonce, display_name="B", public_key="pkB-" + nonce, type="person", status="active", profile={})
    db_session.add_all([eq, creditor, debtor])
    await db_session.flush()
    db_session.add(
        TrustLine(
            from_participant_id=creditor.id,
            to_participant_id=debtor.id,
            equivalent_id=eq.id,
            limit=Decimal("100.00"),
            status="active",
        )
    )
    async with debt_fixture_setup(db_session, label="r2-setup"):
        db_session.add(Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal("90.00")))
    await db_session.commit()

    run = RunRecord(run_id="run-" + nonce, scenario_id="scenario-" + nonce, mode="real", state="running")
    run._real_participants = [(creditor.id, creditor.pid), (debtor.id, debtor.pid)]
    eq_code = eq.code
    run._edge_clearing_history[f"{creditor.pid}:{debtor.pid}:{eq_code}"] = EdgeClearingHistory(
        original_limit=Decimal("100.00")
    )
    scenario = {
        "settings": {"trust_drift": {"enabled": True, "decay_rate": 0.07}},  # other thresholds: defaults
        "trustlines": [
            {"equivalent": eq_code, "from": creditor.pid, "to": debtor.pid, "status": "active", "limit": "100.00"}
        ],
    }

    class _NoopSse:
        pass

    engine = TrustDriftEngine(
        sse=_NoopSse(), utc_now=lambda: None, logger=logging.getLogger("test"), get_scenario_raw=lambda _sid: scenario
    )
    engine.init_trust_drift(run, scenario)
    cfg = run._trust_drift_config
    assert cfg.enabled and cfg.decay_rate == 0.07, f"stand: config not parsed: {cfg}"
    assert (cfg.overload_threshold, cfg.min_limit_ratio) == (0.8, 0.3), f"stand: thresholds not default: {cfg}"

    res = await engine.apply_trust_decay(
        run=run,
        session=db_session,
        tick_index=1,
        debt_snapshot={(debtor.pid, creditor.pid, eq_code): Decimal("90.00")},
        scenario=scenario,
    )
    assert res.updated_count == 1, f"stand: the decay did not reach the write: {res}"

    db_limit = (
        await db_session.execute(
            select(TrustLine.limit).where(
                TrustLine.from_participant_id == creditor.id,
                TrustLine.to_participant_id == debtor.id,
                TrustLine.equivalent_id == eq.id,
            )
        )
    ).scalar_one()
    written = res.committed_limit_updates[0].new_limit
    assert Decimal(str(db_limit)) == Decimal("93.00"), (
        f"decayed limit: db={db_limit} reported={written} mult={Decimal(str(1 - cfg.decay_rate))}"
    )
