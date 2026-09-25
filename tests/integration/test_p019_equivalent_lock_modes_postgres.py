"""Programme 019 stage 5 (`T1909`): the ONE equivalent lock in its TWO modes, on real PostgreSQL concurrency.

Decision `KEEP-EQUIVALENT-LOCK` (fourth consultation, 2026-09-25): payments, staged money phases and injects
take the equivalent's advisory lock SHARED (`pg_advisory_xact_lock_shared`); the clearing takes it EXCLUSIVE at
session level (`pg_advisory_lock`) on a pinned connection, before its authoritative snapshot, and releases it
(or invalidates the connection) at the end. The consultation asked for four checks; each test below is one:

1. shared/shared - two shared holders are GRANTED together, and two real API payments over different pairs
   of one equivalent overlap and both commit;
2. shared/exclusive - a shared holder makes the exclusive request wait, and an exclusive holder makes a new
   shared request wait; the waiter and its blocker are named by `pg_locks`/`pg_blocking_pids`;
3. fresh-after-wait - a clearing that waited for a payment's shared lock sees that payment's commit in its
   first attempt: the serial result "payment first", with NO 40001 retry;
4. cleanup - after a clearing that succeeded, was refused, failed or was cancelled, no advisory lock of the
   key remains on any backend; an unlock that is not confirmed invalidates the connection.

AGAINST A VACUOUS GREEN (AGENTS §9, §11). Each test asserts its MECHANISM from `pg_locks` before its result:
that the lock was really held in the mode claimed, that the waiter really waited (a NOT granted entry of the
key whose blocker is the holder) or really did not. Task timing alone is never the evidence.

MUTATIONS these must fail under (recorded in the `T1909` changelog): the shared primitive taking
`pg_advisory_xact_lock` (exclusive) - tests 1 red; the clearing's lock taken shared - tests 2 and 3 red.

What these do NOT show: the liveness of the clearing under continuous payment load (the starvation probe
`tests/integration/test_p019_t1908_clearing_starvation_probe_postgres.py`), and the SERIALIZABLE outcomes of
payments that DO race on one pair (`tests/integration/test_p019_t1908_lock_removal_experiments_postgres.py`,
`tests/integration/test_payment_inverse_multisegment_postgres.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.clearing.service import ClearingService
from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE, MoneyBoundary
from app.core.payments import service as payment_service_module
from app.core.payments.router import PaymentRouter
from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine
from app.schemas.payment import PaymentCreateRequest
from app.utils.exceptions import ConflictException, GeoException
from tests.integration.p019_interlock_support import _seed_interlock_case

# MODE B: every commit lands in a clone dropped after the test (`tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture


@dataclass
class Stand:
    engine: AsyncEngine
    sessions: async_sessionmaker
    invalidations: list[str]

    @contextlib.asynccontextmanager
    async def pinned(self):
        """A session on ONE physical connection for its whole life - what a session-level lock needs (an
        engine-bound session hands its connection back to the pool at every commit/rollback)."""

        async with self.engine.connect() as connection:
            session = AsyncSession(bind=connection, expire_on_commit=False, autoflush=False)
            try:
                yield session
            finally:
                await _unlock_all(session)
                await session.close()


@pytest_asyncio.fixture
async def stand(committed_database):
    """A SERIALIZABLE engine over the clone, with every pool invalidation recorded."""

    engine = create_async_engine(
        committed_database.url, pool_size=12, max_overflow=0, pool_timeout=20, isolation_level="SERIALIZABLE"
    )
    invalidations: list[str] = []

    def on_invalidate(_dbapi_connection, _record, exception):
        invalidations.append(repr(exception))

    event.listen(engine.sync_engine, "invalidate", on_invalidate)
    try:
        yield Stand(
            engine,
            async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False),
            invalidations,
        )
    finally:
        await engine.dispose()


@pytest.fixture
def generous_budgets(monkeypatch):
    """Parked holders must outlive nothing but the schedule; the budgets are not the subject here."""

    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)


# ── observation of the one lock, from pg_locks ─────────────────────────────────────────────────


def _oid(value: int) -> int:
    """A signed int4 advisory key as `pg_locks` shows it (an `oid`, unsigned 32-bit)."""

    return value & 0xFFFFFFFF


async def lock_entries(sessions, equivalent_id) -> set[tuple[int, str, bool]]:
    """(pid, mode, granted) of every lock entry of THIS equivalent's key on this database, any backend."""

    async with sessions() as observer:
        rows = (
            await observer.execute(
                text(
                    "SELECT pid, mode, granted FROM pg_locks WHERE locktype = 'advisory' AND objsubid = 2 "
                    "AND classid::bigint = :namespace AND objid::bigint = :key "
                    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
                ),
                {
                    "namespace": _oid(_EQUIVALENT_OWNER_LOCK_NAMESPACE),
                    "key": _oid(MoneyBoundary._equivalent_owner_lock_key(equivalent_id)),
                },
            )
        ).all()
    return {(int(pid), str(mode), bool(granted)) for pid, mode, granted in rows}


