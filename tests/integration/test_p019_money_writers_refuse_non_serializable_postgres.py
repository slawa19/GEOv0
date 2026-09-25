"""Programme 019 stage 5 (`T1907`, `FORK-2`; spec, Verification plan §1 №7): every writer whose invariant
leans on SERIALIZABLE refuses a transaction that is not, BEFORE its first write.

WHY. Stage 5 may remove the advisory coordination only if every invariant-relevant money writer runs at
SERIALIZABLE (spec, "Изоляция, писатели и клиринг", item 2): the inject's one-direction-per-pair check
(`book.py`, `_apply_inject_increase`), the payment's capacity read, the clearing's cycle re-read and the
trust decay's floor all hold under concurrency only because every participant is in SSI. Since
2026-09-25 the application's own engine cannot be configured below SERIALIZABLE (`app/config.py`), so
what is left is a SESSION HANDED IN BY A CALLER at another level - the staged tick, the clearing's
caller, a script, a test. The check reads the ACTUAL level of the work transaction (`SHOW
transaction_isolation`) and refuses; it never commits or rolls back a caller's transaction to "upgrade"
it (FORK-2).

THE STAND. A disposable clone; its SERIALIZABLE sessionmaker seeds and observes; a second engine on the
same clone ASKS for READ COMMITTED (`poolclass=NullPool`), and every case first proves its session really
is at READ COMMITTED - otherwise a green here would say nothing. The target is read from the refusal's
`details.reason` (`isolation_not_serializable`), never from an import, so the file collects on the tree
before the check exists and fails there with `TargetMismatch` (the 019 shape, `tests/p019_support.py`).

WHAT "CALLER UNTOUCHED" MEANS PER WRITER. Where the writer runs inside the caller's transaction (the
staged payment, the trust decay, the admin endpoints before their own commit), the caller's transaction
id is read before and after: the same id means the transaction was neither committed nor rolled back.
The clearing, the inject and the trust growth OWN their unit of work - they end every attempt themselves,
by design (`09-decisions-and-defaults.md` §1.12.1) - so for them the assertion is that nothing of theirs
was committed.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from app.core.payments.router import PaymentRouter
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from tests.integration.p019_interlock_support import _seed_interlock_case
from tests.p019_support import require_target, target_xfail

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture

_REASON = "isolation_not_serializable"
_FIXED_BY = "stage 5 (T1907)"


@pytest.fixture
async def read_committed(committed_database):
    engine = create_async_engine(
        committed_database.url, isolation_level="READ COMMITTED", poolclass=NullPool
    )
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


async def _at_read_committed(session) -> int:
    """Control: the session's transaction really runs READ COMMITTED; returns its transaction id."""

    level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    assert str(level).lower() == "read committed", f"stand: not at READ COMMITTED: {level}"
    return int(await session.scalar(text("SELECT txid_current()")))


def _refused(outcome) -> bool:
    return isinstance(outcome, Exception) and (getattr(outcome, "details", None) or {}).get(
        "reason"
    ) == _REASON


async def _call(awaitable):
    try:
        return await awaitable
    except Exception as exc:  # noqa: BLE001 - the outcome is what the test compares
        return exc


async def _state(committed_database, seed) -> dict:
    async with committed_database.sessionmaker() as s:
        debts = {
            (debt.debtor_id, debt.creditor_id): Decimal(str(debt.amount))
            for debt in (
                await s.scalars(select(Debt).where(Debt.equivalent_id == seed["equivalent_id"]))
            ).all()
        }
        transactions = (
            await s.execute(
                select(Transaction.type, Transaction.state).where(
                    Transaction.initiator_id.in_(seed["participant_ids"])
                )
            )
        ).all()
        limits = {
            (line.from_participant_id, line.to_participant_id): Decimal(str(line.limit))
            for line in (
                await s.scalars(
                    select(TrustLine).where(TrustLine.equivalent_id == seed["equivalent_id"])
                )
            ).all()
        }
        equivalent = (
            await s.execute(
                select(Equivalent.is_active).where(Equivalent.id == seed["equivalent_id"])
            )
        ).scalar_one()
    return {
        "debts": debts,
        "transactions": sorted(tuple(row) for row in transactions),
        "limits": limits,
        "is_active": equivalent,
    }


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("t", 1)})


