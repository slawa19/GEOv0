"""T1210 finding 12: `policy.daily_limit` obeys the money grammar, not the storage capacity.

The first edition of the T1201 door routed `policy.daily_limit` through the FULL
`parse_money_amount` - wire grammar AND the `Numeric(20, 8)` capacity rule - and relabelled
every refusal as "trustline.policy.daily_limit must be a number".  Both halves were wrong in
their own way:

* the CAPACITY half guarded a column that does not exist.  `daily_limit` lives in the `policy`
  JSON column: `"0.123456789"` and `"1000000000000"` are stored verbatim, informational-only,
  and were refused anyway - a forward-looking rule for an enforcement day that has not come.
* the MESSAGE told clients their number was not a number.  A scale-9 string is a number; the
  complaint against it was capacity, and the relabelling erased that.

What SURVIVES of the first edition is the wire grammar for strings - `"1e3"`, `"NaN"`,
whitespace - because the canon (`api/openapi.yaml`) declares the field
`oneOf string|number|null` and spec 012 requires money strings to be plain decimal wherever a
client writes them.  A JSON NUMBER is a parsed value, not a client spelling, so it is
re-spelled plainly before the grammar: `1e16` arrives as a float whose `str()` is `"1e+16"`,
and refusing it for that spelling would refuse the canon's own declared type.

These are the tests the finding said did not exist.
"""

from __future__ import annotations

import pytest

from app.utils.exceptions import BadRequestException
from app.utils.validation import validate_trustline_policy


def _refusal(policy: dict) -> BadRequestException | None:
    try:
        validate_trustline_policy(policy)
    except BadRequestException as exc:
        return exc
    return None


@pytest.mark.parametrize(
    "value",
    [
        "0.123456789",  # scale 9: unrepresentable in Numeric(20, 8), stored fine in JSON
        "1000000000000",  # 10**12: past the money columns' magnitude bound, stored fine in JSON
        "100.50",
        "0",
        0,
        100.5,  # JSON number: canon declares oneOf string|number|null
        1e16,  # float whose str() is exponent notation - a value, not a client spelling
    ],
)
def test_daily_limit_accepts_what_the_json_column_stores(value: object) -> None:
    """No capacity rule on a column that has no capacity.

    MUTATION THIS CATCHES: routing `daily_limit` back through `parse_money_amount` - the
    scale-9 and magnitude cases redden first.
    """

    exc = _refusal({"daily_limit": value})
    assert exc is None, (
        f"daily_limit={value!r} was refused ({exc}): the policy JSON column stores this "
        f"verbatim and the value is informational-only, so there is no capacity to protect."
    )


@pytest.mark.parametrize("value", ["1e3", "1E-3", "NaN", "Infinity", " 100", "abc", ""])
def test_daily_limit_strings_still_obey_the_money_wire_grammar(value: str) -> None:
    """The half of the first edition that must NOT move: client-written strings stay strict.

    And the refusal is the grammar's own ("Invalid amount format", naming the field in
    `details`), not the "must be a number" relabelling T1210 flagged.
    """

    exc = _refusal({"daily_limit": value})
    assert exc is not None, f"daily_limit={value!r} must be refused by the wire grammar"
    assert "must be a number" not in str(exc.message), (
        f"daily_limit={value!r} got {exc.message!r}: a grammar/form complaint relabelled as a "
        f"type complaint is the defect this finding recorded."
    )
    assert (exc.details or {}).get("field") == "policy.daily_limit", (
        f"the refusal must name the offending field; got details={exc.details!r}"
    )


@pytest.mark.parametrize("value", [True, {}, [], object()])
def test_daily_limit_non_numeric_types_get_the_type_message(value: object) -> None:
    """"must be a number" is reserved for values that are not numbers."""

    exc = _refusal({"daily_limit": value})
    assert exc is not None and "must be a number" in str(exc.message), (
        f"daily_limit={value!r} is not a number and must say so; got {exc}"
    )


@pytest.mark.parametrize("value", ["-1", -5, -0.5])
def test_daily_limit_stays_non_negative(value: object) -> None:
    exc = _refusal({"daily_limit": value})
    assert exc is not None and "must be >= 0" in str(exc.message), (
        f"daily_limit={value!r} must keep the pre-T1201 non-negativity rule; got {exc}"
    )


def test_max_hop_usage_keeps_the_looser_rule() -> None:
    """`max_hop_usage` is a hop count, not money: numbers and numeric strings, `>= 0`."""

    assert _refusal({"max_hop_usage": 3}) is None
    assert _refusal({"max_hop_usage": "3"}) is None
    exc = _refusal({"max_hop_usage": -1})
    assert exc is not None and "must be >= 0" in str(exc.message)
