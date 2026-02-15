import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.invariants import InvariantChecker
from app.core.simulator.models import EdgeClearingHistory, RunRecord
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine


@pytest.mark.asyncio
async def test_trust_drift_decay_never_shrinks_below_used_debt(db_session):
    # Scenario: debt is close to the limit; decay would normally shrink limit below debt,
    # which would create a TRUST_LIMIT_VIOLATION without any new payment.
    nonce = uuid.uuid4().hex[:10]
    eq = Equivalent(
        code=("D" + nonce[:15]).upper(),
        symbol="D",
        description=None,
        precision=2,
        metadata_={},
        is_active=True,
    )
    creditor = Participant(
        pid="C" + nonce,
        display_name="C",
        public_key="pkC-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    debtor = Participant(
        pid="B" + nonce,
        display_name="B",
        public_key="pkB-" + nonce,
        type="person",
        status="active",
        profile={},
    )
    db_session.add_all([eq, creditor, debtor])
    await db_session.flush()

    # Debt(debtor->creditor) is controlled by TrustLine(creditor->debtor).
    db_session.add(
        TrustLine(
            from_participant_id=creditor.id,
            to_participant_id=debtor.id,
            equivalent_id=eq.id,
            limit=Decimal("100.00"),
            status="active",
        )
    )
    db_session.add(
        Debt(
            debtor_id=debtor.id,
            creditor_id=creditor.id,
            equivalent_id=eq.id,
            amount=Decimal("99.00000001"),
        )
    )
    await db_session.commit()

    run = RunRecord(
        run_id="run-" + nonce,
        scenario_id="scenario-" + nonce,
        mode="real",
        state="running",
    )
    run._real_participants = [(creditor.id, creditor.pid), (debtor.id, debtor.pid)]

    eq_code = eq.code
    hist_key = f"{creditor.pid}:{debtor.pid}:{eq_code}"
    run._edge_clearing_history[hist_key] = EdgeClearingHistory(original_limit=Decimal("100.00"))

    scenario = {
        "settings": {
            "trust_drift": {
                "enabled": True,
                "decay_rate": 0.02,
                "min_limit_ratio": 0.3,
                "overload_threshold": 0.8,
            }
        },
        "trustlines": [
            {
                "equivalent": eq_code,
                "from": creditor.pid,
                "to": debtor.pid,
                "status": "active",
                "limit": 100.0,
            }
        ],
    }

    class _NoopSse:
        pass

    engine = TrustDriftEngine(
        sse=_NoopSse(),
        utc_now=lambda: None,
        logger=__import__("logging").getLogger("test"),
        get_scenario_raw=lambda _sid: scenario,
    )
    engine.init_trust_drift(run, scenario)

    debt_snapshot = {(debtor.pid, creditor.pid, eq_code): Decimal("99.00000001")}

    res = await engine.apply_trust_decay(
        run=run,
        session=db_session,
        tick_index=123,
        debt_snapshot=debt_snapshot,
        scenario=scenario,
    )

    assert res.updated_count >= 1

    # Ensure the DB trustline was not shrunk below debt.
    db_limit = (
        await db_session.execute(
            select(TrustLine.limit).where(
                TrustLine.from_participant_id == creditor.id,
                TrustLine.to_participant_id == debtor.id,
                TrustLine.equivalent_id == eq.id,
            )
        )
    ).scalar_one()

    # The clamp rounds the debt floor up to 0.01.
    assert Decimal(str(db_limit)) >= Decimal("99.01")

    checker = InvariantChecker(db_session)
    assert await checker.check_trust_limits(equivalent_id=eq.id) == []
