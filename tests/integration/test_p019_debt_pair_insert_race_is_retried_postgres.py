"""Programme 019 stage 5, `T1909` precondition 1: a concurrent insert of the SAME new debt row is a retry.

THE FINDING (`T1908`, 2026-09-25). Without the pair locks two writers can both read "no debt X->Y" and
both insert it; the loser's insert meets the winner's committed row on `uq_debts_debtor_creditor_equivalent`
and PostgreSQL answers `23505`, not `40001`. The classifiers retried only 40001/40P01, so the loser ended as
an internal error (`E010`) - and, after admission, as a stored definitive `ABORTED` - for what is a
transient conflict a fresh snapshot cures (the fresh attempt reads the row and updates it).

THE DECISION (fourth consultation, precondition 1): retry `23505` ONLY on that constraint, read from the
structured constraint name through the deliberate exception chain, in all three owners - the API payment
(`PaymentService.pay`), the simulator money phase (`money_replay`, through the staged payment), the inject
(`_apply_inject_unit_of_work`). Every other `23505` stays what it was; the `tx_id` identity resolver stays
separate.

THE SCHEDULE, real and independent of the advisory locks: the writer under test is PARKED after it has read
the pair (its SERIALIZABLE snapshot is taken) and before it writes; a competitor transaction then BLIND-inserts
the same debt row - no read first, so SSI has no read-write cycle to report and the insert is left to the
unique index - and commits; the writer is released and inserts. The competitor declares its write
(`debt_fixture_setup`), as every writer of `debts` must.

CONTROL BEFORE TARGET (`tests/p019_support.py`): every test first asserts, with plain assertions, that the
competitor committed while the writer was parked and that the writer really met `23505` on exactly that
constraint (recorded where the payment names its refusal, or where the inject classifies its error); only then
is the outcome compared with the target, and a mismatch is `TargetMismatch`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.payments import service as payment_service_module
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.core.simulator import real_runner_impl
from app.db.journal_tables import debt_journal_entries, debt_operations
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import RetryablePaymentConflictException
from tests.debt_setup import debt_fixture_setup
from tests.p019_support import require_target, target_xfail

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

DEBT_PAIR = "uq_debts_debtor_creditor_equivalent"
COMPETITOR = Decimal("5.00")
_FIX = "stage 5 part b (T1909, precondition 1)"


@pytest_asyncio.fixture
async def stand(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=8, max_overflow=0, pool_timeout=20, isolation_level="SERIALIZABLE"
    )
    # The COMPETITOR's engine, READ COMMITTED on purpose - see `_blind_insert`.
    competitor_engine = create_async_engine(committed_database.url, pool_size=2, max_overflow=0, isolation_level="READ COMMITTED")
    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
        factory.competitor = async_sessionmaker(
            bind=competitor_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        yield factory
    finally:
        await engine.dispose()
        await competitor_engine.dispose()


async def _people(stand, prefix: str, roles: str, lines: list[tuple[str, str]]):
    """An equivalent, participants, and active trust lines `creditor -> debtor` of limit 100; no debts."""

    n = uuid.uuid4().hex[:8].upper()
    async with stand() as s:
        eq = Equivalent(code=f"{prefix}{n}"[:16], precision=2, is_active=True)
        people = {
            r: Participant(pid=f"{r}_{prefix}_{n}", display_name=r, public_key=f"pk_{r}_{prefix}_{n}",
                           type="person", status="active")
            for r in roles
        }
        s.add_all([eq, *people.values()])
        await s.flush()
        s.add_all(
            [
                TrustLine(from_participant_id=people[c].id, to_participant_id=people[d].id, equivalent_id=eq.id,
                          limit=Decimal("100.00"), status="active")
                for c, d in lines
            ]
        )
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return eq, people


async def _blind_insert(stand, *, debtor, creditor, equivalent) -> None:
    """The competitor: one new debt row, inserted without reading the pair first, committed.

    READ COMMITTED ON PURPOSE, and only the competitor. The writer under test runs SERIALIZABLE, as the
    application does. A SERIALIZABLE competitor holds SIREAD locks of its own (its declared operation reads
    as it completes), and the loser's insert then closes a read-write cycle that PostgreSQL reports as 40001
    - measured on this stand before the fix: every schedule came back `('40001', None)`, which the owners
    already retry, and proved nothing about `23505`. Without SIREAD locks on the competitor's side the unique
    index alone decides the loser's insert: `23505` on `uq_debts_debtor_creditor_equivalent`, the shape
    `T1908`'s probe met between SERIALIZABLE writers (`sqlstate=23505 constraint=...`, spec, Changelog),
    here made deterministic. This is not evidence that a writer may run below SERIALIZABLE; the competitor
    is test setup, not a writer under test.
    """

    async with stand.competitor() as other:
        async with debt_fixture_setup(other, label="p019-t1909-competitor"):
            other.add(Debt(debtor_id=debtor.id, creditor_id=creditor.id, equivalent_id=equivalent.id,
                           amount=COMPETITOR))
        await other.commit()


def _record_payment_refusals(monkeypatch) -> list[tuple[str | None, str | None]]:
    """(SQLSTATE, constraint) of every failure the payment operation named - the collision's evidence."""

    seen: list[tuple[str | None, str | None]] = []
    original = payment_service_module._refuse_attempt

    def recording(attempt, error, prefix):
        seen.append((payment_service_module._payment_db_sqlstate(error), payment_service_module._constraint_name(error)))
        return original(attempt, error, prefix)

    monkeypatch.setattr(payment_service_module, "_refuse_attempt", recording)
    return seen


