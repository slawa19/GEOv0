"""Shared helpers of the clearing/payment interlock schedules. NOT a test module.

Moved here from `tests/integration/test_clearing_payment_prepare_interlock_postgres.py` by programme 019
stage 4 (`T1906`; manifest `specs/019-payment-one-transaction/t1901-manifest.md` section 3 item 1,
`FORK-1`): several race suites import them, and a test module is not a library - rewriting or dropping
its tests must not break the importers.

EXPORTS AND THEIR IMPORTERS (`git grep -n p019_interlock_support -- tests/`):

* `_seed_interlock_case()` - a fresh equivalent, participants A, B, C, the three-edge debt cycle
  A->B 100, B->C 30, C->A 40 (clearing clears 30) and trust lines for it plus the reverse line A->B
  that gives a B -> A payment its capacity. Commits through its own session, so a caller must run on
  a disposable clone (`tier_on_a_clone` / `tier_sessions_on_a_clone`, 018 B0b). Since stage 4 it seeds
  NO `PAYMENT` row: the `NEW` payment it used to insert is refused by CHECK `030`
  (`chk_transaction_payment_terminal`), and no importer read its `payment_tx_id`. A test that needs a
  payment makes one through `PaymentService`.
* `_use_serializable(session)` - pins the session's transaction to SERIALIZABLE, asserts it, and
  returns the backend pid.
* `_no_advisory_lock_is_held(caplog)` - no advisory lock is held on THIS database (T1537: `pg_locks`
  is the whole server), and clearing's cleanup did not invalidate its connection. Becomes true by
  construction when stage 5 removes the advisory locks - that stage replaces or deletes it (manifest
  section 3, "Стадия 5 обязана также").
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text

from tests.debt_setup import debt_fixture_setup


async def _use_serializable(session) -> int:
    await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
    isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
    assert str(isolation).lower() == "serializable"
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


async def _no_advisory_lock_is_held(caplog) -> None:
    """No advisory lock is held on this database, read from `pg_locks` itself.

    The direct form of "the owner lock was released". A probe that then takes the lock for real is the
    stronger statement; this one is what makes a probe TIMEOUT readable - a timeout with no lock held
    is a slow connection, and a timeout with a lock held is the defect.

    `pg_locks` is the whole server, so the database filter is what makes "on this database" true: a
    lock another run holds on another `geov0_test_*` database used to fail this (T1537). A real leak
    would ALSO show as clearing's cleanup invalidating its connection, which nothing asserted before.
    """

    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as observer:
        held = (
            await observer.execute(
                text(
                    "SELECT pid, database, classid, objid, objsubid FROM pg_locks "
                    "WHERE locktype = 'advisory' AND granted "
                    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
                )
            )
        ).all()
    assert held == [], f"an advisory lock is still held after the scenario: {held}"
    invalidated = [
        record.getMessage()
        for record in caplog.records
        if "interlock_unlock_unconfirmed" in record.getMessage()
        or "interlock_cleanup_invalidated" in record.getMessage()
    ]
    assert invalidated == [], f"clearing's cleanup invalidated its connection: {invalidated}"


async def _seed_interlock_case():
    from app.db.models.debt import Debt
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from tests.conftest import TestingSessionLocal

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    equivalent_code = f"PI{nonce}".upper()
    participant_ids = [uuid.uuid4() for _ in range(3)]
    participant_pids = [f"{label}_PI_{nonce}" for label in ("A", "B", "C")]
    a_id, b_id, c_id = participant_ids
    debt_ids = [uuid.uuid4() for _ in range(3)]

    async with TestingSessionLocal() as setup:
        setup.add(
            Equivalent(
                id=equivalent_id,
                code=equivalent_code,
                description="Clearing/payment prepare interlock test",
                precision=2,
            )
        )
        setup.add_all(
            [
                Participant(
                    id=participant_id,
                    pid=pid,
                    display_name=label,
                    public_key=f"pk_{label}_{nonce}",
                    type="person",
                    status="active",
                )
                for participant_id, pid, label in zip(
                    participant_ids,
                    participant_pids,
                    ("A", "B", "C"),
                    strict=True,
                )
            ]
        )
        setup.add_all(
            [
                TrustLine(
                    from_participant_id=creditor_id,
                    to_participant_id=debtor_id,
                    equivalent_id=equivalent_id,
                    limit=Decimal("200.00"),
                    policy={"auto_clearing": True},
                    status="active",
                )
                for debtor_id, creditor_id in (
                    (a_id, b_id),
                    (b_id, c_id),
                    (c_id, a_id),
                    # Reverse B -> A payment capacity is controlled by A -> B.
                    (b_id, a_id),
                )
            ]
        )
        async with debt_fixture_setup(setup, label="setup"):
            setup.add_all(
                [
                    Debt(
                        id=debt_id,
                        debtor_id=debtor_id,
                        creditor_id=creditor_id,
                        equivalent_id=equivalent_id,
                        amount=Decimal(amount),
                    )
                    for debt_id, debtor_id, creditor_id, amount in (
                        (debt_ids[0], a_id, b_id, "100.00"),
                        (debt_ids[1], b_id, c_id, "30.00"),
                        (debt_ids[2], c_id, a_id, "40.00"),
                    )
                ]
            )
        await setup.commit()

    return {
        "equivalent_id": equivalent_id,
        "equivalent_code": equivalent_code,
        "participant_ids": participant_ids,
        "participant_pids": participant_pids,
        "debt_ids": debt_ids,
        "cycle": [{"debt_id": str(debt_id)} for debt_id in debt_ids],
    }
