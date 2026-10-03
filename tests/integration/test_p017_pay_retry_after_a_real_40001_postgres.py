"""The payment retry after a REAL `40001` whose attempt lost its connection: the re-run is on a FRESH session.

017 stage 3, slice S2a, pinned the engine's unit-of-work retry: `PaymentEngine._run_uow_with_retry`
rolled the SAME session back before re-running, and when that rollback failed it re-raised instead of
re-running - a second attempt on a session nobody rolled back would build on whatever the first one left
behind. The engine is gone since programme 019 stage 4 (manifest `t1901`, 5.1, row of the dropped
`test_p017_uow_retry_after_a_real_40001_postgres.py`); the owner of the API retries is
`PaymentService.pay()`, and the property it must keep is the same one, reached differently:

* every attempt runs on its OWN session from the injected factory, so the re-run never shares a
  transaction with the failed attempt;
* the failed attempt's transaction is ended - its operation savepoint rolled back or, when that fails,
  its connection invalidated (`_abandon_operation`), so the server discards it and it cannot commit -
  before the next attempt starts (`_end_failed_attempt`); when even that cannot be established the
  retry is not taken.

THE FAILURE IS REAL, as in the 017 original: the attempt runs a statement (so the driver really has a
transaction), then the driver connection is closed under the session and the attempt fails with a
GENUINE PostgreSQL serialization failure provoked by two SERIALIZABLE transactions. The rollback of the
operation savepoint then fails for real ("the underlying connection is closed"). The control runs the
same `40001` on a healthy connection.

MUTATION that must redden the first test: make `pay()` reuse the failed attempt's session for the re-run
(the second attempt then meets the closed connection, or its leftovers).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.payments.service import PaymentService, _payment_db_sqlstate
from app.db.models.transaction import Transaction
from app.schemas.payment import PaymentCreateRequest
from tests.integration.test_p015_p1_money_replay_postgres import (
    _OPENING,
    _debts,
    _forget_the_route_cache,
    _seed,
)


@pytest_asyncio.fixture
async def factory(committed_database):
    engine = create_async_engine(
        committed_database.url, pool_size=5, max_overflow=0, isolation_level="SERIALIZABLE"
    )
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE p017_retry_probe (id INTEGER PRIMARY KEY, v TEXT)"))
        await conn.execute(text("INSERT INTO p017_retry_probe (id, v) VALUES (1, 'seed')"))
    try:
        yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


async def _provoke_a_serialization_failure(factory) -> DBAPIError:
    """A REAL `40001`: the reader's snapshot predates a committed update of the row it then updates."""
    async with factory() as reader, factory() as writer:
        await reader.execute(text("SELECT v FROM p017_retry_probe WHERE id = 1"))
        await writer.execute(text("UPDATE p017_retry_probe SET v = 'writer' WHERE id = 1"))
        await writer.commit()
        with pytest.raises(DBAPIError) as conflict:
            await reader.execute(text("UPDATE p017_retry_probe SET v = 'reader' WHERE id = 1"))
        await reader.rollback()
    return conflict.value


def _request(world) -> PaymentCreateRequest:
    return PaymentCreateRequest(
        tx_id=str(uuid.uuid4()),
        to=world.receiver.pid,
        equivalent=world.equivalent.code,
        amount="3.00",
        signature="__internal__",
    )


async def _rows(factory, tx_id: str) -> list[str]:
    async with factory() as fresh:
        return list(
            (await fresh.execute(select(Transaction.state).where(Transaction.tx_id == tx_id))).scalars()
        )


@pytest.mark.parametrize("break_the_connection", [True, False], ids=["connection_lost", "control"])
@pytest.mark.asyncio
async def test_a_real_40001_is_retried_on_a_fresh_session_and_the_payment_lands_once(
    factory, monkeypatch, break_the_connection
) -> None:
    conflict = await _provoke_a_serialization_failure(factory)
    # PREMISE, on the error itself: a genuine serialization failure.
    assert _payment_db_sqlstate(conflict) == "40001", conflict

    world = await _seed(factory)
    original = PaymentService._bind_payment
    sessions: list[AsyncSession] = []

    async def first_attempt_fails(self, *args, **kwargs):
        sessions.append(self.session)
        if len(sessions) == 1:
            if break_the_connection:
                # A statement first, so the driver really has a transaction (017, measured 2026-09-24).
                await self.session.execute(text("SELECT 1"))
                connection = await self.session.connection()
                raw = await connection.get_raw_connection()
                await raw.driver_connection.close()
            raise conflict
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(PaymentService, "_bind_payment", first_attempt_fails)
    request = _request(world)
    try:
        result = await PaymentService.pay(factory, world.sender.id, request, require_signature=False)
    finally:
        _forget_the_route_cache(world)

    assert len(sessions) == 2, f"expected exactly one re-run after a real 40001, got {len(sessions)} attempt(s)"
    assert sessions[0] is not sessions[1], (
        "the re-run used the failed attempt's session: it would build on a transaction nobody rolled back"
    )
    assert result.status == "COMMITTED", result
    assert await _rows(factory, request.tx_id) == ["COMMITTED"]
    assert await _debts(factory, world) == {
        (world.sender.pid, world.receiver.pid): _OPENING + Decimal("3.00")
    }, "the payment moved money other than exactly once"
    async with factory() as fresh:
        # Nothing of the failed attempt is left in the probe table's neighbourhood either: one row only.
        assert await fresh.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.tx_id == request.tx_id)
        ) == 1
