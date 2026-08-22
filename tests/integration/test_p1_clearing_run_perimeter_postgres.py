"""RT-010-4 on PostgreSQL: the perimeter must hold on the interlock path too.

Program 010, finding `F-010-3`.

The SQLite case (`tests/unit/test_p1_clearing_run_perimeter.py`) proves detection and the
execution guard on the simple path: `execute_clearing_with_amount` returns straight to
`_execute_clearing_with_amount` for any non-PostgreSQL dialect
(`app/core/clearing/service.py:1160-1163`).  PostgreSQL takes a different route entirely -
preflight read, rollback, a pinned interlock connection, an advisory lock, and only then the
private executor with `interlocked_equivalent_id` set.  That is a separate transition, and a
guard that is not carried across it is a guard with a hole.

The stand therefore owns its engine.  It has to: PostgreSQL clearing explicitly refuses a
session bound to an externally owned connection (`app/core/clearing/service.py:1130-1140`),
which is exactly what the shared fixture provides on this tier.

Because the commits here are real, the test removes its own rows afterwards instead of
leaving them in a shared database.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.clearing.service import ClearingService
from app.db.models.debt import Debt
from app.db.models.transaction import Transaction
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.utils.exceptions import GeoException

pytestmark = pytest.mark.postgres


def _url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if "postgresql" not in url:
        pytest.skip("the interlock path exists on PostgreSQL only")
    return url


@pytest_asyncio.fixture
async def engine_bound_sessions():
    engine = create_async_engine(_url())
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(sessionmaker) -> tuple[str, uuid.UUID, dict[str, uuid.UUID]]:
    """Six participants; the debt cycle belongs to b1/b2/b3 only."""

    nonce = uuid.uuid4().hex[:8]
    eq_code = ("C" + nonce).upper()[:10]
    ids: dict[str, uuid.UUID] = {}

    async with sessionmaker() as s:
        eq = Equivalent(code=eq_code, precision=2, is_active=True)
        s.add(eq)
        await s.flush()
        eq_id = eq.id

        for name in ("a1", "a2", "a3", "b1", "b2", "b3"):
            pid = f"{name}-{nonce}"
            p = Participant(
                id=uuid.uuid4(),
                pid=pid,
                display_name=name.upper(),
                public_key=f"pk-{name}-{nonce}",
                type="person",
                status="active",
                profile={},
            )
            ids[name] = p.id
            ids[f"pid:{name}"] = pid  # type: ignore[assignment]
            s.add(p)

        for debtor, creditor in (("b1", "b2"), ("b2", "b3"), ("b3", "b1")):
            s.add(
                TrustLine(
                    from_participant_id=ids[creditor],
                    to_participant_id=ids[debtor],
                    equivalent_id=eq_id,
                    limit=Decimal("1000"),
                    policy={"auto_clearing": True},
                    status="active",
                )
            )
            s.add(
                Debt(
                    debtor_id=ids[debtor],
                    creditor_id=ids[creditor],
                    equivalent_id=eq_id,
                    amount=Decimal("100"),
                )
            )
        await s.commit()

    return eq_code, eq_id, ids


async def _amounts(sessionmaker, eq_id) -> list[Decimal]:
    async with sessionmaker() as s:
        rows = (
            await s.execute(select(Debt.amount).where(Debt.equivalent_id == eq_id))
        ).scalars().all()
    return sorted(Decimal(str(a)) for a in rows)


async def _cleanup(sessionmaker, eq_id, ids) -> None:
    participant_ids = [v for k, v in ids.items() if not k.startswith("pid:")]
    async with sessionmaker() as s:
        await s.execute(delete(Debt).where(Debt.equivalent_id == eq_id))
        await s.execute(delete(TrustLine).where(TrustLine.equivalent_id == eq_id))
        # A successful clearing writes a CLEARING transaction whose initiator is one of
        # these participants, and the FK is ondelete=RESTRICT.
        await s.execute(
            delete(Transaction).where(Transaction.initiator_id.in_(participant_ids))
        )
        await s.execute(delete(Participant).where(Participant.id.in_(participant_ids)))
        await s.execute(delete(Equivalent).where(Equivalent.id == eq_id))
        await s.commit()


@pytest.mark.asyncio
async def test_interlock_path_refuses_a_cycle_outside_the_perimeter(
    engine_bound_sessions,
) -> None:
    sessionmaker = engine_bound_sessions
    eq_code, eq_id, ids = await _seed(sessionmaker)
    foreign_scope = {ids["pid:a1"], ids["pid:a2"], ids["pid:a3"]}  # type: ignore[index]

    try:
        async with sessionmaker() as session:
            service = ClearingService(session)

            # Detection without a perimeter, so the cycle really exists and the guard is
            # what refuses it - not an empty candidate list.
            cycles = await service.find_cycles(eq_code, max_depth=6)
            assert len(cycles) == 1, f"the stand must hold exactly one cycle: {cycles}"

            with pytest.raises(GeoException):
                await service.execute_clearing_with_amount(
                    cycles[0], allowed_participant_pids=foreign_scope
                )

        after = await _amounts(sessionmaker, eq_id)
        assert after == [Decimal("100")] * 3, (
            f"the refused clearing still changed another run's debts: {after}"
        )
    finally:
        await _cleanup(sessionmaker, eq_id, ids)


@pytest.mark.asyncio
async def test_interlock_path_still_clears_for_the_owning_run(
    engine_bound_sessions,
) -> None:
    """Anti-vacuum on the same path: the guard must refuse strangers, not everyone."""

    sessionmaker = engine_bound_sessions
    eq_code, eq_id, ids = await _seed(sessionmaker)
    own_scope = {ids["pid:b1"], ids["pid:b2"], ids["pid:b3"]}  # type: ignore[index]

    try:
        async with sessionmaker() as session:
            service = ClearingService(session)
            cycles = await service.find_cycles(
                eq_code, max_depth=6, allowed_participant_pids=own_scope
            )
            assert len(cycles) == 1, f"the owning run must still see its cycle: {cycles}"

            cleared = await service.execute_clearing_with_amount(
                cycles[0], allowed_participant_pids=own_scope
            )

        assert cleared == Decimal("100"), cleared
        after = await _amounts(sessionmaker, eq_id)
        assert after == [], f"the owning run's cycle should be gone, got {after}"
    finally:
        await _cleanup(sessionmaker, eq_id, ids)


@pytest.mark.asyncio
async def test_the_expanding_bind_works_on_postgresql(engine_bound_sessions) -> None:
    """The SQL predicate must actually run here, not be masked by the DFS fallback.

    `find_cycles` wraps the whole SQL block in a broad `except` and falls through to the DFS
    producer, whose load is narrowed too - so a scoped-SQL that fails on this dialect still
    yields a correct result, silently, after loading every debt of the equivalent.  The
    binding is the dialect-specific part: raw `text()` needs an expanding bind for `IN`, and
    the UUIDs go through `_bind_uuid`, which behaves differently on SQLite.  So the producer
    is called directly here.
    """

    sessionmaker = engine_bound_sessions
    _eq_code, eq_id, ids = await _seed(sessionmaker)

    try:
        async with sessionmaker() as session:
            service = ClearingService(session)

            unscoped = await service.find_triangles_sql(eq_id)
            assert len(unscoped) == 3, (
                f"one triangle, one row per starting vertex: {unscoped}"
            )

            foreign = {ids["a1"], ids["a2"], ids["a3"]}
            assert await service.find_triangles_sql(
                eq_id, allowed_participant_ids=foreign
            ) == [], "the SQL producer returned another run's cycle"

            own = {ids["b1"], ids["b2"], ids["b3"]}
            assert len(
                await service.find_triangles_sql(eq_id, allowed_participant_ids=own)
            ) == 3, "the predicate rejected the owning run as well"
    finally:
        await _cleanup(sessionmaker, eq_id, ids)