async def _debts(stand, eq) -> dict[tuple[uuid.UUID, uuid.UUID], Decimal]:
    async with stand() as s:
        rows = (await s.execute(select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(Debt.equivalent_id == eq.id))).all()
    return {(d, c): Decimal(str(a)) for d, c, a in rows}


async def _payment_evidence(stand, tx_id: str) -> dict:
    async with stand() as s:
        states = list((await s.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))).scalars())
        envelopes = (await s.execute(select(debt_operations.c.id, debt_operations.c.state).where(debt_operations.c.tx_id == tx_id))).all()
        entries = 0
        if envelopes:
            entries = int(
                await s.scalar(
                    select(func.count()).select_from(debt_journal_entries).where(
                        debt_journal_entries.c.operation_id.in_([e.id for e in envelopes])
                    )
                )
            )
        audits = int(await s.scalar(select(func.count()).select_from(IntegrityAuditLog).where(IntegrityAuditLog.tx_id == tx_id)))
    return {"states": states, "envelopes": [e.state for e in envelopes], "entries": entries, "audits": audits}


# ── the API payment (`pay()` owns the retry) ────────────────────────────────────────────────────


async def _api_schedule(stand, monkeypatch, *, tx_id: str):
    eq, p = await _people(stand, "RA", "XY", [("Y", "X")])  # Y trusts X: X may owe Y
    refusals = _record_payment_refusals(monkeypatch)
    parked, release = asyncio.Event(), asyncio.Event()
    original = payment_service_module._read_payment_prestate

    async def park_once(session, declared_flows):
        result = await original(session, declared_flows)
        if not parked.is_set():
            parked.set()
            await release.wait()
        return result

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", park_once)
    request = PaymentCreateRequest(tx_id=tx_id, to=p["Y"].pid, equivalent=eq.code, amount="10.00", signature="__internal__")

    async def pay():
        try:
            return await PaymentService.pay(stand, p["X"].id, request, require_signature=False)
        except Exception as exc:  # noqa: BLE001 - compared below
            return exc

    task = asyncio.create_task(pay())
    try:
        await asyncio.wait_for(parked.wait(), timeout=20)
        await _blind_insert(stand, debtor=p["X"], creditor=p["Y"], equivalent=eq)
        competitor_committed_while_parked = not task.done()
        release.set()
        outcome = await asyncio.wait_for(task, timeout=60)
    finally:
        release.set()
        PaymentRouter.invalidate_cache(eq.code)
    return eq, p, request, refusals, competitor_committed_while_parked, outcome


