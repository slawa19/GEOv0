"""Two multi-segment payments over the same pairs in OPPOSITE directions serialize and keep the invariants.

A -> B -> C 3.00 and C -> B -> A 2.00 share both pairs. Whichever goes first holds its locks through its
commit (paused right after it took its pair locks); the other must queue on an advisory lock before it
takes its own, both commit, and the debts net to A -> B 1 and B -> C 1.

019 STAGE 4 (`T1906`; manifest `t1901-manifest.md` 5.1). Until then both payments were seeded durable
`PREPARED` with hand-written `PrepareLock` rows and `PaymentEngine.commit` was raced. Both now run the real
path end to end - `PaymentService.create_payment_internal`, one transaction each - from a world of trust
lines only: routing finds A-B-C and C-B-A, the binding phase takes the owner, transaction and pair locks
(`app/core/money_boundary.py`), the money phase writes. The waiter queues on the EQUIVALENT OWNER lock the
holder took first (both payments are in one equivalent), before it reaches any pair lock - the premise
asserts only that it waits on an advisory lock of the holder while the holder is paused after its pair
locks, as it did before. Stage 5 replaces that premise with SERIALIZABLE conflict evidence (`T1908`).

THE APPLICATION'S OWN 40001 RETRY STAYS ON (T1549, 2026-09-14). At the application's SERIALIZABLE the
waiter's snapshot predates the holder's commit and its first attempt may meet a genuine serialization
failure; `pay()` retries that on a fresh snapshot. What the retry could hide - a waiter that bypassed the
holder's locks - is asserted BEFORE the holder is released (`waiter_blocked`), so it cannot.
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.config import settings
from app.core.money_boundary import MoneyBoundary
from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine

# Every test here commits through several sessions and runs on a disposable clone of the migrated
# template; its rows go with the clone's drop and nothing is deleted row by row (018 B0b; see
# `tests/tier_on_a_clone.py`).
from tests.tier_on_a_clone import tier_sessions_on_a_clone  # noqa: E402,F401 - autouse fixture



def _require_postgres(db_session) -> None:
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates inverse multi-segment serialization")


async def _seed_inverse_multisegment_world() -> dict:
    """An equivalent, A, B, C, and a line of 50.00 in each direction of A-B and B-C. No payment, no debt."""

    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8]
    equivalent = Equivalent(
        code=f"IM{nonce}".upper(),
        description="Inverse multi-segment payment test",
        precision=2,
    )
    participants = [
        Participant(
            pid=f"P_IM_{label}_{nonce}",
            display_name=f"P_IM_{label}_{nonce}",
            public_key=f"pk_im_{label}_{nonce}",
            type="person",
            status="active",
        )
        for label in ("A", "B", "C")
    ]

    async with TestingSessionLocal() as setup:
        setup.add_all([equivalent, *participants])
        await setup.flush()
        participant_a, participant_b, participant_c = participants

        trustlines = [
            TrustLine(
                from_participant_id=creditor.id,
                to_participant_id=debtor.id,
                equivalent_id=equivalent.id,
                limit=Decimal("50.00"),
                status="active",
            )
            for creditor, debtor in (
                (participant_b, participant_a),
                (participant_a, participant_b),
                (participant_c, participant_b),
                (participant_b, participant_c),
            )
        ]
        setup.add_all(trustlines)
        await setup.commit()

    return {
        "equivalent_id": equivalent.id,
        "equivalent_code": equivalent.code,
        "participant_ids": [participant.id for participant in participants],
        "participant_a_id": participants[0].id,
        "participant_b_id": participants[1].id,
        "participant_c_id": participants[2].id,
        "participant_a_pid": participants[0].pid,
        "participant_c_pid": participants[2].pid,
        "trustline_ids": [trustline.id for trustline in trustlines],
    }


async def _wait_for_advisory_wait(
    observer,
    *,
    backend_pid: int,
    waiter_acquired: asyncio.Event,
) -> bool:
    for _ in range(500):
        waiting = await observer.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_locks "
                "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted"
                ")"
            ),
            {"pid": backend_pid},
        )
        await observer.rollback()
        if waiting:
            return True
        if waiter_acquired.is_set():
            return False
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize("holder_direction", ["forward", "reverse"])
async def test_inverse_multisegment_commits_serialize_and_preserve_invariants_postgres(
    db_session,
    monkeypatch,
    holder_direction,
) -> None:
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    # The holder is paused inside its binding phase (`PREPARE_TIMEOUT_SECONDS`) while the waiter queues;
    # the budgets are not what this schedule is about.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)

    seed = await _seed_inverse_multisegment_world()
    payments = {
        # A -> B -> C 3.00 and C -> B -> A 2.00: the only routes in this world.
        "forward": (seed["participant_a_id"], seed["participant_c_pid"], "3.00", str(uuid.uuid4())),
        "reverse": (seed["participant_c_id"], seed["participant_a_pid"], "2.00", str(uuid.uuid4())),
    }
    waiter_direction = "reverse" if holder_direction == "forward" else "forward"
    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_attempted = asyncio.Event()
    waiter_acquired = asyncio.Event()
    holder_pair_locks: list[int] = []
    waiter_pair_locks: list[int] = []
    holder_task = None
    waiter_task = None

    async with (
        TestingSessionLocal() as holder_session,
        TestingSessionLocal() as waiter_session,
        TestingSessionLocal() as observer_session,
    ):
        original_pair_locks = MoneyBoundary._acquire_segment_advisory_lock_keys
        original_owner_locks = MoneyBoundary._acquire_equivalent_owner_locks
        waiter_pids: list[int] = []

        async def _pair_locks(self, keys) -> None:
            await original_pair_locks(self, keys)
            if self.session is holder_session and not holder_pair_locks:
                holder_pair_locks.append(len(set(keys)))
                holder_acquired.set()
                await release_holder.wait()
            elif self.session is waiter_session:
                waiter_pair_locks.append(len(set(keys)))
                waiter_acquired.set()

        async def _owner_locks(self, equivalent_ids) -> None:
            if self.session is waiter_session and not waiter_attempted.is_set():
                waiter_pids.append(int(await self.session.scalar(text("SELECT pg_backend_pid()"))))
                waiter_attempted.set()
            await original_owner_locks(self, equivalent_ids)

        monkeypatch.setattr(MoneyBoundary, "_acquire_segment_advisory_lock_keys", _pair_locks)
        monkeypatch.setattr(MoneyBoundary, "_acquire_equivalent_owner_locks", _owner_locks)

        async def _pay(session, direction):
            sender_id, to_pid, amount, tx_id = payments[direction]
            return await PaymentService(session).create_payment_internal(
                sender_id,
                to_pid=to_pid,
                equivalent=seed["equivalent_code"],
                amount=amount,
                idempotency_key=tx_id,
            )

        try:
            holder_task = asyncio.create_task(_pay(holder_session, holder_direction))
            await asyncio.wait_for(holder_acquired.wait(), timeout=20.0)
            # Premise: the holder is paused AFTER its pair locks, and they cover both segments.
            assert holder_pair_locks == [2], holder_pair_locks

            waiter_task = asyncio.create_task(_pay(waiter_session, waiter_direction))
            await asyncio.wait_for(waiter_attempted.wait(), timeout=20.0)
            waiter_blocked = await _wait_for_advisory_wait(
                observer_session,
                backend_pid=waiter_pids[0],
                waiter_acquired=waiter_acquired,
            )
            assert waiter_blocked, "the inverse route did not queue behind the holder's locks"
            assert not waiter_acquired.is_set()

            release_holder.set()
            holder_result, waiter_result = await asyncio.wait_for(
                asyncio.gather(holder_task, waiter_task),
                timeout=30.0,
            )
            assert holder_result.status == "COMMITTED", holder_result
            assert waiter_result.status == "COMMITTED", waiter_result
            assert waiter_acquired.is_set()
            assert waiter_pair_locks and set(waiter_pair_locks) == {2}, waiter_pair_locks
        finally:
            release_holder.set()
            tasks = [task for task in (holder_task, waiter_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    tx_ids = [payments["forward"][3], payments["reverse"][3]]
    async with TestingSessionLocal() as verify:
        tx_states = dict(
            (
                await verify.execute(
                    select(Transaction.tx_id, Transaction.state).where(
                        Transaction.tx_id.in_(tx_ids)
                    )
                )
            ).all()
        )
        assert tx_states == {
            payments["forward"][3]: "COMMITTED",
            payments["reverse"][3]: "COMMITTED",
        }

        debts = {
            tuple(row)
            for row in (
            await verify.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == seed["equivalent_id"]
                )
            )
            ).all()
        }
        assert debts == {
            (
                seed["participant_a_id"],
                seed["participant_b_id"],
                Decimal("1.00000000"),
            ),
            (
                seed["participant_b_id"],
                seed["participant_c_id"],
                Decimal("1.00000000"),
            ),
        }
        # Vacuous since stage 4 (no payment path writes a reservation); it goes with the table at stage 5.
        assert (
            await verify.scalar(
                select(func.count()).select_from(PrepareLock).where(
                    PrepareLock.tx_id.in_(tx_ids)
                )
            )
            == 0
        )
        assert (
            await verify.scalar(
                select(func.count()).select_from(IntegrityAuditLog).where(
                    IntegrityAuditLog.tx_id.in_(tx_ids),
                    IntegrityAuditLog.operation_type == "PAYMENT",
                )
            )
            == 2
        )
        limits = (
            await verify.scalars(
                select(TrustLine.limit).where(
                    TrustLine.id.in_(seed["trustline_ids"])
                )
            )
        ).all()
        assert limits == [Decimal("50.00000000")] * 4