async def _run_writer(name: str, session, seed, committed_database):
    """Call writer `name` on `session`; returns its outcome (a value or the exception)."""

    a_id, b_id, _c_id = seed["participant_ids"]
    a_pid, b_pid, _c_pid = seed["participant_pids"]
    code = seed["equivalent_code"]

    if name == "payment_staged":
        from app.core.payments.service import PaymentService

        return await _call(
            PaymentService(session).create_payment_internal_staged(
                a_id, to_pid=b_pid, equivalent=code, amount="10.00"
            )
        )
    if name == "clearing":
        from app.core.clearing.service import ClearingService

        return await _call(ClearingService(session).execute_clearing_with_amount(seed["cycle"]))
    if name == "inject":
        from tests.integration.test_p015_inject_holds_the_owner_lock_postgres import (
            _Artifacts,
            _runner,
        )
        from app.core.simulator.models import RunRecord

        scenario = {
            "equivalents": [code],
            "participants": [{"id": pid} for pid in seed["participant_pids"]],
            "trustlines": [],
            "behaviorProfiles": [],
            "events": [
                {
                    "type": "inject",
                    "time": 0,
                    "effects": [
                        # creditor B, debtor A: A->B grows from 100 to 105 under B's 200 line.
                        {"op": "inject_debt", "from": b_pid, "to": a_pid, "equivalent": code, "amount": "5.00"}
                    ],
                }
            ],
        }
        run = RunRecord(run_id=f"p019-iso-{uuid.uuid4().hex[:8]}", scenario_id="p019-iso", mode="real", state="running")
        run.seed = 7
        run.tick_index = 1
        run.sim_time_ms = 1_000
        run.intensity_percent = 0
        run._real_seeded = True
        run._real_participants = list(zip(seed["participant_ids"], seed["participant_pids"]))
        run._real_equivalents = [code]
        run._edges_by_equivalent = {}
        run._real_viz_by_eq = {}
        runner = _runner(run, scenario, _Artifacts())
        await session.rollback()  # the inject owns its session's transactions; it starts clean
        return await _call(
            runner._apply_due_scenario_events(session, run_id=run.run_id, run=run, scenario=scenario)
        )
    if name in {"trust_decay", "trust_growth"}:
        from app.core.simulator.models import EdgeClearingHistory, RunRecord, TrustDriftConfig
        from app.core.simulator.trust_drift_engine import TrustDriftEngine

        run = RunRecord(run_id=f"p019-td-{uuid.uuid4().hex[:8]}", scenario_id="p019-td", mode="real", state="running")
        run._real_participants = list(zip(seed["participant_ids"], seed["participant_pids"]))
        run._trust_drift_config = TrustDriftConfig(
            enabled=True, growth_rate=0.1, decay_rate=0.02, max_growth=2.0,
            min_limit_ratio=0.3, overload_threshold=0.4,
        )
        # creditor B -> debtor A: limit 200, debt A->B 100 (ratio 0.5 >= 0.4, so the decay acts).
        run._edge_clearing_history = {
            f"{b_pid}:{a_pid}:{code}": EdgeClearingHistory(original_limit=Decimal("200.00"))
        }
        scenario = {
            "equivalents": [code],
            "trustlines": [{"from": b_pid, "to": a_pid, "equivalent": code, "limit": "200.00", "status": "active"}],
        }
        run._scenario_raw = scenario
        engine = TrustDriftEngine(
            sse=None, utc_now=None, logger=logging.getLogger("tests.p019.iso"),
            get_scenario_raw=lambda _s: scenario,
        )
        if name == "trust_decay":
            return await _call(
                engine.apply_trust_decay(run, session, 7, {(a_pid, b_pid, code): Decimal("100.00")}, scenario)
            )
        return await _call(
            engine.apply_trust_growth(run, session, {(b_pid, a_pid)}, code, 7, {(b_pid, a_pid): 30.0})
        )
    if name == "admin_patch":
        from app.api.v1.admin import admin_update_equivalent
        from app.schemas.admin import AdminEquivalentUpdateRequest

        return await _call(
            admin_update_equivalent(
                code, AdminEquivalentUpdateRequest(is_active=False, reason="p019 iso"), _request(), db=session
            )
        )
    if name == "admin_hold_clear":
        from app.api.v1.admin import admin_clear_equivalent_integrity_hold
        from app.schemas.admin import AdminEquivalentIntegrityHoldClearRequest

        return await _call(
            admin_clear_equivalent_integrity_hold(
                code, AdminEquivalentIntegrityHoldClearRequest(reason="p019 iso"), _request(), db=session
            )
        )
    if name == "admin_delete":
        from app.api.v1.admin import admin_delete_equivalent
        from app.schemas.admin import AdminEquivalentDeleteRequest

        return await _call(
            admin_delete_equivalent(code, AdminEquivalentDeleteRequest(reason="p019 iso"), _request(), db=session)
        )
    raise AssertionError(f"unknown writer {name}")