@target_xfail(_FIX, "the API payment retries a 23505 on the debt pair instead of ending E010/ABORTED")
@pytest.mark.asyncio
async def test_the_api_payment_retries_a_concurrent_insert_of_its_debt_row(stand, monkeypatch) -> None:
    tx_id = str(uuid.uuid4())
    eq, p, _request, refusals, raced, outcome = await _api_schedule(stand, monkeypatch, tx_id=tx_id)

    # Controls: the race happened, and it was THE collision.
    assert raced, "the competitor did not commit while the payment was parked"
    assert (("23505", DEBT_PAIR)) in refusals, f"the payment never met 23505 on {DEBT_PAIR}: {refusals}"

    debts = await _debts(stand, eq)
    evidence = await _payment_evidence(stand, tx_id)
    require_target(
        getattr(outcome, "status", None) == "COMMITTED",
        f"the payment ended {outcome!r} instead of retrying on a fresh snapshot",
    )
    require_target(debts == {(p["X"].id, p["Y"].id): Decimal("15.00000000")}, f"final debts {debts}")
    require_target(
        evidence == {"states": ["COMMITTED"], "envelopes": ["COMPLETED"], "entries": 1, "audits": 1},
        f"one committed row, one envelope, one journal entry, one audit row expected: {evidence}",
    )


@target_xfail(_FIX, "an exhausted debt-pair collision is a retryable 409, never a stored ABORTED")
@pytest.mark.asyncio
async def test_an_exhausted_debt_row_collision_is_a_retryable_conflict_and_not_aborted(stand, monkeypatch) -> None:
    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 1)
    tx_id = str(uuid.uuid4())
    eq, p, request, refusals, raced, outcome = await _api_schedule(stand, monkeypatch, tx_id=tx_id)

    assert raced, "the competitor did not commit while the payment was parked"
    assert (("23505", DEBT_PAIR)) in refusals, f"the payment never met 23505 on {DEBT_PAIR}: {refusals}"

    evidence = await _payment_evidence(stand, tx_id)
    debts_after_conflict = await _debts(stand, eq)
    # The same tx_id, submitted again with the race gone, executes (nothing was recorded against it).
    monkeypatch.setattr(settings, "COMMIT_RETRY_ATTEMPTS", 3)
    try:
        again = await PaymentService.pay(stand, p["X"].id, request, require_signature=False)
    except Exception as exc:  # noqa: BLE001 - compared below
        again = exc
    finally:
        PaymentRouter.invalidate_cache(eq.code)

    require_target(
        isinstance(outcome, RetryablePaymentConflictException)
        and outcome.code == "E008"
        and (outcome.details or {}).get("retryable") is True,
        f"exhaustion ended {outcome!r}, not the retryable 409/E008",
    )
    require_target(evidence["states"] == [] and evidence["envelopes"] == [], f"the conflict left a row: {evidence}")
    require_target(debts_after_conflict == {(p["X"].id, p["Y"].id): Decimal("5.00000000")}, f"{debts_after_conflict}")
    require_target(getattr(again, "status", None) == "COMMITTED", f"the resubmission answered {again!r}")


# ── the simulator money phase (the staged payment propagates, the phase replays) ─────────────────


