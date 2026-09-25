"""Programme 019 stage 5, `T1908`: the EXPERIMENTS that gate the removal of the advisory locks (`T1909`).

NOT A GATE OF THE CURRENT CODE - a measurement that decides a fork (spec, Verification plan: "эксперимент").
Marked `slow`, so the default tier never runs it; re-run it with

    .\\scripts\\verify_local.ps1 -TaskSlug <slug> -BackendOnly -IncludeExpensive `
      -BackendSelector tests/integration/test_p019_t1908_lock_removal_experiments_postgres.py

Every schedule runs TWICE, on the code that ships: `locks_on` - as it is today - and `locks_off` - the
money-boundary lock primitives replaced by counted no-ops (`tests/p019_locks_off.py`, test-only, never app
code). What stays in both: SERIALIZABLE (refused otherwise since `T1907`), the row locks (`FOR SHARE` of
the stop/hold, `FOR UPDATE` of the cycle), and every retry owner (`PaymentService.pay`, the clearing's
`_run_attempts`, the inject's transient retry).

AGAINST A VACUOUS "NO LOSS". An experiment that finds no lost update proves something only if the
interleaving it claims really happened. So each `locks_off` schedule asserts its MECHANISM before its
result: the switch was on the path (`LocksOff.calls`), both writers reached the point that makes the race
(a barrier both passed, or a backend queued on the other's ROW/TRANSACTION lock, read from `pg_locks`),
and SSI actually intervened (`40001` counted by the retry owners - `conflicts > 0`). The invariants are then
checked TOGETHER, not as final debts alone: the serial result, one committed occurrence of each operation,
the envelopes, the audit, the trust limits and one direction per pair.

Every run of a schedule appends one JSON line to `p019_t1908_results.jsonl` under `GEO_TEST_ARTIFACT_ROOT`
(the canonical runner sets it to `.local-run/test-runs/<slug>/artifacts`) and prints it as `T1908-RESULT`;
the numbers recorded in the spec are read from that file, with the command and the date.

(c), the refusal of an unsuitable isolation: the per-boundary refusal is `T1907`'s
`tests/integration/test_p019_money_writers_refuse_non_serializable_postgres.py`; here the three debt writers
are refused again with the locks on and off, to show the refusal does not lean on them. (d), clearing
starvation, is the probe `tests/integration/test_p019_t1908_clearing_starvation_probe_postgres.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clearing.service import ClearingService
from app.core.payments import service as payment_service_module
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator import real_runner_impl
from app.db.journal_tables import debt_operations
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.integration.p019_interlock_support import _seed_interlock_case
from tests.p019_locks_off import blocked_by, switch_money_boundary_locks_off

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

pytestmark = [pytest.mark.slow]

MODES = ["locks_on", "locks_off"]


@pytest_asyncio.fixture
async def stand(committed_database):
    """A SERIALIZABLE engine over the clone with room for every concurrent writer and observer."""

    engine = create_async_engine(
        committed_database.url, pool_size=12, max_overflow=0, pool_timeout=20, isolation_level="SERIALIZABLE"
    )
    try:
        yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


@dataclass
class Conflicts:
    """Every transaction-level conflict a retry owner classified, by owner."""

    payment: list[str] = field(default_factory=list)
    clearing: list[str] = field(default_factory=list)
    inject: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.payment) + len(self.clearing) + len(self.inject)

    @property
    def serialization_failures(self) -> int:
        """Conflicts whose SQLSTATE was 40001 - SSI intervening - by any owner (019 `T1909`, 5a review P3).

        `total` counts every retried conflict, including a book `DebtVersionConflict`, a 40P01 or the
        debt-pair `23505`; an assertion that SSI intervened must count 40001 and nothing else."""
        return sum("40001" in entry.split(",") for entry in (*self.payment, *self.clearing, *self.inject))


def count_conflicts(monkeypatch) -> Conflicts:
    conflicts = Conflicts()

    original_retry = PaymentService._retry_or_none

    def retry_or_none(self, exc, **kwargs):
        conflicts.payment.append(payment_service_module._conflict_cause(exc))
        return original_retry(self, exc, **kwargs)

    monkeypatch.setattr(PaymentService, "_retry_or_none", retry_or_none)

    original_clearing = ClearingService._is_retryable_concurrency_error.__func__

    def clearing_retryable(cls, exc):
        retryable = original_clearing(cls, exc)
        if retryable:
            conflicts.clearing.append(",".join(sorted(cls._postgres_error_codes(exc) & {"40001", "40P01"})))
        return retryable

    monkeypatch.setattr(ClearingService, "_is_retryable_concurrency_error", classmethod(clearing_retryable))

    original_inject = real_runner_impl._is_transient_inject_db_error

    def inject_transient(exc):
        transient = original_inject(exc)
        if transient:
            orig = getattr(exc, "orig", None)
            conflicts.inject.append(str(getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)))
        return transient

    monkeypatch.setattr(real_runner_impl, "_is_transient_inject_db_error", inject_transient)
    return conflicts


def _report(name: str, **values) -> None:
    line = json.dumps({"schedule": name, **values}, default=str, sort_keys=True)
    print("T1908-RESULT " + line)
    root = Path(os.environ.get("GEO_TEST_ARTIFACT_ROOT") or ".local-run/test-runs/t1908/artifacts")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "p019_t1908_results.jsonl").open("a", encoding="utf-8") as out:
        out.write(line + "\n")


async def _pid(session) -> int:
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


async def _wait_blocked(
    observer_factory, blocker_pid: int, timeout: float = 5.0, *, unless_done: asyncio.Task | None = None
) -> list[tuple[int, str]]:
    """Backends queued on a lock `blocker_pid` holds; stops early once `unless_done` has finished."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with observer_factory() as observer:
        while loop.time() < deadline and not (unless_done is not None and unless_done.done()):
            waiting = await blocked_by(observer, blocker_pid)
            if waiting:
                return waiting
            await asyncio.sleep(0.02)
    return []