async def blocking_pids(sessions, pid: int) -> set[int]:
    async with sessions() as observer:
        blockers = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid})
    return {int(p) for p in (blockers or [])}


async def wait_for_entry(
    sessions, equivalent_id, entry: tuple[int, str, bool], *, unless: asyncio.Task | None = None, timeout=5.0
) -> bool:
    """True once `entry` is in `pg_locks` for the key; False on timeout or once `unless` has finished."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if entry in await lock_entries(sessions, equivalent_id):
            return True
        if unless is not None and unless.done():
            return False
        await asyncio.sleep(0.02)
    return False


async def no_entry_left(sessions, equivalent_id, timeout=5.0) -> set[tuple[int, str, bool]]:
    """The key's entries after they had `timeout` to go (a terminated backend's locks go asynchronously)."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        entries = await lock_entries(sessions, equivalent_id)
        if not entries or loop.time() >= deadline:
            return entries
        await asyncio.sleep(0.05)


async def backend_alive(sessions, pid: int) -> bool:
    async with sessions() as observer:
        return bool(
            await observer.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid = :pid)"), {"pid": pid})
        )


async def _pid(session) -> int:
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


async def _finish(*tasks) -> None:
    for task in tasks:
        if task is not None and not task.done():
            await asyncio.wait([task], timeout=20)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=5)


async def _unlock_all(session) -> None:
    """A session-level lock outlives a rollback: never return a pooled connection holding one."""

    await session.rollback()
    await session.execute(text("SELECT pg_advisory_unlock_all()"))
    await session.rollback()


# ── (1) shared / shared ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_shared_holders_of_one_equivalent_are_granted_together(stand) -> None:
    """MECHANISM and RESULT are one here: both backends appear in `pg_locks` with a GRANTED `ShareLock` on
    the same key at the same time. MUTATION (exclusive in the shared primitive): the second request waits and
    fails on its lock budget - red."""

    equivalent_id = uuid.uuid4()
    async with stand.sessions() as first, stand.sessions() as second:
        pids = (await _pid(first), await _pid(second))
        try:
            await MoneyBoundary(first)._acquire_shared_equivalent_locks_in_order([equivalent_id])
            second_boundary = MoneyBoundary(second)
            second_boundary._advisory_lock_budget_s = 1.0
            try:
                await second_boundary._acquire_shared_equivalent_locks_in_order([equivalent_id])
            except DBAPIError as exc:
                pytest.fail(f"the second shared request waited for the first and gave up: {exc.orig!r}")
            entries = await lock_entries(stand.sessions, equivalent_id)
            assert entries == {(pids[0], "ShareLock", True), (pids[1], "ShareLock", True)}, entries
        finally:
            await first.rollback()
            await second.rollback()
    # Transaction level: the ends of both transactions took both entries with them.
    assert await lock_entries(stand.sessions, equivalent_id) == set()


