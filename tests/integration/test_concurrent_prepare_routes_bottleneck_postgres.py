import uuid
from decimal import Decimal

import pytest

from sqlalchemy import select


@pytest.mark.asyncio
async def test_concurrent_prepare_routes_shared_bottleneck_serializes_on_postgres(db_session):
    dialect = None
    try:
        dialect = db_session.get_bind().dialect.name
    except Exception:
        dialect = None

    if dialect not in {"postgresql", "postgres"}:
        pytest.skip("Postgres-only: validates pg_advisory_xact_lock serialization")

    # Local imports so sqlite runs don't import unused Postgres-only bits.
    from tests.conftest import TestingSessionLocal
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.core.payments.engine import PaymentEngine
    from sqlalchemy import delete

    # NOTE:
    # `db_session` is wrapped in an outer transaction (test isolation) and its writes are
    # not visible to other DB connections. This test needs true concurrency across
    # independent connections, so we do all setup via a separate committed session.

    async with TestingSessionLocal() as setup:
        # Ensure equivalent exists
        usd = (
            await setup.execute(select(Equivalent).where(Equivalent.code == "USD"))
        ).scalar_one_or_none()
        if not usd:
            usd = Equivalent(code="USD", description="US Dollar", precision=2)
            setup.add(usd)
            await setup.commit()
            await setup.refresh(usd)

        # Participants: A and B pay to D via shared bottleneck edge C->D.
        # Capacity for C->D is controlled by trustline D->C.
        pids = ["A_CB", "B_CB", "C_CB", "D_CB"]
        participants: list[Participant] = []
        for pid in pids:
            p = Participant(pid=pid, display_name=pid, public_key=f"pk_{pid}", type="person", status="active")
            setup.add(p)
            participants.append(p)
        await setup.commit()
        for p in participants:
            await setup.refresh(p)

        id_by_pid = {p.pid: p.id for p in participants}

        # Trustlines enabling A->C, B->C, C->D.
        # For flow X->Y we need trustline Y->X.
        setup.add_all(
            [
                # A->C enabled by C->A
                TrustLine(from_participant_id=id_by_pid["C_CB"], to_participant_id=id_by_pid["A_CB"], equivalent_id=usd.id, limit=Decimal("100.00"), status="active"),
                # B->C enabled by C->B
                TrustLine(from_participant_id=id_by_pid["C_CB"], to_participant_id=id_by_pid["B_CB"], equivalent_id=usd.id, limit=Decimal("100.00"), status="active"),
                # C->D enabled by D->C (bottleneck 10)
                TrustLine(from_participant_id=id_by_pid["D_CB"], to_participant_id=id_by_pid["C_CB"], equivalent_id=usd.id, limit=Decimal("10.00"), status="active"),
            ]
        )
        await setup.commit()

        # Create two transactions.
        tx1 = Transaction(id=uuid.uuid4(), tx_id=str(uuid.uuid4()), type="PAYMENT", initiator_id=id_by_pid["A_CB"], payload={}, state="NEW")
        tx2 = Transaction(id=uuid.uuid4(), tx_id=str(uuid.uuid4()), type="PAYMENT", initiator_id=id_by_pid["B_CB"], payload={}, state="NEW")
        setup.add_all([tx1, tx2])
        await setup.commit()

    routes1 = [(["A_CB", "C_CB", "D_CB"], Decimal("8.00"))]
    routes2 = [(["B_CB", "C_CB", "D_CB"], Decimal("8.00"))]

    async def _prepare_one(tx_id: str, routes):
        # Separate session so we get true concurrent DB behavior.
        # DO NOT wrap in an outer transaction + rollback: the second worker must be
        # able to observe the first worker's committed prepare locks.
        async with TestingSessionLocal() as session:
            eng = PaymentEngine(session)
            try:
                await eng.prepare_routes(tx_id, routes, usd.id)
                return "ok"
            except Exception as exc:
                return exc

    r1, r2 = await __import__("asyncio").gather(
        _prepare_one(tx1.tx_id, routes1),
        _prepare_one(tx2.tx_id, routes2),
    )

    # Exactly one should succeed; the other should fail due to reserved usage on the shared bottleneck.
    results = [r1, r2]
    ok_count = sum(1 for r in results if r == "ok")
    if ok_count != 1:
        summary = [
            ("ok" if r == "ok" else f"{type(r).__name__}: {r}")
            for r in results
        ]
        raise AssertionError(f"Expected exactly one success; got {ok_count}. Results: {summary}")

    from app.utils.exceptions import RoutingException

    failures = [r for r in results if r != "ok"]
    assert len(failures) == 1
    assert isinstance(failures[0], RoutingException)
    assert getattr(failures[0], "code", None) == "E002"

    # Cleanup (best-effort) to keep the shared Postgres test DB tidy.
    async with TestingSessionLocal() as cleanup:
        await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id.in_([tx1.tx_id, tx2.tx_id])))
        await cleanup.execute(delete(Transaction).where(Transaction.tx_id.in_([tx1.tx_id, tx2.tx_id])))
        await cleanup.execute(delete(TrustLine).where(TrustLine.from_participant_id.in_(list(id_by_pid.values()))))
        await cleanup.execute(delete(Participant).where(Participant.id.in_(list(id_by_pid.values()))))
        await cleanup.commit()