async def _finish(*tasks) -> None:
    for task in tasks:
        if task is not None and not task.done():
            await asyncio.wait([task], timeout=20)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=5)


async def _ledger_invariants(stand, equivalent_id) -> dict:
    """Trust limits and one direction per pair, read on a fresh session."""

    async with stand() as s:
        debts = {
            (d.debtor_id, d.creditor_id): Decimal(str(d.amount))
            for d in (await s.scalars(select(Debt).where(Debt.equivalent_id == equivalent_id))).all()
        }
        limits = {
            (line.from_participant_id, line.to_participant_id): Decimal(str(line.limit))
            for line in (
                await s.scalars(
                    select(TrustLine).where(TrustLine.equivalent_id == equivalent_id, TrustLine.status == "active")
                )
            ).all()
        }
    over_limit = {
        pair: amount for pair, amount in debts.items() if amount > limits.get((pair[1], pair[0]), Decimal("0"))
    }
    both_directions = sorted(
        {frozenset(pair) for pair in debts if (pair[1], pair[0]) in debts}, key=lambda p: sorted(map(str, p))
    )
    return {"debts": debts, "over_limit": over_limit, "both_directions": both_directions}


# ── (a) lost update: payment vs clearing on one cycle ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("parked", ["clearing", "payment"])
@pytest.mark.parametrize("mode", MODES)
async def test_a_lost_update_payment_vs_clearing(mode, parked, stand, monkeypatch) -> None:
    """A pays B 50 while the cycle A->B 100, B->C 30, C->A 40 is cleared (30).

    Either serial order ends at A->B 120, C->A 10 (B->C cleared away). One writer is PARKED at the point
    that makes the race - the clearing after its `FOR UPDATE` of the cycle, before its first write; or the
    payment after its pre-state read, before its first debt write - and the other is let run into it.
    """

    switch = switch_money_boundary_locks_off(monkeypatch) if mode == "locks_off" else None
    conflicts = count_conflicts(monkeypatch)
    seed = await _seed_interlock_case()
    a_id, b_id, c_id = seed["participant_ids"]
    d_ab, d_bc, d_ca = seed["debt_ids"]
    tx_id = str(uuid.uuid4())
    parked_event, release = asyncio.Event(), asyncio.Event()
    parked_pid: list[int] = []

    if parked == "clearing":
        original = ClearingService._cycle_respects_auto_clearing

        async def park_clearing(self, debts):
            if not parked_event.is_set():
                parked_pid.append(await _pid(self.session))
                parked_event.set()
                await release.wait()
            return await original(self, debts)

        monkeypatch.setattr(ClearingService, "_cycle_respects_auto_clearing", park_clearing)
    else:
        original_prestate = payment_service_module._read_payment_prestate

        async def park_payment(session, declared_flows):
            result = await original_prestate(session, declared_flows)
            if not parked_event.is_set():
                parked_pid.append(await _pid(session))
                parked_event.set()
                await release.wait()
            return result

        monkeypatch.setattr(payment_service_module, "_read_payment_prestate", park_payment)

    request = PaymentCreateRequest(
        tx_id=tx_id, to=seed["participant_pids"][1], equivalent=seed["equivalent_code"], amount="50.00",
        signature="__internal__",
    )

    async def pay():
        return await PaymentService.pay(stand, a_id, request, require_signature=False)

    async def clear():
        async with stand() as session:
            return await ClearingService(session).execute_clearing_with_amount(seed["cycle"])

    first, second = (clear, pay) if parked == "clearing" else (pay, clear)
    first_task = second_task = None
    try:
        first_task = asyncio.create_task(first())
        await asyncio.wait_for(parked_event.wait(), timeout=20)
        second_task = asyncio.create_task(second())
        waiting = await _wait_blocked(stand, parked_pid[0], timeout=3.0, unless_done=second_task)
        second_done_while_parked = second_task.done()
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=60)
    finally:
        release.set()
        await _finish(first_task, second_task)
        PaymentRouter.invalidate_cache(seed["equivalent_code"])

    cleared, paid = (results[0], results[1]) if parked == "clearing" else (results[1], results[0])
    invariants = await _ledger_invariants(stand, seed["equivalent_id"])
    async with stand() as s:
        clearings = (
            await s.execute(
                select(Transaction.tx_id, Transaction.state).where(
                    Transaction.type == "CLEARING", Transaction.initiator_id.in_(seed["participant_ids"])
                )
            )
        ).all()
        payment_row = (await s.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))).scalar_one()
        audits = sorted(
            (a.operation_type, a.verification_passed)
            for a in (
                await s.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.tx_id.in_([tx_id, *[row.tx_id for row in clearings]])
                    )
                )
            ).all()
        )
        envelopes = sorted(
            tuple(row)
            for row in (
                await s.execute(
                    select(debt_operations.c.kind, debt_operations.c.state).where(
                        debt_operations.c.tx_id.in_([tx_id, *[row.tx_id for row in clearings]])
                    )
                )
            ).all()
        )

    _report(
        "a_lost_update", mode=mode, parked=parked, conflicts_payment=conflicts.payment,
        conflicts_clearing=conflicts.clearing, waiting=waiting, second_done_while_parked=second_done_while_parked,
        switch=dict(switch.calls) if switch else None, debts={f"{k[0]}>{k[1]}": v for k, v in invariants["debts"].items()},
    )

    # Mechanism: the race really happened.
    if switch is not None:
        assert switch.total > 0, "the lock switch was never on the measured path"
        assert conflicts.serialization_failures > 0, (
            f"no 40001 was counted ({conflicts}): the two writers did not actually race, so 'no lost "
            f"update' is vacuous"
        )
    # The serial result, and everything that goes with it.
    assert paid.status == "COMMITTED", paid
    assert cleared == Decimal("30.00000000"), cleared
    assert invariants["debts"] == {(a_id, b_id): Decimal("120.00000000"), (c_id, a_id): Decimal("10.00000000")}, (
        invariants["debts"]
    )
    assert invariants["over_limit"] == {} and invariants["both_directions"] == []
    assert len(clearings) == 1 and clearings[0].state == "COMMITTED"
    assert payment_row == "COMMITTED"
    assert audits == [("CLEARING", True), ("PAYMENT", True)], audits
    assert envelopes == [("CLEARING", "COMPLETED"), ("PAYMENT", "COMPLETED")], envelopes


