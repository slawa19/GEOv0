"""Two payments over one 10-capacity bottleneck: one commits, the other is refused `E002`.

019 stage 5 (`T1909`, `KEEP-EQUIVALENT-LOCK`). Until then the two payments were serialised by the
equivalent owner lock, and this test held the first inside it while the second queued. Payments now take
that lock SHARED, so they no longer queue on each other: the mechanism is SERIALIZABLE and the retry owner
of `pay()`. The schedule parks BOTH payments after their authoritative pre-state read (both admitted,
both past their capacity check, neither has written), asserts that both hold the shared lock at once
(`pg_locks`), releases them, and asserts that the conflict really happened - a retry counted with its
SQLSTATE - before the result. The result is unchanged: one commit, one `E002`, the shared debt within
capacity, one audit, one publication. The loser was admitted before its refusal, so it is the definitive
`ABORTED` (spec, `FORK-5`; same outcome as `T1908`'s `test_the_bottleneck_loser_is_refused_after_admission`).
"""

import uuid
import asyncio
import sys
from decimal import Decimal

import pytest

from sqlalchemy import select

# The test commits payments (debts and their journal) through several sessions and runs on a
# disposable clone of the migrated template; its rows go with the clone's drop (018 B0b; see
# `tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: E402,F401 - opt-in fixture