async def _seed_two_pairs(stand) -> dict:
    """One equivalent; B trusts A and D trusts C for 100.00 - two DISJOINT pairs, no debt."""

    n = uuid.uuid4().hex[:8].upper()
    async with stand.sessions() as s:
        eq = Equivalent(code=f"LM{n}"[:16], precision=2, is_active=True)
        people = {
            k: Participant(pid=f"{k}_LM_{n}", display_name=k, public_key=f"pk_{k}_lm_{n}", type="person", status="active")
            for k in "ABCD"
        }
        s.add_all([eq, *people.values()])
        await s.flush()
        s.add_all(
            [
                TrustLine(from_participant_id=people["B"].id, to_participant_id=people["A"].id,
                          equivalent_id=eq.id, limit=Decimal("100.00"), status="active"),
                TrustLine(from_participant_id=people["D"].id, to_participant_id=people["C"].id,
                          equivalent_id=eq.id, limit=Decimal("100.00"), status="active"),
            ]
        )
        await s.commit()
    PaymentRouter.invalidate_cache(eq.code)
    return {"eq": eq, "people": people}


@pytest.mark.asyncio
async def test_two_api_payments_over_different_pairs_of_one_equivalent_overlap_and_both_commit(
    stand, monkeypatch, generous_budgets
) -> None:
    """A pays B 10 and C pays D 7 in ONE equivalent, through `PaymentService.pay` (the API path's owner).

    The first is PARKED in its money phase, after its pre-state read, holding its shared lock. MECHANISM:
    the second reaches the same point while the first is parked, and at that moment `pg_locks` shows BOTH
    backends holding the key granted `ShareLock`; the second then COMMITS while the first is still parked.
    RESULT: both `COMMITTED`, the debts are exactly A->B 10 and C->D 7, one `PAYMENT` audit row each.
    MUTATION (exclusive in the shared primitive): the second never reaches its money phase while the first
    is parked - red.
    """

    seed = await _seed_two_pairs(stand)
    eq, people = seed["eq"], seed["people"]
    first_pair = (people["A"].id, people["B"].id)
    parked, release = asyncio.Event(), asyncio.Event()
    second_read = asyncio.Event()
    pids: dict[str, int] = {}
    holders_while_both_in: list[set] = []
    original_prestate = payment_service_module._read_payment_prestate

    async def prestate(session, declared_flows):
        result = await original_prestate(session, declared_flows)
        is_first = {(f.from_id, f.to_id) for f in declared_flows} == {first_pair}
        if is_first and not parked.is_set():
            pids["first"] = await _pid(session)
            parked.set()
            await release.wait()
        elif not is_first and not second_read.is_set():
            pids["second"] = await _pid(session)
            holders_while_both_in.append(await lock_entries(stand.sessions, eq.id))
            second_read.set()
        return result

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", prestate)

    def request(to, amount):
        return PaymentCreateRequest(
            tx_id=str(uuid.uuid4()), to=to.pid, equivalent=eq.code, amount=amount, signature="__internal__"
        )

    first_request, second_request = request(people["B"], "10.00"), request(people["D"], "7.00")
    first_task = second_task = None
    try:
        first_task = asyncio.create_task(
            PaymentService.pay(stand.sessions, people["A"].id, first_request, require_signature=False)
        )
        await asyncio.wait_for(parked.wait(), timeout=20)
        second_task = asyncio.create_task(
            PaymentService.pay(stand.sessions, people["C"].id, second_request, require_signature=False)
        )
        try:
            await asyncio.wait_for(second_read.wait(), timeout=10)
        except asyncio.TimeoutError:
            pytest.fail("the second payment did not get past its equivalent lock while the first held it shared")
        assert holders_while_both_in == [
            {(pids["first"], "ShareLock", True), (pids["second"], "ShareLock", True)}
        ], holders_while_both_in
        second = await asyncio.wait_for(second_task, timeout=20)
        assert not first_task.done(), "the first payment must still be parked when the second commits"
        release.set()
        first = await asyncio.wait_for(first_task, timeout=30)
    finally:
        release.set()
        await _finish(first_task, second_task)
        PaymentRouter.invalidate_cache(eq.code)

    assert (first.status, second.status) == ("COMMITTED", "COMMITTED"), (first, second)
    tx_ids = [first_request.tx_id, second_request.tx_id]
    async with stand.sessions() as s:
        states = dict((await s.execute(select(Transaction.tx_id, Transaction.state).where(Transaction.tx_id.in_(tx_ids)))).all())
        debts = {
            (d.debtor_id, d.creditor_id): d.amount
            for d in (await s.scalars(select(Debt).where(Debt.equivalent_id == eq.id))).all()
        }
        audits = sorted(
            (a.tx_id, a.operation_type, a.verification_passed)
            for a in (await s.scalars(select(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids)))).all()
        )
    assert states == {tx_id: "COMMITTED" for tx_id in tx_ids}, states
    assert debts == {
        (people["A"].id, people["B"].id): Decimal("10.00000000"),
        (people["C"].id, people["D"].id): Decimal("7.00000000"),
    }, debts
    assert audits == sorted((tx_id, "PAYMENT", True) for tx_id in tx_ids), audits