# ── (b) one direction per pair: payment/inject and inject/inject ──────────────────────────────


async def _seed_pair(stand, code_prefix: str):
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant

    n = uuid.uuid4().hex[:8].upper()
    async with stand() as s:
        eq = Equivalent(code=f"{code_prefix}{n}"[:16], precision=2, is_active=True)
        x = Participant(pid=f"X_{code_prefix}_{n}", display_name="X", public_key=f"pk_x_{n}", type="person", status="active")
        y = Participant(pid=f"Y_{code_prefix}_{n}", display_name="Y", public_key=f"pk_y_{n}", type="person", status="active")
        s.add_all([eq, x, y])
        await s.flush()
        # Both directions of trust, so both directions of debt are individually admissible.
        s.add_all(
            [
                TrustLine(from_participant_id=x.id, to_participant_id=y.id, equivalent_id=eq.id,
                          limit=Decimal("100.00"), status="active"),
                TrustLine(from_participant_id=y.id, to_participant_id=x.id, equivalent_id=eq.id,
                          limit=Decimal("100.00"), status="active"),
            ]
        )
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, x, y


def _inject_runner(eq, participants, *, creditor, debtor, amount: str):
    from app.core.simulator.models import RunRecord
    from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import _Artifacts, _runner

    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": p.pid} for p in participants],
        "trustlines": [],
        "behaviorProfiles": [],
        "events": [
            {
                "type": "inject",
                "time": 0,
                "effects": [
                    {"op": "inject_debt", "from": creditor.pid, "to": debtor.pid, "equivalent": eq.code, "amount": amount}
                ],
            }
        ],
    }
    run = RunRecord(run_id=f"p019-t1908-{uuid.uuid4().hex[:8]}", scenario_id="p019-t1908", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1
    run.sim_time_ms = 1_000
    run.intensity_percent = 0
    run._real_seeded = True
    run._real_participants = [(p.id, p.pid) for p in participants]
    run._real_equivalents = [eq.code]
    run._edges_by_equivalent = {}
    run._real_viz_by_eq = {}
    artifacts = _Artifacts()
    return _runner(run, scenario, artifacts), run, scenario, artifacts


class _Barrier:
    """Two parties; a party waits at most `timeout` for the other (with the locks on, the second party is
    queued on the first's owner lock and never arrives - the schedule is then serialised by the lock, and
    the result says so)."""

    def __init__(self, parties: int = 2, timeout: float = 2.0) -> None:
        self.parties, self.timeout = parties, timeout
        self.arrived = 0
        self.met = asyncio.Event()
        self.timed_out = 0

    async def wait(self) -> None:
        self.arrived += 1
        if self.arrived >= self.parties:
            self.met.set()
        try:
            await asyncio.wait_for(self.met.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            self.timed_out += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", ["inject_inject", "payment_inject"])
@pytest.mark.parametrize("mode", MODES)
async def test_b_opposing_directions_on_one_pair(mode, pair, stand, monkeypatch) -> None:
    """Two writers create OPPOSITE debts on one pair at once; at most one direction may survive.

    inject/inject: `inject_debt` X->Y (Y owes X) and Y->X (X owes Y), two runs, two sessions.
    payment/inject: X pays Y 10 (X owes Y) and `inject_debt` X->Y (Y owes X).
    Each writer is held at a two-party barrier AFTER it has read the opposite edge and BEFORE it writes, so
    with the locks off both read "no opposite debt" in their snapshots - the design's counterexample
    (spec, item 5: "проектный, не воспроизведённая потеря"). SSI must break the read-write cycle.
    """

    from app.core.simulator.inject_executor import InjectExecutor

    switch = switch_money_boundary_locks_off(monkeypatch) if mode == "locks_off" else None
    conflicts = count_conflicts(monkeypatch)
    eq, x, y = await _seed_pair(stand, "PB")
    barrier = _Barrier()

    original_stage = InjectExecutor.stage_inject_event

    async def stage_then_meet(self, session, **kwargs):
        staged = await original_stage(self, session, **kwargs)
        if barrier.arrived < barrier.parties:
            await barrier.wait()
        return staged

    monkeypatch.setattr(InjectExecutor, "stage_inject_event", stage_then_meet)

    original_prestate = payment_service_module._read_payment_prestate

    async def prestate_then_meet(session, declared_flows):
        result = await original_prestate(session, declared_flows)
        if barrier.arrived < barrier.parties:
            await barrier.wait()
        return result

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", prestate_then_meet)

    async def inject(creditor, debtor):
        runner, run, scenario, artifacts = _inject_runner(eq, [x, y], creditor=creditor, debtor=debtor, amount="10.00")
        async with stand() as session:
            await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)
        return [e.get("description") or e.get("type") for e in artifacts.events]

    async def pay_x_to_y():
        request = PaymentCreateRequest(
            tx_id=str(uuid.uuid4()), to=y.pid, equivalent=eq.code, amount="10.00", signature="__internal__"
        )
        return await PaymentService.pay(stand, x.id, request, require_signature=False)

    if pair == "inject_inject":
        writers = [inject(x, y), inject(y, x)]
    else:
        writers = [pay_x_to_y(), inject(x, y)]
    tasks = [asyncio.create_task(w) for w in writers]
    try:
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=60)
    finally:
        await _finish(*tasks)
        PaymentRouter.invalidate_cache(eq.code)

    invariants = await _ledger_invariants(stand, eq.id)
    _report(
        "b_opposing_directions", mode=mode, pair=pair, barrier_met=barrier.met.is_set(), barrier_timeouts=barrier.timed_out,
        conflicts_payment=conflicts.payment, conflicts_inject=conflicts.inject,
        outcomes=[repr(o)[:160] for o in outcomes], switch=dict(switch.calls) if switch else None,
        debts={f"{k[0]}>{k[1]}": v for k, v in invariants["debts"].items()},
    )

    assert not [o for o in outcomes if isinstance(o, BaseException)], outcomes
    if switch is not None:
        assert switch.total > 0, "the lock switch was never on the measured path"
        assert barrier.met.is_set(), "both writers must have read the opposite edge before either wrote"
        assert conflicts.serialization_failures > 0, (
            f"no 40001 was counted ({conflicts}): SSI never had to intervene, the race is vacuous"
        )
    assert invariants["both_directions"] == [], f"both directions of one pair exist: {invariants['debts']}"
    assert invariants["over_limit"] == {}, invariants["over_limit"]
    # The result is one of the SERIAL results, and the writers that ran are accounted for.
    ten = Decimal("10.00000000")
    if pair == "inject_inject":
        # Whichever inject ran first is applied; the other finds the opposite debt and is refused.
        assert invariants["debts"] in ({(y.id, x.id): ten}, {(x.id, y.id): ten}), invariants["debts"]
    else:
        # payment first: X owes Y 10, the inject then meets it and is refused -> {X->Y: 10};
        # inject first: Y owes X 10, the payment then nets it away -> {} (both applied).
        assert outcomes[0].status == "COMMITTED", outcomes[0]
        assert invariants["debts"] in ({}, {(x.id, y.id): ten}), invariants["debts"]


