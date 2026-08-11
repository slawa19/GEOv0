import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text

from app.core.payments.engine import PaymentEngine
from app.db.models.audit_log import IntegrityAuditLog
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.prepare_lock import PrepareLock
from app.db.models.transaction import Transaction
from app.db.models.trustline import TrustLine


pytestmark = pytest.mark.postgres


def _require_postgres(db_session) -> None:
    dialect = db_session.get_bind().dialect.name
    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates inverse multi-segment serialization")


def _flow(*, from_id, to_id, amount, equivalent_id) -> dict[str, str]:
    return {
        "from": str(from_id),
        "to": str(to_id),
        "amount": str(amount),
        "equivalent": str(equivalent_id),
    }


async def _seed_inverse_multisegment_payments() -> dict:
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
        forward_tx = Transaction(
            tx_id=str(uuid.uuid4()),
            type="PAYMENT",
            initiator_id=participant_a.id,
            payload={
                "routes": [
                    {
                        "path": [
                            participant_a.pid,
                            participant_b.pid,
                            participant_c.pid,
                        ],
                        "amount": "3.00",
                    }
                ]
            },
            state="PREPARED",
        )
        reverse_tx = Transaction(
            tx_id=str(uuid.uuid4()),
            type="PAYMENT",
            initiator_id=participant_c.id,
            payload={
                "routes": [
                    {
                        "path": [
                            participant_c.pid,
                            participant_b.pid,
                            participant_a.pid,
                        ],
                        "amount": "2.00",
                    }
                ]
            },
            state="PREPARED",
        )
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        setup.add_all([*trustlines, forward_tx, reverse_tx])
        # PrepareLock has no ORM relationship to Transaction, so make the FK
        # ordering explicit instead of relying on unit-of-work dependency sorting.
        await setup.flush()
        setup.add_all(
            [
                PrepareLock(
                    tx_id=forward_tx.tx_id,
                    participant_id=participant_a.id,
                    effects={
                        "flows": [
                            _flow(
                                from_id=participant_a.id,
                                to_id=participant_b.id,
                                amount=Decimal("3.00"),
                                equivalent_id=equivalent.id,
                            ),
                            _flow(
                                from_id=participant_b.id,
                                to_id=participant_c.id,
                                amount=Decimal("3.00"),
                                equivalent_id=equivalent.id,
                            ),
                        ]
                    },
                    expires_at=expires_at,
                ),
                PrepareLock(
                    tx_id=reverse_tx.tx_id,
                    participant_id=participant_c.id,
                    effects={
                        "flows": [
                            _flow(
                                from_id=participant_c.id,
                                to_id=participant_b.id,
                                amount=Decimal("2.00"),
                                equivalent_id=equivalent.id,
                            ),
                            _flow(
                                from_id=participant_b.id,
                                to_id=participant_a.id,
                                amount=Decimal("2.00"),
                                equivalent_id=equivalent.id,
                            ),
                        ]
                    },
                    expires_at=expires_at,
                ),
            ]
        )
        await setup.commit()

    return {
        "equivalent_id": equivalent.id,
        "participant_ids": [participant.id for participant in participants],
        "participant_a_id": participants[0].id,
        "participant_b_id": participants[1].id,
        "participant_c_id": participants[2].id,
        "forward_tx_id": forward_tx.tx_id,
        "reverse_tx_id": reverse_tx.tx_id,
        "trustline_ids": [trustline.id for trustline in trustlines],
    }


async def _cleanup_seed(seed: dict) -> None:
    from tests.conftest import TestingSessionLocal

    tx_ids = [seed["forward_tx_id"], seed["reverse_tx_id"]]
    async with TestingSessionLocal() as cleanup:
        await cleanup.execute(
            delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id.in_(tx_ids))
        )
        await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_(tx_ids)))
        await cleanup.execute(delete(Transaction).where(Transaction.tx_id.in_(tx_ids)))
        await cleanup.execute(
            delete(Debt).where(Debt.equivalent_id == seed["equivalent_id"])
        )
        await cleanup.execute(
            delete(TrustLine).where(
                TrustLine.equivalent_id == seed["equivalent_id"]
            )
        )
        await cleanup.execute(
            delete(Participant).where(Participant.id.in_(seed["participant_ids"]))
        )
        await cleanup.execute(
            delete(Equivalent).where(Equivalent.id == seed["equivalent_id"])
        )
        await cleanup.commit()


