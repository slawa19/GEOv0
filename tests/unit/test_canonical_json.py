from decimal import Decimal

from app.core.auth.canonical import canonical_json


def test_canonical_json_is_deterministic_and_compact():
    payload1 = {"b": 1, "a": {"y": 2, "x": 1}}
    payload2 = {"a": {"x": 1, "y": 2}, "b": 1}

    out1 = canonical_json(payload1)
    out2 = canonical_json(payload2)

    assert out1 == out2
    assert out1 == b'{"a":{"x":1,"y":2},"b":1}'


def test_canonical_json_normalizes_decimal_string_form():
    payload = {"amount": Decimal("100.00"), "small": Decimal("0.0100"), "zero": Decimal("0")}
    out = canonical_json(payload)

    # Decimals are represented deterministically without trailing zeros.
    assert out == b'{"amount":"100","small":"0.01","zero":"0"}'