# ── the bottleneck loser: its outcome by admission (T1908, `FORK-5`) ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_the_bottleneck_loser_is_refused_after_admission(mode, stand, monkeypatch) -> None:
    """A and B each pay D 8 through C, whose line to D is 10: one commits, the other cannot fit.

    Both are held after their pre-state reads (with the locks on the second is queued on the owner lock
    and never reaches the barrier). The loser's outcome is decided by ADMISSION, not by where the conflict
    fell: both requests passed routing and the stop/hold check and began binding, so the loser - refused
    on its fresh retry for capacity - is a DEFINITIVE refusal, stored `ABORTED` (`E002`), and its replay
    answers the same.
    """

    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.utils.exceptions import RoutingException

    switch = switch_money_boundary_locks_off(monkeypatch) if mode == "locks_off" else None
    conflicts = count_conflicts(monkeypatch)
    n = uuid.uuid4().hex[:8].upper()
    async with stand() as s:
        eq = Equivalent(code=f"BN{n}"[:16], precision=2, is_active=True)
        people = {
            k: Participant(pid=f"{k}_BN_{n}", display_name=k, public_key=f"pk_{k}_{n}", type="person", status="active")
            for k in "ABCD"
        }
        s.add_all([eq, *people.values()])
        await s.flush()
        s.add_all(
            [
                TrustLine(from_participant_id=people["C"].id, to_participant_id=people["A"].id,
                          equivalent_id=eq.id, limit=Decimal("100.00"), status="active"),
                TrustLine(from_participant_id=people["C"].id, to_participant_id=people["B"].id,
                          equivalent_id=eq.id, limit=Decimal("100.00"), status="active"),
                TrustLine(from_participant_id=people["D"].id, to_participant_id=people["C"].id,
                          equivalent_id=eq.id, limit=Decimal("10.00"), status="active"),
            ]
        )
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    barrier = _Barrier()
    original_prestate = payment_service_module._read_payment_prestate

    async def prestate_then_meet(session, declared_flows):
        result = await original_prestate(session, declared_flows)
        if barrier.arrived < barrier.parties:
            await barrier.wait()
        return result

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", prestate_then_meet)
    tx_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    async def pay(sender, tx_id):
        request = PaymentCreateRequest(
            tx_id=tx_id, to=people["D"].pid, equivalent=eq.code, amount="8.00", signature="__internal__"
        )
        try:
            return await PaymentService.pay(stand, sender.id, request, require_signature=False)
        except Exception as exc:  # noqa: BLE001 - compared below
            return exc

    tasks = [asyncio.create_task(pay(people["A"], tx_ids[0])), asyncio.create_task(pay(people["B"], tx_ids[1]))]
    try:
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks), timeout=60)
    finally:
        await _finish(*tasks)
        PaymentRouter.invalidate_cache(eq.code)

    async with stand() as s:
        states = dict((await s.execute(select(Transaction.tx_id, Transaction.state).where(Transaction.tx_id.in_(tx_ids)))).all())
        shared = await s.scalar(
            select(func.coalesce(func.sum(Debt.amount), 0)).where(
                Debt.debtor_id == people["C"].id, Debt.creditor_id == people["D"].id, Debt.equivalent_id == eq.id
            )
        )
    winners = [o for o in outcomes if not isinstance(o, Exception)]
    losers = [o for o in outcomes if isinstance(o, Exception)]
    _report(
        "bottleneck_loser", mode=mode, barrier_met=barrier.met.is_set(), conflicts_payment=conflicts.payment,
        outcomes=[repr(o)[:160] for o in outcomes], states=states, shared_debt=shared,
        switch=dict(switch.calls) if switch else None,
    )
    if switch is not None:
        assert switch.total > 0
        assert barrier.met.is_set(), "both payments must have read their pre-state before either wrote"
        assert conflicts.payment, "no conflict was counted: the two payments did not race"
    assert len(winners) == 1 and winners[0].status == "COMMITTED", outcomes
    assert len(losers) == 1 and isinstance(losers[0], RoutingException) and losers[0].code == "E002", outcomes
    loser_tx = next(t for t in tx_ids if t != winners[0].tx_id)
    assert states == {winners[0].tx_id: "COMMITTED", loser_tx: "ABORTED"}, states
    assert Decimal(str(shared)) == Decimal("8.00000000")


