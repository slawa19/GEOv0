"""027 `T2703`: the stage-2 COUNTEREXAMPLES, written before the implementation (`T2704`).

Each stand is a real schedule (barriers in the writers' own code, no injected SQLSTATE) and runs in cells:

* `rc` - the code as it ships since `T2704`: MUST PASS. Until then `naive_rc` (019 code, guard bypassed) failed
  every stand with `TargetMismatch` (Changelog `T2703`).
* `naive_rr` - REPEATABLE READ, guard bypassed: the snapshot predates the lock wait, so it stays broken (xfail).

THE EXPECTED FAILURE IS NARROW. Mechanism checks are plain `assert`s (a broken stand goes red, never
xfail): the transaction level each writer actually ran at (`SHOW transaction_isolation`, read by the
guard), and that the two writers overlapped - both met at the barrier, or one parked while the other
committed. Only the final comparison raises `TargetMismatch` (`require_target`).

Not built, with the reason recorded in the spec (`T2703`, Changelog): close / hold / idempotency stands
that do not break on naive RC (measured by probes on the same harness, not committed).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clearing.service import ClearingService
from app.core.ledger.book import Book, operation_for
from app.core.ledger.reconciliation import take_baseline, verify_journal_equals_change
from app.core.money_boundary import MoneyBoundary
from app.core.payments import service as payment_service_module
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator.inject_executor import InjectExecutor
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from tests.integration.p019_interlock_support import _seed_interlock_case
from tests.integration.test_p019_t1908_lock_removal_experiments_postgres import (
    _Barrier,
    _finish,
    _inject_runner,
    _ledger_invariants,
    _seed_pair,
    count_conflicts,
)
from tests.integration.test_p019_trust_decay_respects_concurrent_debt_postgres import _decaying_run, _world
from tests.p019_support import TargetMismatch, require_target

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

NAIVE = pytest.mark.xfail(raises=TargetMismatch, strict=True, reason="REPEATABLE READ must break this invariant (027)")
CELLS = [pytest.param("rc")]
LEVEL = {"rc": "READ COMMITTED", "naive_rr": "REPEATABLE READ"}
PARK_S = 1.5  # < PREPARE_TIMEOUT_SECONDS (3): at `rc` the other side waits on a line lock for the parked one


@dataclass
class Rig:
    cell: str
    sessions: async_sessionmaker
    levels: list
    conflicts: object

    def ran_at_the_cell_level(self) -> None:
        """Mechanism (M5 of the spec): every guarded writer ran at the level of the cell."""
        expected = LEVEL[self.cell].lower()
        assert self.levels, "no money writer reached the isolation guard: the stand is off the measured path"
        assert {level for _writer, level in self.levels} == {expected}, self.levels


@pytest_asyncio.fixture
async def rig(request, committed_database, monkeypatch):
    cell = request.param
    engine = create_async_engine(committed_database.url, pool_size=10, max_overflow=0, isolation_level=LEVEL[cell])
    levels: list = []
    original = MoneyBoundary.require_read_committed

    async def guard(session, *, writer: str) -> None:
        levels.append((writer, str(await session.scalar(text("SHOW transaction_isolation")))))
        if cell == "rc":
            await original(session, writer=writer)

    monkeypatch.setattr(MoneyBoundary, "require_read_committed", staticmethod(guard))
    try:
        yield Rig(cell, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False),
                  levels, count_conflicts(monkeypatch))
    finally:
        await engine.dispose()


async def _pay(rig: Rig, sender_id, receiver_pid: str, code: str, amount: str, tx_id: str | None = None):
    request = PaymentCreateRequest(tx_id=tx_id or str(uuid.uuid4()), to=receiver_pid, equivalent=code, amount=amount,
                                   signature="__internal__")
    try:
        return await PaymentService.pay(rig.sessions, sender_id, request, require_signature=False)
    except Exception as exc:  # noqa: BLE001 - the outcome is compared, not raised
        return exc


def _meet_before_commit(monkeypatch, barrier: _Barrier) -> None:
    """Payment: park AFTER its debt writes and checks, BEFORE its commit (the integrity audit row)."""
    original = PaymentService._write_integrity_audit

    async def audit_then_meet(self, *args, **kwargs):
        if barrier.arrived < barrier.parties:
            await barrier.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_write_integrity_audit", audit_then_meet)


def _overlapped(rig: Rig, barrier: _Barrier) -> None:
    """The first writer timed out at the barrier: the other waited on a line lock. No 40001 - the pair waits."""
    assert barrier.timed_out, "the writers met: no line lock held the second one up"
    assert rig.conflicts.serialization_failures == 0, rig.conflicts


async def _criterion_b(rig: Rig, equivalent_id) -> tuple:
    async with rig.sessions() as s:
        return (await verify_journal_equals_change(s, equivalent_id)).findings


# ── 1. opposite payments over a fresh pair: one direction per pair ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rig", [*CELLS, pytest.param("naive_rr", marks=NAIVE)], indirect=True)
async def test_opposite_payments_on_a_fresh_pair_keep_one_direction(rig: Rig, monkeypatch) -> None:
    """X pays Y 10 and Y pays X 10 at once on a pair with no debt. Serially: the second nets the first, {}."""
    eq, x, y = await _seed_pair(rig.sessions, "OP")
    barrier = _Barrier()
    _meet_before_commit(monkeypatch, barrier)
    try:
        outcomes = await asyncio.gather(_pay(rig, x.id, y.pid, eq.code, "10.00"), _pay(rig, y.id, x.pid, eq.code, "10.00"))
    finally:
        PaymentRouter.invalidate_cache(eq.code)
    debts = (await _ledger_invariants(rig.sessions, eq.id))["debts"]
    rig.ran_at_the_cell_level()
    _overlapped(rig, barrier)
    assert all(getattr(o, "status", None) == "COMMITTED" for o in outcomes), outcomes
    require_target(debts == {} and not await _criterion_b(rig, eq.id), f"both payments committed, debts {debts}")


# ── 2. opposing inject/inject and payment/inject on a fresh pair ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", ["inject_inject", "payment_inject"])
@pytest.mark.parametrize("rig", CELLS, indirect=True)
async def test_opposing_injects_and_payments_keep_one_direction(rig: Rig, pair, monkeypatch) -> None:
    """`test_b_opposing_directions_on_one_pair` (019 `T1908`) on the cells; the inject meets AFTER staging
    (it has read the opposite edge), the payment after its writes - both before their commits."""
    eq, x, y = await _seed_pair(rig.sessions, "OI")
    barrier = _Barrier()
    _meet_before_commit(monkeypatch, barrier)
    original_stage = InjectExecutor.stage_inject_event

    async def stage_then_meet(self, session, **kwargs):
        staged = await original_stage(self, session, **kwargs)
        if barrier.arrived < barrier.parties:
            await barrier.wait()
        return staged

    monkeypatch.setattr(InjectExecutor, "stage_inject_event", stage_then_meet)

    async def inject(creditor, debtor):
        runner, run, scenario, _artifacts = _inject_runner(eq, [x, y], creditor=creditor, debtor=debtor, amount="10.00")
        async with rig.sessions() as session:
            await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)

    first = inject(x, y) if pair == "inject_inject" else _pay(rig, x.id, y.pid, eq.code, "10.00")
    tasks = [asyncio.create_task(first), asyncio.create_task(inject(y, x) if pair == "inject_inject" else inject(x, y))]
    try:
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=60)
    finally:
        await _finish(*tasks)
        PaymentRouter.invalidate_cache(eq.code)
    invariants = await _ledger_invariants(rig.sessions, eq.id)
    rig.ran_at_the_cell_level()
    _overlapped(rig, barrier)
    assert not [o for o in outcomes if isinstance(o, BaseException)], outcomes
    ten = Decimal("10.00000000")
    serial = ({(y.id, x.id): ten}, {(x.id, y.id): ten}) if pair == "inject_inject" else ({}, {(x.id, y.id): ten})
    require_target(invariants["both_directions"] == [] and invariants["debts"] in serial,
                   f"{pair}: debts {invariants['debts']}")


# ── 3. the first debt of a pair: capacity read before a concurrent insert committed ───────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rig", CELLS, indirect=True)
async def test_first_debt_race_leaves_one_winner_and_a_definitive_refusal(rig: Rig, monkeypatch) -> None:
    """X pays Y 60 twice on a fresh pair under limit 100. The first parks after its capacity check
    (before its pre-state) until the second has committed or `PARK_S` passed. Serially: one COMMITTED,
    the other refused for capacity (`E002`, stored `ABORTED`), debt 60."""
    eq, x, y = await _seed_pair(rig.sessions, "FD")
    other: list[asyncio.Task] = []
    parked: list[bool] = []
    tx_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    original = payment_service_module._read_payment_prestate

    async def park_first(session, declared_flows):
        if not parked:
            parked.append(True)
            other.append(asyncio.create_task(_pay(rig, x.id, y.pid, eq.code, "60.00", tx_ids[1])))
            await asyncio.wait(other, timeout=PARK_S)
            parked.append(other[0].done())
        return await original(session, declared_flows)

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", park_first)
    first = asyncio.create_task(_pay(rig, x.id, y.pid, eq.code, "60.00", tx_ids[0]))
    try:
        outcomes = [await asyncio.wait_for(first, timeout=60), await asyncio.wait_for(other[0], timeout=60)]
    finally:
        await _finish(first, *other)
        PaymentRouter.invalidate_cache(eq.code)
    async with rig.sessions() as s:
        states = dict((await s.execute(select(Transaction.tx_id, Transaction.state)
                                       .where(Transaction.tx_id.in_(tx_ids)))).all())
    debts = (await _ledger_invariants(rig.sessions, eq.id))["debts"]
    rig.ran_at_the_cell_level()
    assert len(parked) == 2, parked  # the first parked; parked[1]: the second committed meanwhile
    codes = sorted(str(getattr(o, "status", None) or getattr(o, "code", None) or type(o).__name__) for o in outcomes)
    require_target(codes == ["COMMITTED", "E002"] and sorted(states.values()) == ["ABORTED", "COMMITTED"]
                   and debts == {(x.id, y.id): Decimal("60.00000000")},
                   f"outcomes {[repr(o)[:200] for o in outcomes]}, states {states}, debts {debts}")


# ── 4. the payment delta check against a neighbour's commit on a shared participant ───────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rig", CELLS, indirect=True)
async def test_delta_check_ignores_a_neighbour_commit_on_a_shared_participant(rig: Rig, monkeypatch) -> None:
    """A pays B 10 while C pays A 10: disjoint pairs, shared participant A. A->B parks after its
    `net_positions_before` until C->A committed. Serially both commit; no `PAYMENT_DELTA_DRIFT`."""
    seed = await _seed_interlock_case()
    a_id, b_id, c_id = seed["participant_ids"]
    a_pid, b_pid, _c_pid = seed["participant_pids"]
    code = seed["equivalent_code"]
    neighbour: list[asyncio.Task] = []
    parked: list[bool] = []
    original = MoneyBoundary._snapshot_net_positions

    async def park_after_before(self, *, equivalent_id, participant_ids, **kw):
        result = await original(self, equivalent_id=equivalent_id, participant_ids=participant_ids, **kw)
        if b_id in participant_ids and not parked:
            parked.append(True)
            neighbour.append(asyncio.create_task(_pay(rig, c_id, a_pid, code, "10.00")))
            await asyncio.wait(neighbour, timeout=PARK_S)
            parked.append(neighbour[0].done())
        return result

    monkeypatch.setattr(MoneyBoundary, "_snapshot_net_positions", park_after_before)
    try:
        paid = await asyncio.wait_for(_pay(rig, a_id, b_pid, code, "10.00"), timeout=60)
        other = await asyncio.wait_for(neighbour[0], timeout=60)
    finally:
        await _finish(*neighbour)
        PaymentRouter.invalidate_cache(code)
    rig.ran_at_the_cell_level()
    assert parked[:2] == [True, True], f"the neighbour did not commit while A->B was parked: {parked}"
    assert getattr(other, "status", None) == "COMMITTED", other
    require_target(getattr(paid, "status", None) == "COMMITTED", f"A->B ended with {paid!r}"[:400])


# ── 5. clearing and a payment on a shared edge ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rig", CELLS, indirect=True)
async def test_clearing_and_payment_on_a_shared_edge(rig: Rig, monkeypatch) -> None:
    """A pays B 50 while the cycle A->B 100, B->C 30, C->A 40 is cleared (30); the payment parks after its
    pre-state until the clearing is done or `PARK_S` passed. Serially: A->B 120, C->A 10, and criterion (b)
    of the verifier finds nothing. Until `T2704` the counterexample was `naive_rc_unlocked` (naive RC with the
    equivalent lock off): the prestate was read before the clearing's commit. At `rc` the clearing waits on the
    payment's line lock of A-B."""
    seed = await _seed_interlock_case()
    a_id, b_id, c_id = seed["participant_ids"]
    clearing: list[asyncio.Task] = []
    parked: list[bool] = []
    original = payment_service_module._read_payment_prestate

    async def clear():
        async with rig.sessions() as session:
            return await ClearingService(session).execute_occurrence(seed["occurrence"])

    async def park_payment(session, declared_flows):
        result = await original(session, declared_flows)
        if not parked:
            parked.append(True)
            clearing.append(asyncio.create_task(clear()))
            await asyncio.wait(clearing, timeout=PARK_S)
            parked.append(clearing[0].done())
        return result

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", park_payment)
    try:
        paid = await asyncio.wait_for(_pay(rig, a_id, seed["participant_pids"][1], seed["equivalent_code"], "50.00"), 60)
        cleared = await asyncio.wait_for(clearing[0], timeout=60)
    finally:
        await _finish(*clearing)
        PaymentRouter.invalidate_cache(seed["equivalent_code"])
    debts = (await _ledger_invariants(rig.sessions, seed["equivalent_id"]))["debts"]
    findings = await _criterion_b(rig, seed["equivalent_id"])
    rig.ran_at_the_cell_level()
    assert len(parked) == 2, parked
    assert not parked[1], "the clearing committed while the payment held the A-B lines"
    assert getattr(paid, "status", None) == "COMMITTED" and cleared == Decimal("30.00000000"), (paid, cleared)
    serial = {(a_id, b_id): Decimal("120.00000000"), (c_id, a_id): Decimal("10.00000000")}
    require_target(debts == serial and not findings, f"debts {debts}, criterion (b) findings {findings}")


