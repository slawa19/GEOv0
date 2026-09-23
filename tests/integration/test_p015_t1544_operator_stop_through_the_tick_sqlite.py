"""T1544 through the real-mode TICK: an operator-stop refusal is a rejection, not an error of the run.

WHAT WAS WRONG. A deactivated equivalent refused the inject and the tick's clearing correctly, but
the tick lifecycle treated both refusals as programmatic failures. The inject owner re-raised the
refusal and left the event pending, so every tick failed on it, `_real_consec_tick_failures` grew and
the run was stopped with `REAL_MODE_TICK_FAILED_REPEATED` after the limit (3 by default). The clearing
phase spent `errors_total` and set `CLEARING_ERROR` on every clearing tick. A stop in ONE equivalent
must not stop a simulation that serves others.

THE RULE is the one the payments phase already applies to a refused payment
(`real_payments_executor.py`: a 4xx `GeoException` is REJECTED, its savepoint rolled back, counted,
and the tick continues). This module drives `RealRunner.tick_real_mode` itself - not the phase
functions - for more ticks than the consecutive-failure limit, and each test asserts that the refusal
really happened, so a path that never ran cannot pass.

The stand: a mode-B clone of the migrated PostgreSQL template (`committed_database`, dropped after the
test), reached through a pooled engine configured like the application's
(`tests/simulator_tick_stand.py`) and installed as `AsyncSessionLocal` so the tick's own sessions use
it. Mode B and not the savepoint-wrapped `db_session`: the tick opens and commits sessions of its own,
and its clearing refuses a connection-bound session, so the stand needs commits that are real. The
heartbeat that advances `tick_index` is not part of `tick_real_mode`, so the loop below advances it.

MOVED OFF SQLITE (017 stage 3, slice S2a). Until then the stand was a file-backed WAL SQLite database
from `tests/scratch_db`. The rule this module holds is not about SQLite, and the stand would have died
with the SQLite driver taking the module's coverage with it, without a single red test. The asserts
are unchanged; only the stand moved. The file name keeps its old suffix so the references to it in
`specs/` stay true.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.core.payments.engine import PaymentEngine
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.utils.exceptions import ConflictException
from tests.debt_setup import debt_fixture_setup
from tests.simulator_tick_stand import RecordingSse as _Sse
from tests.simulator_tick_stand import install_tick_stand as _install
from tests.simulator_tick_stand import pooled_sessionmaker_over


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def factory(committed_database):
    """Real commits on a disposable clone, visible to every session the tick opens."""
    async with pooled_sessionmaker_over(committed_database.url) as session_factory:
        yield session_factory


class _Artifacts:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def write_real_tick_artifact(self, *a, **kw) -> None:
        return None

    def enqueue_event_artifact(self, _run_id: str, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


def _runner(run, scenario, *, actions: int, clearing_every: int, artifacts: _Artifacts) -> RealRunner:
    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _rid: run,
        get_scenario_raw=lambda _sid: scenario,
        sse=_Sse(),
        artifacts=artifacts,
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: True,
        actions_per_tick_max=actions,
        clearing_every_n_ticks=clearing_every,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logging.getLogger("tests.t1544.tick"),
    )
    runner._real_enable_inject = True
    return runner


def _run(run_id: str, people: list[Participant], code: str, *, intensity: int) -> RunRecord:
    run = RunRecord(run_id=run_id, scenario_id="t1544-tick", mode="real", state="running")
    run.seed = 7
    run.tick_index = 0
    run.sim_time_ms = 1_000
    run.intensity_percent = intensity
    run._real_seeded = True
    run._real_participants = [(p.id, p.pid) for p in people]
    run._real_equivalents = [code]
    run._real_viz_by_eq = {}
    run._edges_by_equivalent = {}
    return run


async def _seed(factory, roles: list[str]) -> tuple[Equivalent, list[Participant]]:
    n = uuid.uuid4().hex[:8].upper()
    async with factory() as s:
        eq = Equivalent(code=f"T44K{n}"[:16], precision=2, is_active=True, metadata_={})
        people = [
            Participant(
                pid=f"T44_{role}_{n}", display_name=role, public_key=f"pk_t44_{role}_{n}",
                type="person", status="active", profile={},
            )
            for role in roles
        ]
        s.add_all([eq, *people])
        await s.commit()
    return eq, people


async def _trust(factory, eq: Equivalent, creditor: Participant, debtor: Participant, limit: str) -> None:
    async with factory() as s:
        s.add(
            TrustLine(
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=eq.id,
                limit=Decimal(limit),
                policy={"auto_clearing": True},
                status="active",
            )
        )
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)


async def _deactivate(factory, eq: Equivalent) -> None:
    async with factory() as s:
        await s.execute(update(Equivalent).where(Equivalent.id == eq.id).values(is_active=False))
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)


async def _debts(factory, eq: Equivalent) -> list[Decimal]:
    async with factory() as s:
        rows = (await s.execute(select(Debt.amount).where(Debt.equivalent_id == eq.id))).scalars().all()
    return sorted(Decimal(str(a)) for a in rows)


async def _ticks(runner: RealRunner, run: RunRecord, count: int) -> None:
    for _ in range(count):
        run.tick_index += 1
        run.sim_time_ms += 1_000
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=60)


def _assert_the_run_was_not_charged(run: RunRecord) -> None:
    assert run.state == "running", (run.state, run.last_error)
    assert run.errors_total == 0, run.last_error
    assert run._real_consec_tick_failures == 0
    assert run.last_error is None, run.last_error


def _messages(caplog, marker: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if marker in r.getMessage()]


@pytest.mark.asyncio
async def test_a_refused_inject_is_consumed_and_does_not_fail_the_run(factory, monkeypatch, caplog) -> None:
    """RED before the rejection rule: the refused event stayed pending and failed every tick."""
    eq, (creditor, debtor) = await _seed(factory, ["C", "D"])
    await _trust(factory, eq, creditor, debtor, "100.00")
    await _deactivate(factory, eq)
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": creditor.pid}, {"id": debtor.pid}],
        "trustlines": [
            {"from": creditor.pid, "to": debtor.pid, "equivalent": eq.code, "limit": "100.00",
             "status": "active"}
        ],
        "behaviorProfiles": [],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {"op": "inject_debt", "from": creditor.pid, "to": debtor.pid,
                     "equivalent": eq.code, "amount": "5.00"}
                ],
            }
        ],
    }
    artifacts = _Artifacts()
    run = _run(f"t1544-inject-{uuid.uuid4().hex[:6]}", [creditor, debtor], eq.code, intensity=0)
    runner = _runner(run, scenario, actions=1, clearing_every=10_000, artifacts=artifacts)
    _install(monkeypatch, factory)
    limit = int(runner._real_max_consec_tick_failures_limit)
    assert limit >= 1, f"premise: the consecutive-failure limit is disabled ({limit})"

    with caplog.at_level(logging.WARNING):
        await _ticks(runner, run, limit + 2)

    # The property first, so a regression reports the run it damaged; the premise right after.
    _assert_the_run_was_not_charged(run)
    refusals = _messages(caplog, "simulator.real.inject.refused_equivalent_inactive")
    assert len(refusals) == 1, (
        f"premise and consumption: expected the refusal exactly once over {limit + 2} ticks, got {refusals}"
    )
    notes = [
        p for p in artifacts.payloads
        if p.get("type") == "note"
        and (p.get("scenario") or {}).get("description") == "inject refused (equivalent inactive)"
    ]
    assert len(notes) == 1, artifacts.payloads
    assert 0 in run._real_fired_scenario_event_indexes
    _assert_the_run_was_not_charged(run)
    assert await _debts(factory, eq) == []
    async with factory() as s:
        envelopes = await s.scalar(text("SELECT count(*) FROM debt_operations"))
    assert envelopes == 0


@pytest.mark.asyncio
async def test_a_refused_tick_clearing_does_not_spend_the_error_budget(factory, monkeypatch, caplog) -> None:
    """RED before the rejection rule: every clearing tick added to `errors_total` as CLEARING_ERROR."""
    eq, people = await _seed(factory, ["A", "B", "C"])
    a, b, c = people
    ring = [(a, b), (b, c), (c, a)]  # (debtor, creditor)
    for debtor, creditor in ring:
        await _trust(factory, eq, creditor, debtor, "1000.00")
    ring_debts = [
        Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=eq.id, amount=Decimal("10.00"))
        for debtor, creditor in ring
    ]
    async with factory() as s:
        # Values built above: a fixture block may hold only model construction and `add`/`flush`.
        async with debt_fixture_setup(s, label="setup"):
            s.add_all(ring_debts)
        await s.commit()
    await _deactivate(factory, eq)
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": p.pid} for p in people],
        "trustlines": [
            {"from": creditor.pid, "to": debtor.pid, "equivalent": eq.code, "limit": "1000.00",
             "status": "active"}
            for debtor, creditor in ring
        ],
        "behaviorProfiles": [],
    }
    run = _run(f"t1544-clearing-{uuid.uuid4().hex[:6]}", people, eq.code, intensity=0)
    runner = _runner(run, scenario, actions=1, clearing_every=1, artifacts=_Artifacts())
    _install(monkeypatch, factory)
    assert runner._real_tick_clearing_coordinator._clearing_policy == "static", (
        "premise: the stand needs the static clearing policy, which clears on every tick here"
    )
    ticks = int(runner._real_max_consec_tick_failures_limit) + 2

    with caplog.at_level(logging.INFO):
        await _ticks(runner, run, ticks)

    # The property first, so a regression reports the run it damaged; the premise right after.
    _assert_the_run_was_not_charged(run)
    assert run.current_phase is None, (
        f"the refused clearing left the published phase at {run.current_phase!r}"
    )
    refusals = _messages(caplog, "simulator.real.clearing_refused_equivalent_inactive")
    assert len(refusals) == ticks, (
        f"premise: expected the clearing refusal on each of {ticks} ticks, got {refusals}"
    )
    assert all("exc=ConflictException" in m for m in refusals), refusals
    _assert_the_run_was_not_charged(run)
    assert await _debts(factory, eq) == [Decimal("10.00")] * 3


@pytest.mark.asyncio
async def test_a_refused_staged_payment_is_rejected_and_the_tick_continues(
    factory, monkeypatch, caplog
) -> None:
    """The payments phase already had the rule; this proves it for the operator stop, by running it."""
    eq, (sender, receiver) = await _seed(factory, ["S", "R"])
    await _trust(factory, eq, receiver, sender, "1000.00")  # receiver extends credit to sender
    await _deactivate(factory, eq)
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": sender.pid}, {"id": receiver.pid}],
        "trustlines": [
            {"from": receiver.pid, "to": sender.pid, "equivalent": eq.code, "limit": "1000.00",
             "status": "active"}
        ],
        "behaviorProfiles": [],
    }
    run = _run(f"t1544-payment-{uuid.uuid4().hex[:6]}", [sender, receiver], eq.code, intensity=100)
    runner = _runner(run, scenario, actions=1, clearing_every=10_000, artifacts=_Artifacts())
    _install(monkeypatch, factory)

    outcomes: list[str] = []
    original = PaymentService.create_payment_internal_staged

    async def _recording(self, *args, **kwargs):
        try:
            staged = await original(self, *args, **kwargs)
        except ConflictException as exc:
            outcomes.append(f"refused:{(exc.details or {}).get('reason')}")
            raise
        outcomes.append(f"result:{staged.result.status}")
        return staged

    monkeypatch.setattr(PaymentService, "create_payment_internal_staged", _recording)
    ticks = int(runner._real_max_consec_tick_failures_limit) + 1

    with caplog.at_level(logging.WARNING):
        await _ticks(runner, run, ticks)

    refused = [o for o in outcomes if o == f"refused:{PaymentEngine.EQUIVALENT_INACTIVE_REASON}"]
    assert refused, f"premise: no staged payment was refused by the operator stop: {outcomes}"
    assert refused == outcomes, outcomes
    assert run.rejected_total == len(refused), (run.rejected_total, outcomes)
    assert run.committed_total == 0
    _assert_the_run_was_not_charged(run)
    assert await _debts(factory, eq) == []
    async with factory() as s:
        assert await s.scalar(select(func.count(Transaction.id))) == 0
