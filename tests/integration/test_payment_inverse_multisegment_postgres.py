"""Two multi-segment payments over the same pairs in OPPOSITE directions both commit and keep the invariants.

A -> B -> C 3.00 and C -> B -> A 2.00 share both pairs; the debts must net to A -> B 1 and B -> C 1.

019 STAGE 4 (`T1906`; manifest `t1901-manifest.md` 5.1). Until then both payments were seeded durable
`PREPARED` with hand-written reservation rows and the engine's commit was raced. Both run the real path end to
end - `PaymentService.create_payment_internal`, one transaction each - from a world of trust lines only.

019 STAGE 5 (`T1909`, `KEEP-EQUIVALENT-LOCK`; manifest 5.1 row `:282-288`). There is no pair or transaction
lock any more: a payment takes its equivalent's lock SHARED, so the two payments no longer queue on each
other. The premise "the waiter queued on an advisory lock of the holder" is REPLACED by the SERIALIZABLE
evidence that now decides the race (`T1908`):

* the holder is parked in its money phase after its pre-state read - both directions of both pairs read,
  nothing written - and, while it is parked, BOTH payments hold the equivalent lock (`ShareLock`, granted,
  read from `pg_locks`) and the waiter runs to its commit: nothing serialises them any more;
* the holder, released, has read rows the waiter then wrote and must not commit on that snapshot: SSI
  refuses it with `40001`, counted by the retry owner (`PaymentService.pay` -> `_retry_or_none`), and the
  holder's whole attempt runs again on a fresh snapshot (its pre-state read twice).

The result assertions are the stage-4 ones unchanged: both `COMMITTED`, debts 1/1, two `PAYMENT` audit rows,
all four limits intact. The reservation count (vacuous since stage 4) went with the table (migration `031`).
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.config import settings
from app.core.money_boundary import _EQUIVALENT_OWNER_LOCK_NAMESPACE, MoneyBoundary
from app.core.payments import service as payment_service_module
from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
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


async def _share_holders(observer, equivalent_id) -> set[int]:
    """Backends holding THIS equivalent's lock granted in shared mode, on this database."""

    rows = (
        await observer.execute(
            text(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted AND mode = 'ShareLock' "
                "AND objsubid = 2 AND classid::bigint = :namespace AND objid::bigint = :key "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            ),
            {
                "namespace": _EQUIVALENT_OWNER_LOCK_NAMESPACE & 0xFFFFFFFF,
                "key": MoneyBoundary._equivalent_owner_lock_key(equivalent_id) & 0xFFFFFFFF,
            },
        )
    ).all()
    await observer.rollback()
    return {int(pid) for (pid,) in rows}


@pytest.mark.asyncio
@pytest.mark.parametrize("holder_direction", ["forward", "reverse"])
async def test_inverse_multisegment_commits_serialize_and_preserve_invariants_postgres(
    db_session,
    monkeypatch,
    holder_direction,
) -> None:
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    # The holder is parked inside its money phase (`COMMIT_TIMEOUT_SECONDS`) while the waiter runs; the
    # budgets are not what this schedule is about.
    monkeypatch.setattr(settings, "PREPARE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "COMMIT_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "PAYMENT_TOTAL_TIMEOUT_SECONDS", 60)

    seed = await _seed_inverse_multisegment_world()
    a_id, b_id, c_id = seed["participant_a_id"], seed["participant_b_id"], seed["participant_c_id"]
    payments = {
        # A -> B -> C 3.00 and C -> B -> A 2.00: the only routes in this world.
        "forward": (a_id, seed["participant_c_pid"], "3.00", str(uuid.uuid4())),
        "reverse": (c_id, seed["participant_a_pid"], "2.00", str(uuid.uuid4())),
    }
    flows_of = {
        "forward": {(a_id, b_id), (b_id, c_id)},
        "reverse": {(c_id, b_id), (b_id, a_id)},
    }
    waiter_direction = "reverse" if holder_direction == "forward" else "forward"
    holder_parked = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_read = asyncio.Event()
    release_waiter = asyncio.Event()
    prestate_reads: dict[str, list[int]] = {"forward": [], "reverse": []}
    conflicts: list[str] = []
    holder_task = None
    waiter_task = None

    original_prestate = payment_service_module._read_payment_prestate

    async def prestate(session, declared_flows):
        result = await original_prestate(session, declared_flows)
        pairs = {(flow.from_id, flow.to_id) for flow in declared_flows}
        direction = next(d for d, flows in flows_of.items() if flows == pairs)
        prestate_reads[direction].append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        if len(prestate_reads[direction]) == 1:
            if direction == holder_direction:
                holder_parked.set()
                await release_holder.wait()
            else:
                waiter_read.set()
                await release_waiter.wait()
        return result

    original_retry = PaymentService._retry_or_none

    def retry_or_none(self, exc, **kwargs):
        conflicts.append(payment_service_module._conflict_cause(exc))
        return original_retry(self, exc, **kwargs)

    monkeypatch.setattr(payment_service_module, "_read_payment_prestate", prestate)
    monkeypatch.setattr(PaymentService, "_retry_or_none", retry_or_none)

    async with (
        TestingSessionLocal() as holder_session,
        TestingSessionLocal() as waiter_session,
        TestingSessionLocal() as observer_session,
    ):

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
            await asyncio.wait_for(holder_parked.wait(), timeout=20.0)

            waiter_task = asyncio.create_task(_pay(waiter_session, waiter_direction))
            try:
                await asyncio.wait_for(waiter_read.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                pytest.fail("the inverse route did not reach its money phase while the holder was parked in its own")
            # Premise 1: both are past their equivalent lock at once - shared, nothing queued on the other.
            holders = await _share_holders(observer_session, seed["equivalent_id"])
            assert holders == {prestate_reads[holder_direction][0], prestate_reads[waiter_direction][0]}, holders
            release_waiter.set()
            waiter_result = await asyncio.wait_for(waiter_task, timeout=20.0)
            assert waiter_result.status == "COMMITTED", waiter_result
            assert not holder_task.done(), "the holder must still be parked when the waiter commits"
            assert conflicts == [], f"the waiter met a conflict before the holder wrote anything: {conflicts}"

            release_holder.set()
            holder_result = await asyncio.wait_for(holder_task, timeout=30.0)
            assert holder_result.status == "COMMITTED", holder_result
        finally:
            release_holder.set()
            release_waiter.set()
            tasks = [task for task in (holder_task, waiter_task) if task is not None]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    # Premise 2: SERIALIZABLE, not a lock, decided the race - the holder's stale attempt was refused with a
    # real 40001 and the retry owner ran the whole attempt again (its pre-state read on a fresh snapshot).
    assert conflicts and set(conflicts) == {"40001"}, conflicts
    assert len(prestate_reads[holder_direction]) == len(conflicts) + 1, (prestate_reads, conflicts)
    assert len(prestate_reads[waiter_direction]) == 1, prestate_reads

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