# Writers that run INSIDE the caller's transaction: the caller's transaction id must survive the call.
_INSIDE_CALLER = {"payment_staged", "trust_decay", "admin_patch", "admin_hold_clear", "admin_delete"}

_WRITERS = [
    "payment_staged",
    "clearing",
    "inject",
    "trust_decay",
    "trust_growth",
    "admin_patch",
    "admin_hold_clear",
    "admin_delete",
]


@target_xfail(_FIXED_BY, "a money writer on a non-SERIALIZABLE transaction refuses before its first write")
@pytest.mark.asyncio
@pytest.mark.parametrize("writer", _WRITERS)
async def test_a_money_writer_refuses_a_read_committed_transaction(
    writer, read_committed, committed_database
) -> None:
    seed = await _seed_interlock_case()
    before = await _state(committed_database, seed)
    try:
        async with read_committed() as session:
            txid = await _at_read_committed(session)
            outcome = await _run_writer(writer, session, seed, committed_database)
            same_transaction = None
            if writer in _INSIDE_CALLER and session.in_transaction():
                same_transaction = int(await session.scalar(text("SELECT txid_current()"))) == txid
            await session.rollback()
    finally:
        PaymentRouter.invalidate_cache(seed["equivalent_code"])
    after = await _state(committed_database, seed)

    # The refusal must be the ISOLATION refusal, before any write, with the caller untouched.
    require_target(
        _refused(outcome),
        f"{writer} ran on a READ COMMITTED transaction: outcome {outcome!r}",
    )
    assert after == before, f"{writer} refused but changed committed state: {before} -> {after}"
    if writer in _INSIDE_CALLER:
        assert same_transaction is True, (
            f"{writer} committed or rolled back the caller's transaction on its way to the refusal"
        )


@target_xfail(_FIXED_BY, "pay() handed a READ COMMITTED session factory refuses and records nothing")
@pytest.mark.asyncio
async def test_the_api_pay_refuses_a_read_committed_session_factory(read_committed, committed_database) -> None:
    """`pay()` opens its own sessions; handed a READ COMMITTED factory it refuses and records nothing."""

    from app.core.payments.service import PaymentService
    from app.schemas.payment import PaymentCreateRequest

    seed = await _seed_interlock_case()
    before = await _state(committed_database, seed)
    async with read_committed() as probe:
        await _at_read_committed(probe)
    request = PaymentCreateRequest(
        tx_id=str(uuid.uuid4()), to=seed["participant_pids"][1], equivalent=seed["equivalent_code"],
        amount="10.00", signature="__internal__",
    )
    try:
        outcome = await _call(
            PaymentService.pay(read_committed, seed["participant_ids"][0], request, require_signature=False)
        )
    finally:
        PaymentRouter.invalidate_cache(seed["equivalent_code"])
    after = await _state(committed_database, seed)
    require_target(_refused(outcome), f"pay() ran at READ COMMITTED: {outcome!r}")
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["payment_staged", "clearing", "trust_decay"])
async def test_the_same_writer_runs_at_serializable(writer, committed_database) -> None:
    """COUNTER-CHECK (anti-vacuum): the refusal is about the level, not the writer - on a SERIALIZABLE
    transaction the same call does its work (a payment staged, 30 cleared, a limit decayed)."""

    seed = await _seed_interlock_case()
    try:
        async with committed_database.sessionmaker() as session:
            level = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
            assert str(level).lower() == "serializable", level
            outcome = await _run_writer(writer, session, seed, committed_database)
            if session.in_transaction():
                await session.commit()
    finally:
        PaymentRouter.invalidate_cache(seed["equivalent_code"])
    assert not isinstance(outcome, Exception), repr(outcome)
    after = await _state(committed_database, seed)
    a_id, b_id, _c = seed["participant_ids"]
    if writer == "payment_staged":
        assert outcome.result.status == "COMMITTED", outcome
        assert after["debts"][(a_id, b_id)] == Decimal("110.00000000"), after["debts"]
    elif writer == "clearing":
        assert outcome == Decimal("30.00000000"), outcome
    else:
        assert outcome.updated_count == 1, outcome
        assert after["limits"][(b_id, a_id)] == Decimal("196.00000000"), after["limits"]
