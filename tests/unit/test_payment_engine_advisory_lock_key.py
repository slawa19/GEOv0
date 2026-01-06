import uuid

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