# ── (2) shared / exclusive ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_shared_holder_holds_off_the_exclusive_session_lock_and_an_exclusive_holder_holds_off_a_shared_one(
    stand,
) -> None:
    """Both directions of the exclusion, on one key.

    (a) A transaction holds the key SHARED; `acquire_exclusive_equivalent_session_lock` on another backend
    appears as a NOT granted `ExclusiveLock` blocked by exactly that holder, is still pending after the
    holder has held it a while, and is granted when the holder commits.
    (b) A session holds the key EXCLUSIVE - across its own rollback (session level); a new shared request
    appears as a NOT granted `ShareLock` blocked by it, and is granted on the release, which is confirmed.
    MUTATION (the clearing's lock taken shared): (a) does not wait, and (b) does not block - red.
    """

    equivalent_id = uuid.uuid4()
    exclusive_task = shared_task = None
    async with stand.pinned() as holder, stand.pinned() as clearing, stand.pinned() as payer:
        holder_pid, clearing_pid, payer_pid = await _pid(holder), await _pid(clearing), await _pid(payer)
        clearing_boundary = MoneyBoundary(clearing)
        clearing_boundary._advisory_lock_budget_s = 30
        payer_boundary = MoneyBoundary(payer)
        payer_boundary._advisory_lock_budget_s = 30
        try:
            # (a) shared holder -> the exclusive request waits.
            await MoneyBoundary(holder)._acquire_shared_equivalent_locks_in_order([equivalent_id])
            exclusive_task = asyncio.create_task(clearing_boundary.acquire_exclusive_equivalent_session_lock(equivalent_id))
            assert await wait_for_entry(
                stand.sessions, equivalent_id, (clearing_pid, "ExclusiveLock", False), unless=exclusive_task
            ), "the exclusive request did not wait for the shared holder"
            assert await blocking_pids(stand.sessions, clearing_pid) == {holder_pid}
            await asyncio.sleep(0.2)
            assert not exclusive_task.done()
            await holder.commit()
            await asyncio.wait_for(exclusive_task, timeout=5)
            assert await lock_entries(stand.sessions, equivalent_id) == {(clearing_pid, "ExclusiveLock", True)}

            # (b) the exclusive lock is session level: it survives the rollback of its transaction ...
            await clearing.rollback()
            assert await lock_entries(stand.sessions, equivalent_id) == {(clearing_pid, "ExclusiveLock", True)}
            # ... and holds off a new shared request.
            shared_task = asyncio.create_task(payer_boundary._acquire_shared_equivalent_locks_in_order([equivalent_id]))
            assert await wait_for_entry(
                stand.sessions, equivalent_id, (payer_pid, "ShareLock", False), unless=shared_task
            ), "the shared request did not wait for the exclusive holder"
            assert await blocking_pids(stand.sessions, payer_pid) == {clearing_pid}
            await asyncio.sleep(0.2)
            assert not shared_task.done()
            assert await clearing_boundary.release_exclusive_equivalent_session_lock(equivalent_id) is True
            await asyncio.wait_for(shared_task, timeout=5)
            assert await lock_entries(stand.sessions, equivalent_id) == {(payer_pid, "ShareLock", True)}
            await payer.rollback()
            assert await lock_entries(stand.sessions, equivalent_id) == set()
        finally:
            await _finish(exclusive_task, shared_task)