def _tick_stand(stand, eq, p, monkeypatch):
    from tests.integration.test_p015_p1_money_replay_postgres import _install, _record_plans, _runner, _Sse
    from app.core.simulator.models import RunRecord

    run = RunRecord(run_id=f"p019-t1909-{uuid.uuid4().hex[:8]}", scenario_id="p019-t1909", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1
    run.sim_time_ms = 1_000
    run.intensity_percent = 100
    run._real_seeded = True
    run._real_participants = [(p["S"].id, p["S"].pid), (p["R"].id, p["R"].pid)]
    run._real_equivalents = [eq.code]
    run._real_viz_by_eq = {}
    run._edges_by_equivalent = {}
    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": p["S"].pid}, {"id": p["R"].pid}],
        "trustlines": [
            {"from": p["R"].pid, "to": p["S"].pid, "equivalent": eq.code, "limit": "100.00", "status": "active"}
        ],
        "behaviorProfiles": [],
    }
    sse = _Sse()
    runner = _runner(run, scenario, sse)
    _install(monkeypatch, stand)
    plans = _record_plans(monkeypatch, runner)

    competitor: list[int] = []
    original = runner._load_debt_snapshot_by_pid

    async def snapshot_then_competitor(session, participants, equivalents):
        snapshot = await original(session, participants, equivalents)
        if not competitor:
            competitor.append(1)
            await _blind_insert(stand, debtor=p["S"], creditor=p["R"], equivalent=eq)
        return snapshot

    monkeypatch.setattr(runner, "_load_debt_snapshot_by_pid", snapshot_then_competitor)
    return run, runner, sse, plans, competitor


async def _run_transactions(stand, p) -> list[str]:
    async with stand() as s:
        return list(
            (await s.execute(select(Transaction.state).where(Transaction.initiator_id.in_([p["S"].id, p["R"].id])))).scalars()
        )


@target_xfail(_FIX, "the money phase replays a 23505 on the debt pair instead of recording the payment E010")
@pytest.mark.asyncio
async def test_the_money_phase_replays_a_concurrent_insert_of_a_staged_debt_row(stand, monkeypatch, caplog) -> None:
    eq, p = await _people(stand, "RS", "SR", [("R", "S")])
    refusals = _record_payment_refusals(monkeypatch)
    run, runner, sse, plans, competitor = _tick_stand(stand, eq, p, monkeypatch)
    try:
        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)
    finally:
        PaymentRouter.invalidate_cache(eq.code)

    assert competitor == [1] and plans, (competitor, plans)
    assert (("23505", DEBT_PAIR)) in refusals, f"the staged payment never met 23505 on {DEBT_PAIR}: {refusals}"

    states = await _run_transactions(stand, p)
    debts = await _debts(stand, eq)
    replanned = sum(Decimal(a.amount) for a in plans[-1])
    require_target(len(plans) == 2 and run._real_money_replays_total == 1, f"no replay: plans={len(plans)}")
    require_target(states == ["COMMITTED"] * len(plans[-1]), f"the phase stored {states}")
    require_target(
        debts == {(p["S"].id, p["R"].id): COMPETITOR + replanned}, f"expected {COMPETITOR}+{replanned}, got {debts}"
    )
    require_target(
        (run.errors_total, sse.published("tx.failed"), sse.published("tx.updated")) == (0, 0, len(plans[-1])),
        f"errors={run.errors_total} failed={sse.published('tx.failed')} updated={sse.published('tx.updated')}",
    )


@target_xfail(_FIX, "an exhausted money phase is a conflict tick, not a stored E010 payment")
@pytest.mark.asyncio
async def test_an_exhausted_money_phase_keeps_the_conflict_contract(stand, monkeypatch) -> None:
    eq, p = await _people(stand, "RE", "SR", [("R", "S")])
    refusals = _record_payment_refusals(monkeypatch)
    run, runner, sse, plans, competitor = _tick_stand(stand, eq, p, monkeypatch)
    runner._real_money_replay_attempts_limit = 1
    try:
        await asyncio.wait_for(runner.tick_real_mode(run.run_id), timeout=90.0)
    finally:
        PaymentRouter.invalidate_cache(eq.code)

    assert competitor == [1] and plans, (competitor, plans)
    assert (("23505", DEBT_PAIR)) in refusals, f"the staged payment never met 23505 on {DEBT_PAIR}: {refusals}"

    states = await _run_transactions(stand, p)
    debts = await _debts(stand, eq)
    require_target(states == [], f"the exhausted phase stored {states}")
    require_target(debts == {(p["S"].id, p["R"].id): COMPETITOR}, f"{debts}")
    require_target(
        (run.errors_total, (run.last_error or {}).get("code"), run._real_money_replay_exhausted_total)
        == (0, "REAL_MODE_MONEY_CONFLICT_UNRESOLVED", 1),
        f"errors={run.errors_total} last_error={run.last_error} exhausted={run._real_money_replay_exhausted_total}",
    )


