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
    from tests.conftest import engine, TestingSessionLocal
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.trustline import TrustLine
    from app.db.models.transaction import Transaction
    from app.core.payments.engine import PaymentEngine

    # Ensure equivalent exists
    result = await db_session.execute(select(Equivalent).where(Equivalent.code == "USD"))
    usd = result.scalar_one_or_none()
    if not usd:
        usd = Equivalent(code="USD", description="US Dollar", precision=2)
        db_session.add(usd)
        await db_session.commit()
        await db_session.refresh(usd)

    # Participants: A and B pay to D via shared bottleneck edge C->D.
    # Capacity for C->D is controlled by trustline D->C.
    pids = ["A_CB", "B_CB", "C_CB", "D_CB"]
    participants = []
    for pid in pids:
        p = Participant(pid=pid, display_name=pid, public_key=f"pk_{pid}", type="person", status="active")
        db_session.add(p)
        participants.append(p)
    await db_session.commit()
    for p in participants:
        await db_session.refresh(p)

    id_by_pid = {p.pid: p.id for p in participants}

    # Trustlines enabling A->C, B->C, C->D.
    # For flow X->Y we need trustline Y->X.
    db_session.add_all(
        [
            # A->C enabled by C->A
            TrustLine(from_participant_id=id_by_pid["C_CB"], to_participant_id=id_by_pid["A_CB"], equivalent_id=usd.id, limit=Decimal("100.00"), status="active"),
            # B->C enabled by C->B
            TrustLine(from_participant_id=id_by_pid["C_CB"], to_participant_id=id_by_pid["B_CB"], equivalent_id=usd.id, limit=Decimal("100.00"), status="active"),
            # C->D enabled by D->C (bottleneck 10)
            TrustLine(from_participant_id=id_by_pid["D_CB"], to_participant_id=id_by_pid["C_CB"], equivalent_id=usd.id, limit=Decimal("10.00"), status="active"),
        ]
    )
    await db_session.commit()

    # Create two transactions.
    tx1 = Transaction(id=uuid.uuid4(), tx_id=str(uuid.uuid4()), type="PAYMENT", initiator_id=id_by_pid["A_CB"], payload={}, state="NEW")
    tx2 = Transaction(id=uuid.uuid4(), tx_id=str(uuid.uuid4()), type="PAYMENT", initiator_id=id_by_pid["B_CB"], payload={}, state="NEW")
    db_session.add_all([tx1, tx2])
    await db_session.commit()

    routes1 = [(["A_CB", "C_CB", "D_CB"], Decimal("8.00"))]
    routes2 = [(["B_CB", "C_CB", "D_CB"], Decimal("8.00"))]

    async def _prepare_one(tx_id: str, routes):
        # Separate connection/session so we get true concurrent DB behavior.
        async with engine.connect() as conn:
            trans = await conn.begin()
            async with TestingSessionLocal(bind=conn) as session:
                await session.begin_nested()
                eng = PaymentEngine(session)
                try:
                    await eng.prepare_routes(tx_id, routes, usd.id)
                    return "ok"
                except Exception as exc:
                    return exc
                finally:
                    await trans.rollback()

    r1, r2 = await __import__("asyncio").gather(
        _prepare_one(tx1.tx_id, routes1),
        _prepare_one(tx2.tx_id, routes2),
    )

    # Exactly one should succeed; the other should fail due to reserved usage on the shared bottleneck.
    results = [r1, r2]
    ok_count = sum(1 for r in results if r == "ok")
    assert ok_count == 1

    from app.utils.exceptions import RoutingException

    failures = [r for r in results if r != "ok"]
    assert len(failures) == 1
    assert isinstance(failures[0], RoutingException)
    assert getattr(failures[0], "code", None) == "E002"