# ── (3) the clearing's snapshot is taken after its wait ──────────────────────────────────────


@dataclass
class ClearingProbe:
    pids: list[int]
    attempts: int = 0
    conflicts: int = 0
    locked_amounts: list[list[Decimal]] | None = None


def probe_clearing(monkeypatch) -> ClearingProbe:
    """Records the clearing's backend (at its exclusive request), its attempts and its retryable conflicts."""

    probe = ClearingProbe(pids=[], locked_amounts=[])
    original_exclusive = MoneyBoundary.acquire_exclusive_equivalent_session_lock

    async def exclusive(self, equivalent_id):
        probe.pids.append(await _pid(self.session))
        return await original_exclusive(self, equivalent_id)

    monkeypatch.setattr(MoneyBoundary, "acquire_exclusive_equivalent_session_lock", exclusive)

    original_attempt = ClearingService._execute_clearing_with_amount

    async def attempt(self, cycle, **kwargs):
        probe.attempts += 1
        return await original_attempt(self, cycle, **kwargs)

    monkeypatch.setattr(ClearingService, "_execute_clearing_with_amount", attempt)

    original_retryable = ClearingService._is_retryable_concurrency_error.__func__

    def retryable(cls, exc):
        answer = original_retryable(cls, exc)
        if answer:
            probe.conflicts += 1
        return answer

    monkeypatch.setattr(ClearingService, "_is_retryable_concurrency_error", classmethod(retryable))

    original_policy = ClearingService._cycle_respects_auto_clearing

    async def policy(self, debts):
        probe.locked_amounts.append(sorted(Decimal(str(d.amount)) for d in debts))
        return await original_policy(self, debts)

    monkeypatch.setattr(ClearingService, "_cycle_respects_auto_clearing", policy)
    return probe