@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_concurrent_payments_shared_bottleneck_commit_once_postgres(
    db_session,
    monkeypatch,
):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None

    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates the SERIALIZABLE resolution of a shared bottleneck")

    from sqlalchemy import text

    from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE, MoneyBoundary
    from app.core.payments import service as payment_service_module
    from app.core.payments.service import PaymentService
    from app.config import settings
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from app.utils.event_bus import event_bus
    from app.utils.exceptions import RoutingException
    from tests.conftest import TestingSessionLocal

    # This test deliberately parks both payments inside their transactions. Keep the product
    # timeout taxonomy out of the schedule while retaining bounded test waits.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 15)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 30)

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    participant_ids = [uuid.uuid4() for _ in range(4)]
    eq = Equivalent(
        id=equivalent_id,
        code=f"SR{nonce}".upper(),
        description="Single route concurrency test",
        precision=2,
    )
    pids = [f"A_SR_{nonce}", f"B_SR_{nonce}", f"C_SR_{nonce}", f"D_SR_{nonce}"]
    participants = [
        Participant(
            id=participant_id,
            pid=pid,
            display_name=pid,
            public_key=f"pk_{pid}",
            type="person",
            status="active",
        )
        for participant_id, pid in zip(participant_ids, pids, strict=True)
    ]
    id_by_pid = {participant.pid: participant.id for participant in participants}
    a_pid, b_pid, c_pid, d_pid = pids
    tx_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    original_prestate = payment_service_module._read_payment_prestate
    prestate_calls = 0
    both_parked = asyncio.Event()
    release_both = asyncio.Event()
    parked_pids: list[int] = []

    async def _park_after_prestate(session, declared_flows):
        # The first attempt of EACH payment parks here: routed, admitted, shared lock held, capacity
        # checked, pre-state read, nothing written. A retry passes straight through.
        nonlocal prestate_calls
        result = await original_prestate(session, declared_flows)
        prestate_calls += 1
        if prestate_calls <= 2:
            parked_pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
            if prestate_calls == 2:
                both_parked.set()
            await release_both.wait()
        return result

    monkeypatch.setattr(
        payment_service_module, "_read_payment_prestate", _park_after_prestate
    )

    retried_causes: list[str] = []
    original_retry = PaymentService._retry_or_none

    def _count_retry(self, exc, **kwargs):
        retried_causes.append(payment_service_module._conflict_cause(exc))
        return original_retry(self, exc, **kwargs)

    monkeypatch.setattr(PaymentService, "_retry_or_none", _count_retry)

    publications: list[dict] = []

    def _capture_publish(**kwargs):
        publications.append(dict(kwargs))

    monkeypatch.setattr(event_bus, "publish", _capture_publish)

    async def _pay(sender_id, tx_id: str):
        async with TestingSessionLocal() as session:
            await session.connection(
                execution_options={"isolation_level": "SERIALIZABLE"}
            )
            isolation = (
                await session.execute(text("SHOW transaction_isolation"))
            ).scalar_one()
            assert str(isolation).lower() == "serializable"  # 019 stage 5 (T1907): the only supported level
            try:
                return await PaymentService(session).create_payment_internal(
                    sender_id,
                    to_pid=d_pid,
                    equivalent=eq.code,
                    amount="8.00",
                    idempotency_key=tx_id,
                )
            except Exception as exc:
                return exc

    task1 = None
    task2 = None
    try:
        async with TestingSessionLocal() as setup:
            setup.add(eq)
            setup.add_all(participants)
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=id_by_pid[c_pid],
                        to_participant_id=id_by_pid[a_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("100.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=id_by_pid[c_pid],
                        to_participant_id=id_by_pid[b_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("100.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=id_by_pid[d_pid],
                        to_participant_id=id_by_pid[c_pid],
                        equivalent_id=equivalent_id,
                        limit=Decimal("10.00"),
                        status="active",
                    ),
                ]
            )
            await setup.commit()

        task1 = asyncio.create_task(_pay(id_by_pid[a_pid], tx_ids[0]))
        task2 = asyncio.create_task(_pay(id_by_pid[b_pid], tx_ids[1]))
        # MECHANISM 1: both payments are inside their transactions at once - the shared lock let the
        # second in while the first holds it.
        await asyncio.wait_for(both_parked.wait(), timeout=10.0)
        assert not task1.done()
        assert not task2.done()
        assert len(set(parked_pids)) == 2, parked_pids
        async with TestingSessionLocal() as observer:
            shared_holders = (
                await observer.execute(
                    text(
                        "SELECT pid, mode FROM pg_locks WHERE locktype = 'advisory' AND granted "
                        "AND classid = CAST(:namespace AS oid) AND objid = CAST(:key AS oid) "
                        "AND database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database())"
                    ),
                    {
                        "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE,
                        "key": MoneyBoundary._equivalent_owner_lock_key(equivalent_id)
                        & 0xFFFFFFFF,
                    },
                )
            ).all()
        assert sorted(shared_holders) == sorted(
            (pid, "ShareLock") for pid in parked_pids
        ), shared_holders

        release_both.set()
        result1, result2 = await asyncio.wait_for(
            asyncio.gather(task1, task2),
            timeout=30.0,
        )

        # MECHANISM 2: the race was resolved by a retry of a real conflict, not by a queue. Two
        # concurrent inserts of the one new C -> D debt row: SERIALIZABLE reports 40001 (or the 23505
        # the owners retry as the same collision, `is_debt_pair_collision`).
        assert retried_causes, "no retry was counted: the two payments did not race"
        assert set(retried_causes) <= {"40001", "23505"}, retried_causes

        results = [result1, result2]
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RoutingException)
        assert failures[0].code == "E002"
        committed_tx_id = successes[0].tx_id
        rejected_tx_id = next(tx_id for tx_id in tx_ids if tx_id != committed_tx_id)

        async with TestingSessionLocal() as verify:
            states = dict(
                (
                    await verify.execute(
                        select(Transaction.tx_id, Transaction.state).where(
                            Transaction.tx_id.in_(tx_ids)
                        )
                    )
                ).all()
            )
            shared_debt = await verify.scalar(
                select(Debt.amount).where(
                    Debt.debtor_id == id_by_pid[c_pid],
                    Debt.creditor_id == id_by_pid[d_pid],
                    Debt.equivalent_id == eq.id,
                )
            )
            audits = (
                await verify.scalars(
                    select(IntegrityAuditLog).where(
                        IntegrityAuditLog.operation_type == "PAYMENT",
                        IntegrityAuditLog.tx_id.in_(tx_ids),
                    )
                )
            ).all()

            assert states == {
                committed_tx_id: "COMMITTED",
                rejected_tx_id: "ABORTED",
            }
            assert shared_debt == Decimal("8.00000000")
            assert shared_debt <= Decimal("10.00")
            assert [(audit.tx_id, audit.verification_passed) for audit in audits] == [
                (committed_tx_id, True)
            ]

        assert len(publications) == 1
        assert publications[0]["event"] == "payment.received"
        assert publications[0]["payload"]["tx_id"] == committed_tx_id
    finally:
        primary_error = sys.exc_info()[1]
        try:
            release_both.set()
            tasks = [task for task in (task1, task2) if task is not None]
            done = set()
            pending = set()
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=5.0)
            for task in pending:
                task.cancel()
            still_pending = set()
            if pending:
                cancelled, still_pending = await asyncio.wait(pending, timeout=1.0)
                done.update(cancelled)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if still_pending:
                pending_names = sorted(task.get_name() for task in still_pending)
                raise AssertionError(
                    f"Payment workers did not stop after cancellation: {pending_names}"
                )
        except BaseException as teardown_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "Payment test teardown also failed: "
                f"{type(teardown_error).__name__}: {teardown_error}"
            )


# `test_concurrent_prepare_routes_shared_bottleneck_serializes_on_postgres` is DROPPED by 019 stage 4
# (manifest `t1901-manifest.md` 5.3, rows :411-420, :425-427; spec Verification plan section 3, "Стадия 4"):
# two `PaymentEngine.prepare_routes` calls over seeded `NEW` payment rows asserted that exactly one reserved
# the shared bottleneck and the other got `E002`. `prepare_routes` is gone with the engine and CHECK `030`
# refuses the `NEW` seed. The effect - one payment through the shared bottleneck, the other refused `E002`,
# the shared debt within capacity - is held end to end, with commits, by
# `test_concurrent_payments_shared_bottleneck_commit_once_postgres` above.