async def _wait_for_advisory_wait(
    observer,
    *,
    backend_pid: int,
    waiter_acquired: asyncio.Event,
) -> bool:
    for _ in range(200):
        waiting = await observer.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_locks "
                "WHERE pid = :pid AND locktype = 'advisory' AND NOT granted"
                ")"
            ),
            {"pid": backend_pid},
        )
        if waiting:
            return True
        if waiter_acquired.is_set():
            return False
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize("holder_direction", ["forward", "reverse"])
@pytest.mark.xfail(
    reason="P104: inverse multi-segment commits still use directional pair keys",
    strict=True,
)
async def test_inverse_multisegment_commits_serialize_and_preserve_invariants_postgres(
    db_session,
    monkeypatch,
    holder_direction,
) -> None:
    _require_postgres(db_session)

    from tests.conftest import TestingSessionLocal

    seed = await _seed_inverse_multisegment_payments()
    holder_tx_id = seed[f"{holder_direction}_tx_id"]
    waiter_direction = "reverse" if holder_direction == "forward" else "forward"
    waiter_tx_id = seed[f"{waiter_direction}_tx_id"]
    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_attempted = asyncio.Event()
    waiter_acquired = asyncio.Event()
    holder_task = None
    waiter_task = None

    try:
        async with (
            TestingSessionLocal() as holder_session,
            TestingSessionLocal() as waiter_session,
            TestingSessionLocal() as observer_session,
        ):
            waiter_pid = int(
                (await waiter_session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            )
            holder_engine = PaymentEngine(holder_session)
            waiter_engine = PaymentEngine(waiter_session)
            # A whole-UoW retry could hide the unsafe schedule behind a green final state.
            holder_engine._retry_attempts = 1
            waiter_engine._retry_attempts = 1
            holder_acquire = holder_engine._acquire_segment_advisory_lock_keys
            waiter_acquire = waiter_engine._acquire_segment_advisory_lock_keys

            async def _hold_after_acquisition(keys) -> None:
                await holder_acquire(keys)
                holder_acquired.set()
                await release_holder.wait()

            async def _observe_waiter_acquisition(keys) -> None:
                waiter_attempted.set()
                await waiter_acquire(keys)
                waiter_acquired.set()

            monkeypatch.setattr(
                holder_engine,
                "_acquire_segment_advisory_lock_keys",
                _hold_after_acquisition,
            )
            monkeypatch.setattr(
                waiter_engine,
                "_acquire_segment_advisory_lock_keys",
                _observe_waiter_acquisition,
            )

            try:
                holder_task = asyncio.create_task(holder_engine.commit(holder_tx_id))
                await asyncio.wait_for(holder_acquired.wait(), timeout=5.0)

                waiter_task = asyncio.create_task(waiter_engine.commit(waiter_tx_id))
                await asyncio.wait_for(waiter_attempted.wait(), timeout=5.0)
                waiter_blocked = await _wait_for_advisory_wait(
                    observer_session,
                    backend_pid=waiter_pid,
                    waiter_acquired=waiter_acquired,
                )
                assert waiter_blocked, "inverse route bypassed the holder's pair locks"
                assert not waiter_acquired.is_set()

                release_holder.set()
                holder_result, waiter_result = await asyncio.wait_for(
                    asyncio.gather(holder_task, waiter_task),
                    timeout=15.0,
                )
                assert holder_result is True
                assert waiter_result is True
                assert waiter_acquired.is_set()
            finally:
                release_holder.set()
                tasks = [task for task in (holder_task, waiter_task) if task is not None]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

        async with TestingSessionLocal() as verify:
            tx_states = dict(
                (
                    await verify.execute(
                        select(Transaction.tx_id, Transaction.state).where(
                            Transaction.tx_id.in_(
                                [seed["forward_tx_id"], seed["reverse_tx_id"]]
                            )
                        )
                    )
                ).all()
            )
            assert tx_states == {
                seed["forward_tx_id"]: "COMMITTED",
                seed["reverse_tx_id"]: "COMMITTED",
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
                    select(func.count()).select_from(PrepareLock).where(
                        PrepareLock.tx_id.in_(
                            [seed["forward_tx_id"], seed["reverse_tx_id"]]
                        )
                    )
                )
                == 0
            )
            assert (
                await verify.scalar(
                    select(func.count()).select_from(IntegrityAuditLog).where(
                        IntegrityAuditLog.tx_id.in_(
                            [seed["forward_tx_id"], seed["reverse_tx_id"]]
                        ),
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
    finally:
        await _cleanup_seed(seed)
