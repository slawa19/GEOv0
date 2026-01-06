import uuid
from decimal import Decimal

import pytest

from app.core.clearing.service import ClearingService


class _Debt:
    def __init__(self, debtor_id: uuid.UUID, creditor_id: uuid.UUID, equivalent_id: uuid.UUID, amount: Decimal):
        self.debtor_id = debtor_id
        self.creditor_id = creditor_id
        self.equivalent_id = equivalent_id
        self.amount = amount


class _TrustLine:
    def __init__(
        self,
        *,
        from_participant_id: uuid.UUID,
        to_participant_id: uuid.UUID,
        equivalent_id: uuid.UUID,
        status: str = "active",
        policy: dict | None = None,
    ):
        self.from_participant_id = from_participant_id
        self.to_participant_id = to_participant_id
        self.equivalent_id = equivalent_id
        self.status = status
        self.policy = policy


class _ScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


class _ExecResult:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return _ScalarResult(self._items)


class _Session:
    def __init__(self, *, trustlines):
        self._trustlines = list(trustlines)

    async def execute(self, stmt, params=None):
        text = str(stmt)
        if "FROM trust_lines" in text:
            return _ExecResult(self._trustlines)
        raise AssertionError(f"Unexpected statement: {text}")


@pytest.mark.asyncio
async def test_cycle_rejected_when_any_edge_auto_clearing_false():
    eq = uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Debt cycle: A->B, B->C, C->A
    debts = [
        _Debt(debtor_id=a, creditor_id=b, equivalent_id=eq, amount=Decimal("10")),
        _Debt(debtor_id=b, creditor_id=c, equivalent_id=eq, amount=Decimal("10")),
        _Debt(debtor_id=c, creditor_id=a, equivalent_id=eq, amount=Decimal("10")),
    ]

    # Controlling trustlines are creditor->debtor for each debt edge.
    trustlines = [
        _TrustLine(from_participant_id=b, to_participant_id=a, equivalent_id=eq, policy={"auto_clearing": True}),
        _TrustLine(from_participant_id=c, to_participant_id=b, equivalent_id=eq, policy={"auto_clearing": False}),
        _TrustLine(from_participant_id=a, to_participant_id=c, equivalent_id=eq, policy={"auto_clearing": True}),
    ]

    service = ClearingService(_Session(trustlines=trustlines))
    assert await service._cycle_respects_auto_clearing(debts) is False


@pytest.mark.asyncio
async def test_cycle_allowed_when_all_edges_auto_clearing_true_or_default():
    eq = uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    debts = [
        _Debt(debtor_id=a, creditor_id=b, equivalent_id=eq, amount=Decimal("10")),
        _Debt(debtor_id=b, creditor_id=c, equivalent_id=eq, amount=Decimal("10")),
        _Debt(debtor_id=c, creditor_id=a, equivalent_id=eq, amount=Decimal("10")),
    ]

    trustlines = [
        _TrustLine(from_participant_id=b, to_participant_id=a, equivalent_id=eq, policy={"auto_clearing": True}),
        _TrustLine(from_participant_id=c, to_participant_id=b, equivalent_id=eq, policy=None),
        _TrustLine(from_participant_id=a, to_participant_id=c, equivalent_id=eq, policy={"auto_clearing": True}),
    ]

    service = ClearingService(_Session(trustlines=trustlines))
    assert await service._cycle_respects_auto_clearing(debts) is True
