import uuid
from decimal import Decimal

import pytest

from app.core.payments.engine import PaymentEngine


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Session:
    bind = _Bind()

    def __init__(self):
        self.executed = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), dict(params or {})))


@pytest.mark.asyncio
async def test_acquire_segment_advisory_locks_executes_pg_advisory_xact_lock_for_each_unique_segment():
    session = _Session()
    engine = PaymentEngine(session)

    eq = uuid.uuid4()
    a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    participant_map = {"A": a, "B": b, "C": c, "D": d}

    routes = [
        (["A", "B", "D"], Decimal("5")),
        (["A", "C", "D"], Decimal("5")),
    ]

    await engine._acquire_segment_advisory_locks(
        equivalent_id=eq,
        routes=routes,
        participant_map=participant_map,
    )

    # Unique segments across the two routes: A->B, B->D, A->C, C->D.
    assert len(session.executed) == 4
    assert all("pg_advisory_xact_lock" in sql for sql, _params in session.executed)
    assert all("key" in params for _sql, params in session.executed)