# ── (c) the refusal of an unsuitable isolation, with and without the locks ────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["payment_staged", "clearing", "inject"])
@pytest.mark.parametrize("mode", MODES)
async def test_c_an_unsuitable_isolation_is_refused_with_or_without_the_locks(
    mode, writer, committed_database, monkeypatch
) -> None:
    """The three debt writers refuse a READ COMMITTED transaction before their first write, and the refusal
    does not lean on the locks: with the lock primitives switched off it is the same refusal and nothing is
    written. (The full per-boundary refusal, with the caller's transaction untouched, is `T1907`'s
    `test_p019_money_writers_refuse_non_serializable_postgres.py`; this is the lock-independence half.)"""

    from sqlalchemy.pool import NullPool

    from tests.integration.test_p019_money_writers_refuse_non_serializable_postgres import (
        _at_read_committed,
        _refused,
        _run_writer,
        _state,
    )

    switch = switch_money_boundary_locks_off(monkeypatch) if mode == "locks_off" else None
    seed = await _seed_interlock_case()
    before = await _state(committed_database, seed)
    engine = create_async_engine(committed_database.url, isolation_level="READ COMMITTED", poolclass=NullPool)
    try:
        read_committed = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
        async with read_committed() as session:
            await _at_read_committed(session)
            outcome = await _run_writer(writer, session, seed, committed_database)
            await session.rollback()
    finally:
        await engine.dispose()
        PaymentRouter.invalidate_cache(seed["equivalent_code"])
    after = await _state(committed_database, seed)
    _report(
        "c_isolation_refusal", mode=mode, writer=writer, refused=_refused(outcome), outcome=repr(outcome)[:160],
        switch=dict(switch.calls) if switch else None,
    )
    assert _refused(outcome), f"{writer} ran at READ COMMITTED ({mode}): {outcome!r}"
    assert after == before, f"{writer} refused but changed committed state ({mode})"
