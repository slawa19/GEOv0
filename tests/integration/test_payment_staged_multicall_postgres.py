"""The staged payment path: a caller-owned transaction with several payments, and the staged owner lock.

027 STAGE 2 (`T2704`): the 2nd and 3rd tests (the shared staged lock, its `lock_timeout`) are REMOVED with it.

019 STAGE 4 (`T1906`; manifest `t1901-manifest.md` 5.1). The first test of this module,
`test_staged_multicall_batches_do_not_exhaust_retry_on_retained_locks_postgres`, is DROPPED: it seeded four
durable `PREPARED` payments with hand-written `PrepareLock` rows (CHECK `030` refuses the seed) and staged
them through `PaymentEngine.commit(commit=False)` (removed). Its assertions, by manifest row:

* both calls of a batch succeed (:239, :245) - removed with `PaymentEngine.commit`;
* exactly one batch fails with a real 40001, its retry in a fresh outer transaction succeeds, all four are
  COMMITTED with debts A->B 4, B->C 4 (:273-280, :289-311) - SURVIVE, stronger, in
  `tests/integration/test_p015_p1_money_replay_postgres.py::`
  `test_a_real_serialization_failure_replays_the_money_phase_and_commits_once` (a real 40001 on the
  staged path of a real tick, one replay, one commit, the debts);
* no `prepare_locks` left (:312-319) - vacuous since stage 4, the table goes at stage 5;
* one PAYMENT audit row per payment and the trust limits untouched (:320-336) - the staged path had no
  other assertion of these, so the first test below is new and asserts them on the direct execution.

019 STAGE 5 (`T1909`, `KEEP-EQUIVALENT-LOCK`). The staged entry takes the batch's complete equivalent set
SHARED (`acquire_shared_equivalent_locks`). The second test is REWRITTEN for the new mode: until stage 5 it
asserted that a second staged batch over the same set WAITS; now two staged batches must NOT wait for each
other (both hold the lock at once, read from `pg_locks`), while a clearing's EXCLUSIVE session lock on one
equivalent of the set must wait for them - with the waiter and its blocker named by PostgreSQL - and a
disjoint equivalent is not serialised. The third test (the caller's `lock_timeout` restored) keeps its
assertions, on the renamed entry.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.payments.service import PaymentService
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine

from tests.debt_setup import debt_fixture_setup

# The first test commits payments through several sessions and runs on a disposable clone of the migrated
# template; its rows go with the clone's drop (018 B0b; see `tests/tier_on_a_clone.py`). The other two
# only take advisory locks on random ids, commit no row, and stay on the tier.
from tests.tier_on_a_clone import tier_on_a_clone  # noqa: E402,F401 - opt-in fixture



def _require_postgres(db_session) -> None:
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates staged transaction advisory locks")


async def _seed_staged_world() -> dict:
    """A, B, C; B trusts A and C trusts B for 50.00; opening debts A->B 1 and B->C 1. No payment."""

    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:8]
    equivalent = Equivalent(
        code=f"SM{nonce}".upper(),
        description="Staged multi-call payment test",
        precision=2,
    )
    participant_a, participant_b, participant_c = [
        Participant(
            pid=f"P_SM_{label}_{nonce}",
            display_name=f"P_SM_{label}_{nonce}",
            public_key=f"pk_sm_{label}_{nonce}",
            type="person",
            status="active",
        )
        for label in ("A", "B", "C")
    ]

    async with TestingSessionLocal() as setup:
        setup.add_all([equivalent, participant_a, participant_b, participant_c])
        await setup.flush()
        async with debt_fixture_setup(setup, label="setup"):
            setup.add_all(
                [
                    TrustLine(
                        from_participant_id=participant_b.id,
                        to_participant_id=participant_a.id,
                        equivalent_id=equivalent.id,
                        limit=Decimal("50.00"),
                        status="active",
                    ),
                    TrustLine(
                        from_participant_id=participant_c.id,
                        to_participant_id=participant_b.id,
                        equivalent_id=equivalent.id,
                        limit=Decimal("50.00"),
                        status="active",
                    ),
                    Debt(
                        debtor_id=participant_a.id,
                        creditor_id=participant_b.id,
                        equivalent_id=equivalent.id,
                        amount=Decimal("1.00"),
                    ),
                    Debt(
                        debtor_id=participant_b.id,
                        creditor_id=participant_c.id,
                        equivalent_id=equivalent.id,
                        amount=Decimal("1.00"),
                    ),
                ]
            )
        await setup.commit()

    return {
        "equivalent_id": equivalent.id,
        "equivalent_code": equivalent.code,
        "participant_a_id": participant_a.id,
        "participant_b_id": participant_b.id,
        "participant_c_id": participant_c.id,
        "participant_b_pid": participant_b.pid,
        "participant_c_pid": participant_c.pid,
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("tier_on_a_clone")
async def test_a_staged_batch_writes_one_payment_audit_row_per_committed_payment_and_keeps_the_limits_postgres(
    db_session,
) -> None:
    """Two staged payments in ONE caller-owned transaction, the way the simulator tick stages them: the
    batch's equivalent set first, shared (`acquire_shared_equivalent_locks`), then each payment through
    `create_payment_internal_staged`, then the caller's one commit.

    After the commit, each payment is `COMMITTED` with exactly ONE `IntegrityAuditLog` row of type
    `PAYMENT` for its equivalent, verified; the debts moved by exactly the two amounts; no trust line
    changed. (Manifest 5.1, `test_payment_staged_multicall_postgres.py` :320-336, TO WRITE at stage 4.)

    MUTATIONS: drop `_write_integrity_audit` from `PaymentService._apply_payment` - no audit row, red;
    write it twice - two rows for a payment, red.
    """

    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_staged_world()
    batch = [
        (seed["participant_a_id"], seed["participant_b_pid"], "1.00", str(uuid.uuid4())),
        (seed["participant_b_id"], seed["participant_c_pid"], "2.00", str(uuid.uuid4())),
    ]
    tx_ids = [tx_id for *_rest, tx_id in batch]

    async with TestingSessionLocal() as verify:
        limits_before = sorted(
            (
                await verify.execute(
                    select(TrustLine.id, TrustLine.limit, TrustLine.status).where(
                        TrustLine.equivalent_id == seed["equivalent_id"]
                    )
                )
            ).all()
        )

    async with TestingSessionLocal() as session:
        service = PaymentService(session)
        await service.lock_staged_lines([seed["equivalent_code"]], {seed[f"participant_{x}_id"] for x in "abc"})
        staged = []
        for sender_id, to_pid, amount, tx_id in batch:
            staged.append(
                await service.create_payment_internal_staged(
                    sender_id,
                    to_pid=to_pid,
                    equivalent=seed["equivalent_code"],
                    amount=amount,
                    idempotency_key=tx_id,
                )
            )
        # Premise: both were staged as payments of this transaction, not refused into a result.
        assert [item.result.status for item in staged] == ["COMMITTED", "COMMITTED"], staged
        await session.commit()

    async with TestingSessionLocal() as verify:
        states = dict(
            (
                await verify.execute(
                    select(Transaction.tx_id, Transaction.state).where(Transaction.tx_id.in_(tx_ids))
                )
            ).all()
        )
        audits = (
            await verify.execute(
                select(
                    IntegrityAuditLog.tx_id,
                    IntegrityAuditLog.operation_type,
                    IntegrityAuditLog.equivalent_code,
                    IntegrityAuditLog.verification_passed,
                ).where(IntegrityAuditLog.tx_id.in_(tx_ids))
            )
        ).all()
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
        limits_after = sorted(
            (
                await verify.execute(
                    select(TrustLine.id, TrustLine.limit, TrustLine.status).where(
                        TrustLine.equivalent_id == seed["equivalent_id"]
                    )
                )
            ).all()
        )

    assert states == {tx_id: "COMMITTED" for tx_id in tx_ids}, states
    assert sorted(audits) == sorted(
        (tx_id, "PAYMENT", seed["equivalent_code"], None) for tx_id in tx_ids  # 024 `T2413.2`: None = the row records the operation, no check ran
    ), audits
    assert debts == {
        (seed["participant_a_id"], seed["participant_b_id"], Decimal("2.00000000")),
        (seed["participant_b_id"], seed["participant_c_id"], Decimal("3.00000000")),
    }, debts
    assert len(limits_before) == 2, limits_before
    assert limits_after == limits_before
