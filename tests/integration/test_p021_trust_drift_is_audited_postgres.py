"""R-021-3 (programme 021, stage 1, `T2102`): trust drift, driven by a REAL tick, is audited.

WHAT IS WRONG (on `87c7200`). Growth and decay rewrite `trust_lines.limit` with a Core `UPDATE`
(`trust_drift_engine.py:324`, `:531`): no `TRUST_LINE_UPDATE` row and no integrity checkpoint.

THE TARGET (spec, "Решения" items 4, 5 and 9). Both go through `TrustLineService`'s internal path in their own
transactions: one `TRUST_LINE_UPDATE` row per changed line, labelled as belonging to the caller's transaction,
the rows of one transaction sharing ONE before/after checksum pair, and exactly two checkpoint computations per
touched equivalent per transaction (counted on the service's own binding, so the clearing's checkpoints are not
in the count).

WHAT MUST NOT MOVE, asserted as controls (green before and after): drift changes only the ACTIVE line (a frozen
line with the same overload keeps its limit), the decay floor is the debt read in the decay's own transaction
(a line cannot be decayed below it), growth only raises.

The stand is a real `RealRunnerImpl.tick_real_mode` on a mode-B clone (`tests/simulator_tick_stand.py`), because
both drift writers run inside the tick's own sessions and commits.
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
from sqlalchemy import select

from app.core.payments.router import PaymentRouter
from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner_impl import RealRunnerImpl
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from tests.debt_setup import debt_fixture_setup
from tests.p021_support import (
    TrustLineBatchPoints,
    is_transaction_scoped,
    require_target,
    trust_line_audit_rows,
)
from tests.simulator_tick_stand import RecordingSse, install_tick_stand, pooled_sessionmaker_over


@pytest_asyncio.fixture
async def factory(committed_database):
    async with pooled_sessionmaker_over(committed_database.url) as session_factory:
        yield session_factory


class _Artifacts:
    def write_real_tick_artifact(self, *a, **kw) -> None:
        return None

    def enqueue_event_artifact(self, _run_id: str, _payload: dict[str, Any]) -> None:
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def runner_for(run: RunRecord, scenario: dict, *, clearing_every: int, logger: logging.Logger | None = None) -> RealRunnerImpl:
    return RealRunnerImpl(
        lock=threading.RLock(),
        get_run=lambda _rid: run,
        get_scenario_raw=lambda _sid: scenario,
        sse=RecordingSse(),
        artifacts=_Artifacts(),
        utc_now=_utc_now,
        publish_run_status=lambda _rid: None,
        db_enabled=lambda: True,
        actions_per_tick_max=1,
        clearing_every_n_ticks=clearing_every,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=10,
        real_max_errors_total_default=50,
        logger=logger or logging.getLogger("tests.p021.drift"),
    )


def run_for(people: list[Participant], code: str) -> RunRecord:
    run = RunRecord(run_id=f"p021-drift-{uuid.uuid4().hex[:6]}", scenario_id="p021-drift", mode="real", state="running")
    run.seed = 7
    run.tick_index = 0
    run.sim_time_ms = 1_000
    run.intensity_percent = 0
    run._real_seeded = True
    run._real_participants = [(p.id, p.pid) for p in people]
    run._real_equivalents = [code]
    run._real_viz_by_eq = {}
    run._edges_by_equivalent = {}
    return run


async def world(factory, roles: list[str], lines: list[tuple[str, str, str, str]], debts: list[tuple[str, str, str]]):
    """`lines`: (creditor, debtor, limit, status); `debts`: (debtor, creditor, amount)."""

    n = uuid.uuid4().hex[:8].upper()
    async with factory() as s:
        eq = Equivalent(code=f"P21D{n}"[:16], precision=2, is_active=True, metadata_={})
        people = {
            r: Participant(pid=f"P21_{r}_{n}", display_name=r, public_key=f"pk_p21_{r}_{n}", type="person",
                           status="active", profile={})
            for r in roles
        }
        s.add_all([eq, *people.values()])
        await s.flush()
        for creditor, debtor, limit, status in lines:
            s.add(TrustLine(from_participant_id=people[creditor].id, to_participant_id=people[debtor].id,
                            equivalent_id=eq.id, limit=Decimal(limit), policy={"auto_clearing": True},
                            status=status))
        await s.flush()
        rows = [
            Debt(debtor_id=people[d].id, creditor_id=people[c].id, equivalent_id=eq.id, amount=Decimal(a))
            for d, c, a in debts
        ]
        async with debt_fixture_setup(s, label="setup"):
            s.add_all(rows)
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, people


def scenario_for(eq: Equivalent, people: dict, lines, trust_drift: dict) -> dict:
    return {
        "equivalents": [eq.code],
        "participants": [{"id": p.pid} for p in people.values()],
        # The SCENARIO says every line is active: a frozen line in the database is what an inject leaves
        # behind (`inject_executor.py:939`), and drift must still not touch it.
        "trustlines": [
            {"from": people[c].pid, "to": people[d].pid, "equivalent": eq.code, "limit": limit, "status": "active"}
            for c, d, limit, _status in lines
        ],
        "behaviorProfiles": [],
        "settings": {"trust_drift": trust_drift},
    }


async def limits(factory, eq: Equivalent, people: dict) -> dict[tuple[str, str], tuple[Decimal, str]]:
    names = {p.id: r for r, p in people.items()}
    async with factory() as s:
        rows = (await s.execute(select(TrustLine).where(TrustLine.equivalent_id == eq.id))).scalars().all()
    return {(names[r.from_participant_id], names[r.to_participant_id]): (Decimal(str(r.limit)), str(r.status)) for r in rows}


async def ticks(runner: RealRunnerImpl, run: RunRecord, count: int) -> None:
    for _ in range(count):
        run.tick_index += 1
        run.sim_time_ms += 1_000
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=60)


def one_pair(rows) -> bool:
    # 024 `T2413.2`: no checkpoint in the transaction - the rows of one batch carry the empty pair and say so.
    return {(r.state_checksum_before, r.state_checksum_after, r.verification_passed) for r in rows} == {("", "", None)}


DECAY = {"enabled": True, "decay_rate": 0.02, "min_limit_ratio": 0.3, "overload_threshold": 0.8, "growth_rate": 0.05}
DECAY_LINES = [
    ("C", "D", "100.00", "active"),  # debt 90 -> ratio 0.9 -> 98.00
    ("C", "G", "100.00", "active"),  # debt 99 -> floor 99.00
    ("C", "E", "100.00", "frozen"),  # debt 90, same overload, frozen -> untouched
]
DECAY_DEBTS = [("D", "C", "90.00"), ("G", "C", "99.00"), ("E", "C", "90.00")]


@pytest.mark.asyncio
async def test_decay_in_a_real_tick_is_audited_per_transaction(factory, monkeypatch) -> None:
    eq, people = await world(factory, ["C", "D", "G", "E"], DECAY_LINES, DECAY_DEBTS)
    scenario = scenario_for(eq, people, DECAY_LINES, DECAY)
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=10_000)
    install_tick_stand(monkeypatch, factory)
    checkpoints = TrustLineBatchPoints(monkeypatch)

    await ticks(runner, run, 1)

    # ── controls: the decay ran, only on active lines, never below the debt ───────────────────
    assert run.state == "running" and run.errors_total == 0, (run.state, run.last_error)
    after = await limits(factory, eq, people)
    assert after[("C", "D")] == (Decimal("98.00"), "active"), after
    assert after[("C", "G")] == (Decimal("99.00"), "active"), after  # the debt floor
    assert after[("C", "E")] == (Decimal("100.00"), "frozen"), after  # drift touches the active line only

    async with factory() as s:
        rows = await trust_line_audit_rows(s, equivalent_codes=[eq.code], operation_type="TRUST_LINE_UPDATE")
    require_target(
        len(rows) == 2
        and all(is_transaction_scoped(r) for r in rows)
        and one_pair(rows)
        and sorted((r.affected_participants.get("from"), r.affected_participants.get("to")) for r in rows)
        == sorted([(people["C"].pid, people["D"].pid), (people["C"].pid, people["G"].pid)])
        and checkpoints.count == 2,
        f"decay tick: {len(rows)} TRUST_LINE_UPDATE rows, {checkpoints.count} trust-line checkpoints",
    )


GROWTH = {"enabled": True, "growth_rate": 0.05, "max_growth": 2.0, "decay_rate": 0.02, "overload_threshold": 0.8,
          "min_limit_ratio": 0.3}
RING_LINES = [("B", "A", "1000.00", "active"), ("C", "B", "1000.00", "active"), ("A", "C", "1000.00", "active")]
RING_DEBTS = [("A", "B", "10.00"), ("B", "C", "10.00"), ("C", "A", "10.00")]


@pytest.mark.asyncio
async def test_growth_after_a_real_tick_clearing_is_audited_per_transaction(factory, monkeypatch) -> None:
    eq, people = await world(factory, ["A", "B", "C"], RING_LINES, RING_DEBTS)
    scenario = scenario_for(eq, people, RING_LINES, GROWTH)
    run = run_for(list(people.values()), eq.code)
    runner = runner_for(run, scenario, clearing_every=1)
    install_tick_stand(monkeypatch, factory)
    checkpoints = TrustLineBatchPoints(monkeypatch)

    await ticks(runner, run, 1)

    # ── controls: the ring was cleared and growth raised all three lines once ─────────────────
    assert run.state == "running" and run.errors_total == 0, (run.state, run.last_error)
    async with factory() as s:
        debts = (await s.execute(select(Debt.amount).where(Debt.equivalent_id == eq.id))).scalars().all()
    assert all(Decimal(str(a)) == 0 for a in debts), debts
    after = await limits(factory, eq, people)
    assert {k: v for k, v in after.items()} == {
        ("B", "A"): (Decimal("1050.00"), "active"),
        ("C", "B"): (Decimal("1050.00"), "active"),
        ("A", "C"): (Decimal("1050.00"), "active"),
    }, after

    async with factory() as s:
        rows = await trust_line_audit_rows(s, equivalent_codes=[eq.code], operation_type="TRUST_LINE_UPDATE")
    require_target(
        len(rows) == 3 and all(is_transaction_scoped(r) for r in rows) and one_pair(rows) and checkpoints.count == 2,
        f"growth tick: {len(rows)} TRUST_LINE_UPDATE rows, {checkpoints.count} trust-line checkpoints",
    )