# ── the inject (one transient retry, then the event stays pending) ──────────────────────────────


def _inject_stand(eq, people, effects):
    from app.core.simulator.models import RunRecord
    from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import _Artifacts, _runner

    scenario = {
        "equivalents": [eq.code],
        "participants": [{"id": x.pid} for x in people],
        "trustlines": [],
        "behaviorProfiles": [],
        "events": [{"type": "inject", "time": 0, "effects": effects}],
    }
    run = RunRecord(run_id=f"p019-t1909-inj-{uuid.uuid4().hex[:8]}", scenario_id="p019-t1909", mode="real", state="running")
    run.seed = 7
    run.tick_index = 1
    run.sim_time_ms = 1_000
    run.intensity_percent = 0
    run._real_seeded = True
    run._real_participants = [(x.id, x.pid) for x in people]
    run._real_equivalents = [eq.code]
    run._edges_by_equivalent = {}
    run._real_viz_by_eq = {}
    artifacts = _Artifacts()
    return _runner(run, scenario, artifacts), run, scenario, artifacts


def _park_each_staging(monkeypatch, competitors):
    """Before staging attempt k, run competitor k (if any) - after the attempt's snapshot, before its write."""

    from app.core.simulator.inject_executor import InjectExecutor

    attempts: list[int] = []
    original = InjectExecutor.stage_inject_event

    async def compete_then_stage(self, session, **kwargs):
        attempts.append(1)
        if len(attempts) <= len(competitors):
            await competitors[len(attempts) - 1]()
        return await original(self, session, **kwargs)

    monkeypatch.setattr(InjectExecutor, "stage_inject_event", compete_then_stage)
    return attempts


def _record_inject_errors(monkeypatch) -> list[tuple[str | None, str | None]]:
    seen: list[tuple[str | None, str | None]] = []
    original = real_runner_impl._is_transient_inject_db_error

    def recording(exc):
        seen.append((payment_service_module._payment_db_sqlstate(exc), payment_service_module._constraint_name(exc)))
        return original(exc)

    monkeypatch.setattr(real_runner_impl, "_is_transient_inject_db_error", recording)
    return seen


@target_xfail(_FIX, "the inject retries a 23505 on the debt pair instead of dropping the event as a db error")
@pytest.mark.asyncio
async def test_the_inject_retries_a_concurrent_insert_of_its_debt_row(stand, monkeypatch) -> None:
    eq, p = await _people(stand, "RI", "XY", [("X", "Y")])  # X trusts Y: Y may owe X
    errors = _record_inject_errors(monkeypatch)
    effects = [{"op": "inject_debt", "from": p["X"].pid, "to": p["Y"].pid, "equivalent": eq.code, "amount": "10.00"}]
    runner, run, scenario, artifacts = _inject_stand(eq, [p["X"], p["Y"]], effects)
    attempts = _park_each_staging(
        monkeypatch, [lambda: _blind_insert(stand, debtor=p["Y"], creditor=p["X"], equivalent=eq)]
    )
    try:
        async with stand() as session:
            await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)
    finally:
        PaymentRouter.invalidate_cache(eq.code)

    assert attempts, "staging never ran"
    assert (("23505", DEBT_PAIR)) in errors, f"the inject never met 23505 on {DEBT_PAIR}: {errors}"

    debts = await _debts(stand, eq)
    notes = [e.get("description") for e in artifacts.events]
    require_target(len(attempts) == 2, f"staging ran {len(attempts)} time(s); notes {notes}")
    require_target(debts == {(p["Y"].id, p["X"].id): Decimal("15.00000000")}, f"final debts {debts}; notes {notes}")
    require_target(
        0 in run._real_fired_scenario_event_indexes and "inject failed (db error)" not in notes,
        f"the event was not applied: fired={run._real_fired_scenario_event_indexes} notes={notes}",
    )


