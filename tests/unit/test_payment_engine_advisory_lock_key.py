import uuid

import pytest

from app.core.payments.engine import PaymentEngine


def test_segment_lock_key_is_deterministic_and_bigint_range():
    eq = uuid.uuid4()
    a = uuid.uuid4()
    b = uuid.uuid4()

    k1 = PaymentEngine._segment_lock_key(equivalent_id=eq, from_participant_id=a, to_participant_id=b)
    k2 = PaymentEngine._segment_lock_key(equivalent_id=eq, from_participant_id=a, to_participant_id=b)
    k3 = PaymentEngine._segment_lock_key(equivalent_id=eq, from_participant_id=b, to_participant_id=a)

    assert k1 == k2
    assert k1 != k3

    assert -(2**63) <= k1 <= (2**63 - 1)


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _Session:
    bind = _Bind()

    def __init__(self):
        self.keys: list[int] = []

    async def execute(self, _stmt, params=None):
        if params and "key" in params:
            self.keys.append(int(params["key"]))


@pytest.mark.asyncio
async def test_acquire_segment_advisory_locks_uses_sorted_key_order(monkeypatch):
    eq_id = uuid.uuid4()
    a_id, b_id, c_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    participant_map = {"A": a_id, "B": b_id, "C": c_id}
    routes = [(["A", "B", "C"], 1), (["A", "C"], 1)]

    # Force a non-sorted key set; the implementation must sort before executing.
    key_map = {
        (a_id, b_id): 5,
        (b_id, c_id): 2,
        (a_id, c_id): 9,
    }

    def _fake_segment_lock_key(*, equivalent_id, from_participant_id, to_participant_id) -> int:
        assert equivalent_id == eq_id
        return key_map[(from_participant_id, to_participant_id)]

    monkeypatch.setattr(PaymentEngine, "_segment_lock_key", staticmethod(_fake_segment_lock_key))

    session = _Session()
    engine = PaymentEngine(session)

    await engine._acquire_segment_advisory_locks(
        equivalent_id=eq_id,
        routes=routes,
        participant_map=participant_map,
    )

    assert session.keys == [2, 5, 9]