@pytest.mark.asyncio
async def test_a_clearing_that_waited_for_a_payment_sees_its_commit_in_its_first_attempt(
    stand, monkeypatch, generous_budgets
) -> None:
    """Cycle A->B 100, B->C 30, C->A 40. B pays A 80 (it nets A->B down to 20) and is PARKED in its money
    phase holding its shared lock; the clearing of the cycle starts meanwhile.

    MECHANISM: the clearing's backend is a NOT granted `ExclusiveLock` of the key blocked by the payment's
    backend, while the committed A->B is still 100 (read by an observer during the wait).
    RESULT - the serial result "payment first": the clearing clears 20, not 30, in ONE attempt with NO
    retryable conflict (a snapshot taken before the wait would have met the payment's write: 40001 and a
    retry, or the stale 30); it locked the amounts 20/30/40; the final debts are B->C 10, C->A 20.
    MUTATION (the clearing's lock taken shared): the clearing does not wait - red.
    """

    probe = probe_clearing(monkeypatch)
    seed = await _seed_interlock_case()
    a_id, b_id, c_id = seed["participant_ids"]
    parked, release = asyncio.Event(), asyncio.Event()
    payment_pid: list[int] = []
    original_prestate = payment_service_module._read_payment_prestate

    async def prestate(session, declared_flows):
        result = await original_prestate(session, declared_flows)
        if not parked.is_set():
            payment_pid.append(await _pid(session))
            parked.set()
            await release.wait()
        return result

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", prestate)
    request = PaymentCreateRequest(
        tx_id=str(uuid.uuid4()), to=seed["participant_pids"][0], equivalent=seed["equivalent_code"],
        amount="80.00", signature="__internal__",
    )

    async def clear():
        async with stand.sessions() as session:
            return await ClearingService(session).execute_clearing_with_amount(seed["cycle"])

    payment_task = clearing_task = None
    try:
        payment_task = asyncio.create_task(PaymentService.pay(stand.sessions, b_id, request, require_signature=False))
        await asyncio.wait_for(parked.wait(), timeout=20)
        clearing_task = asyncio.create_task(clear())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while not probe.pids and loop.time() < deadline and not clearing_task.done():
            await asyncio.sleep(0.02)
        assert probe.pids, "the clearing never asked for its exclusive lock"
        clearing_pid = probe.pids[0]
        assert await wait_for_entry(
            stand.sessions, seed["equivalent_id"], (clearing_pid, "ExclusiveLock", False), unless=clearing_task
        ), "the clearing did not wait for the payment's shared lock"
        assert await blocking_pids(stand.sessions, clearing_pid) == {payment_pid[0]}
        async with stand.sessions() as observer:
            committed_ab = await observer.scalar(select(Debt.amount).where(Debt.debtor_id == a_id, Debt.creditor_id == b_id))
        assert committed_ab == Decimal("100.00000000"), committed_ab
        assert probe.attempts == 0, "the clearing began an attempt before it had its exclusive lock"
        release.set()
        paid = await asyncio.wait_for(payment_task, timeout=30)
        cleared = await asyncio.wait_for(clearing_task, timeout=30)
    finally:
        release.set()
        await _finish(payment_task, clearing_task)
        PaymentRouter.invalidate_cache(seed["equivalent_code"])

    assert paid.status == "COMMITTED", paid
    assert probe.attempts == 1 and probe.conflicts == 0, (probe.attempts, probe.conflicts)
    assert probe.locked_amounts == [[Decimal("20.00000000"), Decimal("30.00000000"), Decimal("40.00000000")]], (
        probe.locked_amounts
    )
    assert cleared == Decimal("20.00000000"), cleared
    async with stand.sessions() as s:
        debts = {
            (d.debtor_id, d.creditor_id): d.amount
            for d in (await s.scalars(select(Debt).where(Debt.equivalent_id == seed["equivalent_id"]))).all()
        }
        clearings = (
            await s.execute(
                select(Transaction.state).where(
                    Transaction.type == "CLEARING", Transaction.initiator_id.in_(seed["participant_ids"])
                )
            )
        ).scalars().all()
    assert debts == {(b_id, c_id): Decimal("10.00000000"), (c_id, a_id): Decimal("20.00000000")}, debts
    assert clearings == ["COMMITTED"], clearings
    assert await lock_entries(stand.sessions, seed["equivalent_id"]) == set()


# ── (4) no lock outlives the clearing ─────────────────────────────────────────────────────────


