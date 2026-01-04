import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.clearing.service import ClearingService


class _Equivalent:
    def __init__(self, id_: uuid.UUID, code: str):
        self.id = id_
        self.code = code


class _Debt:
    def __init__(self, id_: uuid.UUID, debtor_id: uuid.UUID, creditor_id: uuid.UUID, equivalent_id: uuid.UUID, amount: Decimal):
        self.id = id_
        self.debtor_id = debtor_id
        self.creditor_id = creditor_id
        self.equivalent_id = equivalent_id
        self.amount = amount


class _Lock:
    def __init__(self, expires_at: datetime, effects: dict):
        self.expires_at = expires_at
        self.effects = effects


class _ScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)

    def scalar_one_or_none(self):
        if not self._items:
            return None
        if len(self._items) != 1:
            raise AssertionError("Expected exactly one row")
        return self._items[0]


class _ExecResult:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return _ScalarResult(self._items)

    def scalar_one_or_none(self):
        if not self._items:
            return None
        if len(self._items) != 1:
            raise AssertionError("Expected exactly one row")
        return self._items[0]


class _Session:
    def __init__(self, *, equivalent, debts, locks, participants):
        self._equivalent = equivalent
        self._debts = debts
        self._locks = locks
        self._participants = participants

    async def execute(self, stmt):
        text = str(stmt)
        if "FROM equivalents" in text:
            return _ExecResult([self._equivalent])
        if "FROM debts" in text:
            # clearing query filters amount > 0 in SQL; we prefilter here
            return _ExecResult([d for d in self._debts if d.amount > 0])
        if "FROM prepare_locks" in text:
            return _ExecResult(self._locks)
        if "FROM participants" in text:
            return _ExecResult(self._participants)
        raise AssertionError(f"Unexpected statement: {text}")


@pytest.mark.asyncio
async def test_find_cycles_excludes_edges_with_active_prepare_locks():
    eq_id = uuid.uuid4()
    eq = _Equivalent(eq_id, "USD")

    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Cycle A->B->C->A
    debts = [
        _Debt(uuid.uuid4(), a, b, eq_id, Decimal("10")),
        _Debt(uuid.uuid4(), b, c, eq_id, Decimal("10")),
        _Debt(uuid.uuid4(), c, a, eq_id, Decimal("10")),
    ]

    # Active prepare lock touches A<->B, so that edge must be excluded and cycle should not be found.
    lock = _Lock(
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        effects={
            "flows": [
                {
                    "from": str(a),
                    "to": str(b),
                    "equivalent": str(eq_id),
                    "amount": "1",
                }
            ]
        },
    )

    class _ParticipantRow:
        def __init__(self, id_: uuid.UUID, pid: str):
            self.id = id_
            self.pid = pid

    participants = [_ParticipantRow(a, "A"), _ParticipantRow(b, "B"), _ParticipantRow(c, "C")]
    session = _Session(equivalent=eq, debts=debts, locks=[lock], participants=participants)
    service = ClearingService(session)

    cycles = await service.find_cycles("USD", max_depth=6)
    assert cycles == []
