"""Programme 021, stage 1 (`T2102`, T2100 P2-4): the seeding and growth owners roll back a failed batch.

R-021-4 holds the decay's owner in a real tick. The same rule has two more owners in stage 1:

* growth - `TrustDriftEngine.apply_trust_growth` commits its own transaction, so it is the one that rolls back;
* the HTTP seeder - `_ensure_run_seeded` (`app/api/v1/simulator.py`) commits the seeding on the REQUEST's
  session, and translates a failure into a 503.

Each test fails the batch AFTER an earlier mutation of the same transaction (the after-mutation checkpoint of
the trust-line service, a non-database exception), then COMMITS the same session - what any later work on it
would do - and reads what became durable. Mode A: the fixture's savepoint stands in for the database, and the
later commit is a savepoint release, which is enough to show what the session still carried.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
from app.core.simulator.trust_drift_engine import TrustDriftEngine
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.p021_support import TrustLineBatchPoints, trust_line_audit_rows


@pytest.mark.asyncio
async def test_a_growth_failing_after_its_mutation_is_rolled_back_by_the_engine(db_session, monkeypatch) -> None:
    n = uuid.uuid4().hex[:6].upper()
    eq = Equivalent(code=f"P21G{n}", precision=2, is_active=True, metadata_={})
    a, b = (
        Participant(pid=f"P21G_{r}_{n}", display_name=r, public_key=f"pk_{r}_{n}", type="person",
                    status="active", profile={})
        for r in ("A", "B")
    )
    db_session.add_all([eq, a, b])
    await db_session.flush()
    line = TrustLine(from_participant_id=a.id, to_participant_id=b.id, equivalent_id=eq.id,
                     limit=Decimal("100.00"), status="active", policy={})
    db_session.add(line)
    await db_session.commit()
    line_id, eq_code, a_pid, b_pid = line.id, eq.code, a.pid, b.pid

    run = RunRecord(run_id=f"p021-g-{n}", scenario_id="p021-g", mode="real", state="running")
    run._real_participants = [(a.id, a_pid), (b.id, b_pid)]
    run._trust_drift_config = TrustDriftConfig(enabled=True, growth_rate=0.05, max_growth=2.0)
    run._edge_clearing_history = {f"{a_pid}:{b_pid}:{eq_code}": EdgeClearingHistory(original_limit=Decimal("100"))}
    run._scenario_raw = {"trustlines": []}
    engine = TrustDriftEngine(sse=None, utc_now=None, logger=logging.getLogger("tests.p021.growth"),
                              get_scenario_raw=lambda _s: run._scenario_raw)
    checkpoints = TrustLineBatchPoints(monkeypatch)
    checkpoints.fail_on_call = 2  # one equivalent: call 2 is the after-mutation checkpoint

    with pytest.raises(RuntimeError, match="forced trust-line batch failure"):
        await engine.apply_trust_growth(run, db_session, {(a_pid, b_pid)}, eq_code, 1)
    assert checkpoints.count == 2, "premise: the failure point was reached"

    await db_session.commit()  # a later commit on the same session
    limit = await db_session.scalar(select(TrustLine.limit).where(TrustLine.id == line_id))
    assert Decimal(str(limit)) == Decimal("100.00"), f"the failed growth's limit became durable: {limit}"
    assert await trust_line_audit_rows(db_session, equivalent_codes=[eq_code]) == []


@pytest.mark.asyncio
async def test_a_failed_http_seeding_is_rolled_back_before_its_503(db_session, monkeypatch) -> None:
    import app.api.v1.simulator as simulator_module

    n = uuid.uuid4().hex[:6].upper()
    eq_code = f"P21H{n}"
    pids = [f"P21H_A_{n}", f"P21H_B_{n}"]
    scenario = {
        "equivalents": [eq_code],
        "participants": [{"id": p} for p in pids],
        "trustlines": [{"from": pids[0], "to": pids[1], "equivalent": eq_code, "limit": "10"}],
    }
    run = SimpleNamespace(run_id=f"p021-h-{n}", scenario_id="p021-h", _scenario_raw=scenario, _real_seeded=False,
                          _real_seeding_lock=None)
    monkeypatch.setattr(simulator_module.runtime, "get_run", lambda _rid: run)
    checkpoints = TrustLineBatchPoints(monkeypatch)
    checkpoints.fail_on_call = 2  # one equivalent: call 2 is the after-mutation checkpoint

    response = await simulator_module._ensure_run_seeded(run.run_id, db_session)

    assert response is not None and response.status_code == 503, response
    assert checkpoints.count == 2, "premise: the failure point was reached"
    assert run._real_seeded is False

    await db_session.commit()  # a later commit on the request's session
    left = (await db_session.execute(select(Participant.pid).where(Participant.pid.in_(pids)))).scalars().all()
    eqs = (await db_session.execute(select(Equivalent.id).where(Equivalent.code == eq_code))).scalars().all()
    assert left == [] and eqs == [], f"the failed seeding left participants {left} and equivalents {eqs}"
    assert await trust_line_audit_rows(db_session, equivalent_codes=[eq_code]) == []