# ── 6. SEED after the baseline ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rig", CELLS, indirect=True)
async def test_seed_never_commits_after_a_baseline_that_missed_it(rig: Rig) -> None:
    """A SEED has written a debt and completed its book operation (it read: no baseline); a baseline of
    the equivalent is taken on another transaction; then the SEED commits. Serially: the SEED either
    precedes the baseline (which then sees its debt) or is refused."""
    eq, x, y = await _seed_pair(rig.sessions, "SB")
    seed_error: BaseException | None = None
    async with rig.sessions() as seed:
        rig.levels.append(("seed", str(await seed.scalar(text("SHOW transaction_isolation")))))
        async with Book.operation(seed, operation_for("SEED", f"p027-seed/{uuid.uuid4()}", {"probe": "t2703"},
                                                      scope_equivalent_ids=None)):
            seed.add(Debt(debtor_id=x.id, creditor_id=y.id, equivalent_id=eq.id, amount=Decimal("4")))
            await seed.flush()

        async def cutover():
            async with rig.sessions() as session:
                taken = await take_baseline(session, eq.id)
                await session.commit()
                return taken

        baseline = asyncio.create_task(cutover())
        await asyncio.wait([baseline], timeout=PARK_S)
        baseline_first = baseline.done()
        try:
            await seed.commit()
        except Exception as exc:  # noqa: BLE001 - the refusal is the subject
            seed_error = exc
            await seed.rollback()
    taken = await asyncio.wait_for(baseline, timeout=60)
    debts = (await _ledger_invariants(rig.sessions, eq.id))["debts"]
    rig.ran_at_the_cell_level()
    assert not baseline_first, "the baseline committed while the SEED held the equivalent row"
    seed_committed = seed_error is None
    assert seed_committed == bool(debts), (seed_error, debts)
    require_target(not seed_committed or taken.edges_seen >= 1,
                   f"a SEED committed after a baseline that did not see it: {taken}, error {seed_error!r}")