OUTCOMES = ["success", "refused_inactive", "errored", "cancelled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", OUTCOMES)
async def test_no_advisory_lock_of_the_key_outlives_the_clearing(stand, monkeypatch, caplog, outcome) -> None:
    """Whatever ends the clearing - success, the operator stop, an error inside the attempt, a caller
    cancellation - no entry of the key remains on ANY backend of this database, and the clearing's
    connection went back to the pool (its backend alive, no invalidation): the release was confirmed.

    MECHANISM first: inside the attempt the clearing's backend holds the key as a GRANTED `ExclusiveLock`
    (read at the stop/hold check, which every outcome here passes through) - without it, "nothing left" would
    be vacuous.
    """

    probe = probe_clearing(monkeypatch)
    seed = await _seed_interlock_case()
    held_in_attempt: list[set] = []
    in_attempt = asyncio.Event()
    original_guard = ClearingService._refuse_if_equivalent_inactive

    async def guard(self, equivalent_ids):
        held_in_attempt.append(await lock_entries(stand.sessions, seed["equivalent_id"]))
        return await original_guard(self, equivalent_ids)

    monkeypatch.setattr(ClearingService, "_refuse_if_equivalent_inactive", guard)

    if outcome == "refused_inactive":
        async with stand.sessions() as s:
            await s.execute(update(Equivalent).where(Equivalent.id == seed["equivalent_id"]).values(is_active=False))
            await s.commit()
    elif outcome in {"errored", "cancelled"}:

        async def policy(self, debts):
            in_attempt.set()
            if outcome == "errored":
                raise RuntimeError("p019 injected failure inside the clearing attempt")
            await asyncio.Event().wait()

        monkeypatch.setattr(ClearingService, "_cycle_respects_auto_clearing", policy)

    async def clear():
        async with stand.sessions() as session:
            return await ClearingService(session).execute_clearing_with_amount(seed["cycle"])

    task = asyncio.create_task(clear())
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.clearing.service"):
            if outcome == "cancelled":
                await asyncio.wait_for(in_attempt.wait(), timeout=20)
                task.cancel()
            done, _ = await asyncio.wait([task], timeout=30)
            assert task in done, "the clearing did not end"
    finally:
        await _finish(task)
        PaymentRouter.invalidate_cache(seed["equivalent_code"])

    assert probe.pids, "the clearing never took its exclusive lock"
    clearing_pid = probe.pids[0]
    assert held_in_attempt and held_in_attempt[0] == {(clearing_pid, "ExclusiveLock", True)}, held_in_attempt

    if outcome == "success":
        assert task.result() == Decimal("30.00000000")
    elif outcome == "refused_inactive":
        with pytest.raises(ConflictException) as refused:
            task.result()
        assert (refused.value.details or {}).get("reason") == MoneyBoundary.EQUIVALENT_INACTIVE_REASON
    elif outcome == "errored":
        with pytest.raises(GeoException):
            task.result()
    else:
        assert task.cancelled() or isinstance(task.exception(), asyncio.CancelledError)

    assert await no_entry_left(stand.sessions, seed["equivalent_id"]) == set()
    assert stand.invalidations == [], stand.invalidations
    assert await backend_alive(stand.sessions, clearing_pid), "the clearing's connection was not pooled back"
    unconfirmed = [r.getMessage() for r in caplog.records if "interlock_" in r.getMessage()]
    assert unconfirmed == [], unconfirmed


@pytest.mark.asyncio
async def test_an_unconfirmed_unlock_invalidates_the_clearing_connection(stand, monkeypatch, caplog) -> None:
    """The release answers "not released" and releases nothing: the connection must NOT go back to the pool
    with the lock. RESULT: the clearing's committed result is still returned; the pool recorded ONE
    invalidation; the clearing's backend is gone (the server ended it, and its lock with it); no entry of the
    key remains; the warning `event=clearing.interlock_unlock_unconfirmed` names the cause.
    COUNTER-CHECK: `test_no_advisory_lock_of_the_key_outlives_the_clearing[success]` - a confirmed release
    keeps the backend alive with no invalidation - so "gone" here is the invalidation, not the stand.
    """

    probe = probe_clearing(monkeypatch)
    seed = await _seed_interlock_case()
    releases: list[bool] = []

    async def unconfirmed_release(self, equivalent_id):
        releases.append(False)
        return False

    monkeypatch.setattr(MoneyBoundary, "release_exclusive_equivalent_session_lock", unconfirmed_release)

    try:
        with caplog.at_level(logging.WARNING, logger="app.core.clearing.service"):
            async with stand.sessions() as session:
                cleared = await asyncio.wait_for(
                    ClearingService(session).execute_clearing_with_amount(seed["cycle"]), timeout=30
                )
    finally:
        PaymentRouter.invalidate_cache(seed["equivalent_code"])

    assert probe.pids and releases == [False], (probe.pids, releases)
    clearing_pid = probe.pids[0]
    assert cleared == Decimal("30.00000000"), cleared
    assert len(stand.invalidations) == 1, stand.invalidations
    assert any("event=clearing.interlock_unlock_unconfirmed" in r.getMessage() for r in caplog.records)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while await backend_alive(stand.sessions, clearing_pid) and loop.time() < deadline:
        await asyncio.sleep(0.05)
    assert not await backend_alive(stand.sessions, clearing_pid), "the connection holding the lock stayed open"
    assert await no_entry_left(stand.sessions, seed["equivalent_id"]) == set()