@target_xfail(_FIX, "an inject that meets the collision twice stays pending instead of being dropped")
@pytest.mark.asyncio
async def test_an_inject_exhausted_by_the_collision_stays_pending(stand, monkeypatch) -> None:
    eq, p = await _people(stand, "RJ", "XYZ", [("X", "Y"), ("X", "Z")])
    errors = _record_inject_errors(monkeypatch)
    effects = [
        {"op": "inject_debt", "from": p["X"].pid, "to": p["Y"].pid, "equivalent": eq.code, "amount": "10.00"},
        {"op": "inject_debt", "from": p["X"].pid, "to": p["Z"].pid, "equivalent": eq.code, "amount": "10.00"},
    ]
    runner, run, scenario, artifacts = _inject_stand(eq, [p["X"], p["Y"], p["Z"]], effects)
    # Attempt 1 collides on X-Y; attempt 2 (fresh snapshot: X-Y exists) collides on X-Z.
    attempts = _park_each_staging(
        monkeypatch,
        [
            lambda: _blind_insert(stand, debtor=p["Y"], creditor=p["X"], equivalent=eq),
            lambda: _blind_insert(stand, debtor=p["Z"], creditor=p["X"], equivalent=eq),
        ],
    )
    raised: BaseException | None = None
    try:
        async with stand() as session:
            await runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)
    except Exception as exc:  # noqa: BLE001 - compared below
        raised = exc
    finally:
        PaymentRouter.invalidate_cache(eq.code)

    assert attempts, "staging never ran"
    assert (("23505", DEBT_PAIR)) in errors, f"the inject never met 23505 on {DEBT_PAIR}: {errors}"

    debts = await _debts(stand, eq)
    async with stand() as s:
        envelopes = int(
            await s.scalar(
                select(func.count()).select_from(debt_operations).where(
                    debt_operations.c.kind == "INJECT", debt_operations.c.identity == f"{run.run_id}:0"
                )
            )
        )
    notes = [e.get("description") for e in artifacts.events]
    require_target(len(attempts) == 2 and raised is not None, f"attempts={len(attempts)} raised={raised!r} notes={notes}")
    require_target(0 not in run._real_fired_scenario_event_indexes, f"the event was consumed: notes={notes}")
    require_target(
        debts == {(p["Y"].id, p["X"].id): COMPETITOR, (p["Z"].id, p["X"].id): COMPETITOR} and envelopes == 0,
        f"only the competitors' rows may exist: {debts}, inject envelopes {envelopes}",
    )


# ── the counter-check: another 23505 is NOT retried (anti-vacuum, AGENTS.md §9) ─────────────────


@target_xfail(_FIX, "the classifiers recognise the debt-pair collision")
def test_only_the_debt_pair_constraint_is_a_retryable_collision() -> None:
    """Built on the real driver's error type: the same SQLSTATE on any other constraint stays terminal."""

    from asyncpg.exceptions import UniqueViolationError
    from sqlalchemy.exc import IntegrityError

    def integrity(constraint: str | None) -> IntegrityError:
        driver = UniqueViolationError("duplicate key value violates unique constraint")
        driver.sqlstate = "23505"
        driver.constraint_name = constraint
        return IntegrityError("INSERT ...", {}, driver)

    classify = payment_service_module._classify_payment_db_error
    for other in ("transactions_tx_id_key", "uq_debt_operations_tx_id", "uq_prepare_locks_tx_participant", None):
        assert not isinstance(classify(integrity(other)), RetryablePaymentConflictException), other
        assert not real_runner_impl._is_transient_inject_db_error(integrity(other)), other
    require_target(
        isinstance(classify(integrity(DEBT_PAIR)), RetryablePaymentConflictException)
        and real_runner_impl._is_transient_inject_db_error(integrity(DEBT_PAIR)),
        "the debt-pair collision is not classified as a retryable conflict",
    )