# ── 7. trust decay during a payment ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rig", CELLS, indirect=True)
async def test_trust_decay_during_a_payment_never_floors_below_its_debt(rig: Rig, monkeypatch) -> None:
    """Debt S->R 90 under limit 100; S pays R 10 and parks at its money phase (after its line read) while
    the tick's decay (snapshot 90) runs, until the decay is done or `PARK_S` passed. The decay floors at the
    debt it reads: serially the limit ends at least at the debt (019 `test_p019_trust_decay_...`)."""
    from app.core.simulator.trust_drift_engine import TrustDriftEngine

    eq, sender, receiver = await _world(rig.sessions)
    run, scenario = _decaying_run(eq, sender, receiver)
    engine = TrustDriftEngine(sse=None, utc_now=None, logger=logging.getLogger("tests.p027.decay"),
                              get_scenario_raw=lambda _s: scenario)
    decay: list[asyncio.Task] = []
    parked: list[bool] = []
    original = PaymentService._apply_payment

    async def run_decay():
        async with rig.sessions() as tail:
            decayed = await engine.apply_trust_decay(run, tail, 7, scenario)
            await tail.commit()
            return decayed

    async def park_money(self, declaration, *args, **kwargs):
        if not parked:
            parked.append(True)
            decay.append(asyncio.create_task(run_decay()))
            await asyncio.wait(decay, timeout=PARK_S)
            parked.append(decay[0].done())
        return await original(self, declaration, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_apply_payment", park_money)
    try:
        paid = await asyncio.wait_for(_pay(rig, sender.id, receiver.pid, eq.code, "10.00"), timeout=60)
        decayed = (await asyncio.wait_for(asyncio.gather(*decay, return_exceptions=True), timeout=60))[0]
    finally:
        await _finish(*decay)
        PaymentRouter.invalidate_cache(eq.code)
    async with rig.sessions() as s:
        limit = await s.scalar(select(TrustLine.limit).where(TrustLine.equivalent_id == eq.id,
                                                             TrustLine.status == "active"))
        debt = await s.scalar(select(Debt.amount).where(Debt.equivalent_id == eq.id))
    rig.ran_at_the_cell_level()
    assert len(parked) == 2 and getattr(paid, "status", None) == "COMMITTED", (parked, paid)
    assert not parked[1] and getattr(decayed, "updated_count", None) == 0, (parked, decayed)  # waited, read 100
    require_target(Decimal(str(debt)) <= Decimal(str(limit)), f"debt {debt} above the decayed limit {limit}")
