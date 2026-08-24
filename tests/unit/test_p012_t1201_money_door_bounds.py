"""T1201: the money door judges the VALUE, and both of its bounds are pinned here.

WHY THIS MODULE EXISTS. `T1201` shipped a door with two bounds and a predicate that was
documented as "the predicate form of `parse_money_amount`". Two external measurements on the
result:

1. **They disagreed, and the door was the wrong one of the two.** The door bounded the *lexical
   scale of the string*; `is_storable_money` bounded the *value*. So `"0.100000000"`,
   `"1000.0000000000"` and `"1E+3"` were refused with 400/E009 while `Numeric(20, 8)` holds each
   of them exactly and the predicate said so. That is a compatibility break on the primary money
   API, and the programme inflicted it on itself twice over: `to_money_str` treats
   `Equivalent.precision` as a MINIMUM number of fraction digits and `precision` is declared
   `ge=0, le=18`, so for any equivalent with `precision > 8` the renderer emits a string its own
   door then rejected; and `POST /trustlines` refused `{"limit": "1e3"}` while accepting
   `{"limit": 1e3}`.

2. **The magnitude bound had no test in either direction.** Mutation on the PostgreSQL tier:
   `MONEY_MAX_INTEGER_DIGITS` 12 -> 50 left all nine 012 tests green, while `MONEY_MAX_SCALE`
   8 -> 18 correctly reddened four. `is_storable_money` had no test at all - four call sites,
   and a scan of 187 tracked JSON files found zero values that would trip it, so it had no
   indirect coverage either.

Every test below therefore comes with the mutation it catches, written next to it. The
constants are read from `app.utils.validation` at call time, so `monkeypatch.setattr` on the
module IS the mutation - these are not descriptions of a flip, they perform it.

WHAT IS DELIBERATELY NOT HERE. Whether `Numeric(20, 8)` really rounds rather than truncates is a
fact about PostgreSQL and is measured on the PostgreSQL tier
(`tests/integration/test_p012_rt1_*`), because SQLite does not round on write at all
(`tests/unit/test_p012_numeric_scale_rounding_is_invisible_on_sqlite.py`). This module asserts
only what the door does, which is backend-independent.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.utils import validation
from app.utils.exceptions import BadRequestException
from app.utils.money import to_money_str
from app.utils.validation import (
    MONEY_MAX_INTEGER_DIGITS,
    MONEY_MAX_LEXICAL_SCALE,
    MONEY_MAX_SCALE,
    is_storable_money,
    parse_money_amount,
)

# `Debt.amount` / `TrustLine.limit`, i.e. `Numeric(20, 8)`: 8 fraction digits and 20 - 8 = 12
# integer digits. Written here as the two halves of the column declaration rather than as the
# constants under test, so that a mutation of the constants cannot silently move the expectation
# with itself.
COLUMN_PRECISION = 20
COLUMN_SCALE = 8
COLUMN_INTEGER_DIGITS = COLUMN_PRECISION - COLUMN_SCALE


def _verdict(amount: object) -> Decimal | None:
    """The door's answer as a value, or None for a refusal."""

    try:
        return parse_money_amount(amount)
    except BadRequestException:
        return None


# --------------------------------------------------------------------------------------------
# Finding 1: the door and the predicate must give the same answer.
# --------------------------------------------------------------------------------------------

# Plain decimal strings only, and all within the lexical length bound, so the wire grammar has
# no opinion on any of them: whatever verdict the door reaches, it reaches by the capacity rule.
_PLAIN_DECIMALS = [
    # exactly representable at scale 8, spelled in every way the wire allows
    "0",
    "0.0",
    "0.00000000",
    "1",
    "1.0",
    "0001.23",
    "0.05",
    "0.1",
    "0.10000000",
    "0.100000000",
    "0.1000000000000000",
    "1000.0000000000",
    "0.12345678",
    "-0.12345678",
    "-0.100000000",
    "999999999999.99999999",
    "999999999999",
    # NOT representable: a ninth significant fraction digit
    "0.123456789",
    "0.000000001",
    "0.123456789012345678",
    "-0.123456789",
    "999999999999.999999999",
    # NOT representable: thirteen integer digits
    "1000000000000",
    "1000000000000.5",
    "-1000000000000",
    "12345678901234567890",
]


@pytest.mark.parametrize("amount", _PLAIN_DECIMALS)
def test_the_door_and_the_predicate_return_the_same_verdict(amount: str) -> None:
    """`parse_money_amount` accepts exactly what `is_storable_money` calls storable.

    The docstring of `is_storable_money` calls itself the predicate form of the door. That is
    either true or one of the two is wrong, and until this test existed it was false for
    `"0.100000000"`, `"1000.0000000000"` and `"1E+3"` - all three refused by the door and all
    three held exactly by `Numeric(20, 8)`.

    MUTATION THIS CATCHES: put the lexical bound back into the door, i.e. pass
    `max_scale=MONEY_MAX_SCALE` to `parse_amount_decimal` in `parse_money_amount`. Measured:
    five of these cases flip to a refusal the predicate disagrees with.
    """

    accepted = _verdict(amount) is not None
    storable = is_storable_money(Decimal(amount))

    assert accepted == storable, (
        f"the door and `is_storable_money` disagree about {amount!r}: the door "
        f"{'accepted' if accepted else 'refused'} it and the predicate says it is "
        f"{'storable' if storable else 'not storable'}. They are documented as the same rule "
        f"and they are applied to the same columns - the writers that cannot raise a 400 "
        f"(scenario seeding, inject execution) use the predicate, and HTTP callers use the "
        f"door. A disagreement means one class of caller is refused what the other stores, or "
        f"stores what the other refuses."
    )


@pytest.mark.parametrize(
    "amount",
    [
        "0.100000000",
        "1000.0000000000",
        "0.1000000000000000",
        "0.10000000",
        "1.0",
        "0001.23",
    ],
)
def test_trailing_zeros_do_not_make_a_value_unstorable(amount: str) -> None:
    """`0.1` written with trailing zeros is still `0.1`, and the column still holds it.

    This is the compatibility break the first edition shipped, stated in the direction a caller
    experiences it: these strings were accepted before `T1201` and after this fix, and were
    400/E009 in between.

    MUTATION THIS CATCHES: the same one - a lexical `max_scale=8` in the door.
    """

    value = _verdict(amount)
    assert value is not None, (
        f"{amount!r} was refused. Its VALUE is {Decimal(amount)}, which "
        f"Numeric({COLUMN_PRECISION}, {COLUMN_SCALE}) stores exactly; only its spelling is "
        f"long. `## Intended` licenses refusing the value that cannot be stored exactly, not "
        f"the spelling that is longer than necessary."
    )
    assert value == Decimal(amount), (
        f"the door returned {value!r} for {amount!r}, which is a different number. The door "
        f"validates; it must not renormalise a value that will be signed or stored."
    )


@pytest.mark.parametrize("precision", list(range(0, 19)))
@pytest.mark.parametrize(
    "value",
    ["0", "0.05", "0.1", "10.25", "0.00000001", "999999999999.99999999", "-0.05"],
)
def test_the_renderer_output_is_admitted_by_the_door(value: str, precision: int) -> None:
    """Whatever `to_money_str` prints for a storable value, the door takes back.

    The two are the same programme's output and input sides, and they were not talking to each
    other: `Equivalent.precision` is declared `ge=0, le=18` and `to_money_str` treats it as a
    MINIMUM, so a `precision: 10` equivalent rendered `0.1` as `"0.1000000000"` and the door
    answered 400/E009 to it. Stated over the whole declared range of `precision` rather than on
    an example, because the example would have been picked after the fact.

    MUTATION THIS CATCHES: a lexical `max_scale=8` in the door - every `precision > 8` case
    reddens.
    """

    rendered = to_money_str(Decimal(value), precision)
    parsed = _verdict(rendered)

    assert parsed is not None, (
        f"`to_money_str({value!r}, {precision})` produced {rendered!r} and the door refused it. "
        f"The renderer's output goes on the wire and comes back as input (an admin UI round "
        f"trip, a scenario replay), so a door that refuses it makes the system reject its own "
        f"output."
    )
    assert parsed == Decimal(value), (
        f"round-tripping {value!r} at precision {precision} gave {parsed!r} via {rendered!r}"
    )


# --------------------------------------------------------------------------------------------
# Finding 4a: the magnitude bound, in both directions.
# --------------------------------------------------------------------------------------------


def test_the_largest_amount_the_column_can_hold_is_admitted() -> None:
    """The bound is the column's, so the column's maximum must pass.

    MUTATION THIS CATCHES: `MONEY_MAX_INTEGER_DIGITS` 12 -> 11 (or any narrowing), which would
    start refusing money the ledger can hold.
    """

    largest = "9" * COLUMN_INTEGER_DIGITS + "." + "9" * COLUMN_SCALE
    assert _verdict(largest) == Decimal(largest), (
        f"{largest!r} is the largest value Numeric({COLUMN_PRECISION}, {COLUMN_SCALE}) can "
        f"hold and the door refused it. Narrowing the door below the column's capacity is not "
        f"conservative, it is a second defect of the same family as F-012-1: money that exists "
        f"cannot be expressed."
    )


@pytest.mark.parametrize(
    "amount",
    ["1000000000000", "1000000000000.5", "-1000000000000", "12345678901234567890"],
)
def test_a_magnitude_the_column_cannot_hold_is_refused_at_the_door(amount: str) -> None:
    """One integer digit too many is a 400, not a 500 from inside the commit.

    `Numeric(20, 8)` raises `numeric field overflow` on write, deep inside the transaction, and
    it escapes to the caller as a bare HTTP 500 - the sender is told the service broke rather
    than that the amount was unrepresentable. `MONEY_MAX_INTEGER_DIGITS` exists to convert that
    into a 400 naming the field, and until now nothing tested it: the reviewer's mutation
    `12 -> 50` left every 012 test green.

    The 500 itself is measured over HTTP against a real PostgreSQL column in
    `tests/integration/test_p012_t1201_magnitude_bound_is_load_bearing_postgres.py`; here we pin
    only the door's verdict, which is backend-independent.

    MUTATION THIS CATCHES: `MONEY_MAX_INTEGER_DIGITS` 12 -> 50, performed below.
    """

    assert _verdict(amount) is None, (
        f"{amount!r} has more than {COLUMN_INTEGER_DIGITS} integer digits, which is what is "
        f"left of Numeric({COLUMN_PRECISION}, {COLUMN_SCALE}) once the scale is spent. The "
        f"door admitted it, so it will reach the INSERT and come back as a 500."
    )

    with pytest.raises(BadRequestException) as excinfo:
        parse_money_amount(amount, field="amount")
    details = getattr(excinfo.value, "details", None) or {}
    assert details.get("max_integer_digits") == MONEY_MAX_INTEGER_DIGITS, (
        f"the refusal of {amount!r} must name the magnitude bound it broke, so that a client "
        f"can tell a fraction it cannot keep from a magnitude it cannot keep; got {details!r}."
    )


@pytest.mark.parametrize(
    "amount",
    ["1000000000000", "1000000000000.5", "12345678901234567890"],
)
def test_counter_check_widening_the_magnitude_bound_admits_what_the_column_refuses(
    monkeypatch: pytest.MonkeyPatch, amount: str
) -> None:
    """The mutation the reviewer ran, executed instead of described.

    `MONEY_MAX_INTEGER_DIGITS` 12 -> 50 left all nine 012 tests green, which is what "the bound
    has no test" means. With the two tests above in place the mutation now reddens them; this
    one proves the mutation is real, i.e. that the constant is what decides these verdicts and
    the tests above are not passing for some unrelated reason.
    """

    monkeypatch.setattr(validation, "MONEY_MAX_INTEGER_DIGITS", 50)

    assert _verdict(amount) == Decimal(amount), (
        f"with the magnitude bound widened to 50 digits the door still refuses {amount!r}, so "
        f"the bound is not what decides it and "
        f"`test_a_magnitude_the_column_cannot_hold_is_refused_at_the_door` proves nothing about "
        f"`MONEY_MAX_INTEGER_DIGITS`."
    )


def test_counter_check_widening_the_scale_bound_admits_what_the_column_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same demonstration for the fraction half, on the constant the door actually reads.

    `MONEY_MAX_SCALE` 8 -> 18 is the mutation that re-opens `F-012-1`: the door starts admitting
    amounts PostgreSQL will round on write, so a participant signs one number and the ledger
    keeps another. (The end-to-end consequence is asserted on the PostgreSQL tier, where the
    rounding is real; here we show only that the constant is load-bearing at the door.)
    """

    assert _verdict("0.123456789") is None, "precondition: the door refuses a ninth fraction digit"

    monkeypatch.setattr(validation, "MONEY_MAX_SCALE", 18)

    assert _verdict("0.123456789") == Decimal("0.123456789"), (
        "with MONEY_MAX_SCALE widened to 18 the door still refuses 0.123456789, so the door no "
        "longer reads that constant and every test in this file that claims sensitivity to it "
        "is claiming too much."
    )


# --------------------------------------------------------------------------------------------
# Finding 4b: `is_storable_money` itself - four call sites, zero tests.
# --------------------------------------------------------------------------------------------

_STORABLE = [
    Decimal("0"),
    Decimal("0.05"),
    Decimal("0.12345678"),
    Decimal("-0.12345678"),
    Decimal("0.100000000"),  # trailing zeros beyond scale 8: same value, still storable
    Decimal("1E+3"),  # exponent form: a spelling, not a capacity question
    Decimal("1E-8"),
    Decimal("999999999999.99999999"),
    "0.05",  # the predicate accepts what config hands it, not only Decimals
    10,
]

_NOT_STORABLE = [
    Decimal("0.123456789"),  # PostgreSQL would round this
    Decimal("1E-9"),
    Decimal("-0.000000001"),
    Decimal("1000000000000"),  # numeric field overflow
    Decimal("1E+30"),  # far past the point where `quantize` itself would raise
    Decimal("NaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
    "not a number",
    None,
    object(),
]


@pytest.mark.parametrize("value", _STORABLE, ids=[str(v) for v in _STORABLE])
def test_is_storable_money_accepts_what_the_column_keeps_unchanged(value: object) -> None:
    """The predicate's positive half, which nothing exercised.

    An independent scan of 187 tracked JSON files found zero values that would trip this
    predicate, so its four call sites (`inject_executor.py` x3, `real_scenario_seeder.py`) gave
    it no indirect coverage either: it could have been `return True` and the suite would not
    have noticed.

    MUTATION THIS CATCHES: `return False` at the top, or narrowing either constant - e.g.
    `MONEY_MAX_INTEGER_DIGITS` 12 -> 11 reddens the `999999999999.99999999` case.
    """

    assert is_storable_money(value) is True, (
        f"{value!r} is held exactly by Numeric({COLUMN_PRECISION}, {COLUMN_SCALE}), yet the "
        f"predicate calls it unstorable. Its callers SKIP what it rejects (seeding drops the "
        f"row and logs), so a false negative silently deletes configured money instead of "
        f"refusing it loudly."
    )


@pytest.mark.parametrize("value", _NOT_STORABLE, ids=[str(v) for v in _NOT_STORABLE])
def test_is_storable_money_refuses_what_the_column_would_change(value: object) -> None:
    """The predicate's negative half, including the two shapes that are not merely 'too small'.

    `Decimal("1E+30")` is here for the ordering comment inside the predicate: the magnitude test
    must run BEFORE `quantize`, because `quantize` raises `InvalidOperation` on operands this
    large rather than answering. If the two were swapped the predicate would raise instead of
    returning False, and its callers do not catch that.

    MUTATION THIS CATCHES: `MONEY_MAX_SCALE` 8 -> 18 reddens the `0.123456789` and `1E-9` cases;
    `MONEY_MAX_INTEGER_DIGITS` 12 -> 50 reddens `1000000000000`.
    """

    assert is_storable_money(value) is False, (
        f"{value!r} does not survive Numeric({COLUMN_PRECISION}, {COLUMN_SCALE}) unchanged - it "
        f"is rounded, overflows, or is not a number at all - yet the predicate calls it "
        f"storable. Its callers WRITE what it accepts."
    )


@pytest.mark.parametrize("value", [Decimal("1E+30"), Decimal("1E+3000")])
def test_is_storable_money_answers_rather_than_raises_on_huge_values(value: Decimal) -> None:
    """A predicate that raises is not a predicate; its callers only branch on it."""

    assert is_storable_money(value) is False


@pytest.mark.parametrize(
    "amount,expected",
    [(Decimal("0.123456789"), True), (Decimal("1E-9"), True), (Decimal("0.12345678"), True)],
)
def test_counter_check_a_widened_scale_makes_the_predicate_agree_to_rounding(
    monkeypatch: pytest.MonkeyPatch, amount: Decimal, expected: bool
) -> None:
    """`MONEY_MAX_SCALE` 8 -> 18 flips the predicate's verdict on exactly the rounded values.

    Without this, the table above would be indistinguishable from a list of literals: it would
    pass just as well if the predicate hard-coded these answers.
    """

    monkeypatch.setattr(validation, "MONEY_MAX_SCALE", 18)
    assert is_storable_money(amount) is expected


# --------------------------------------------------------------------------------------------
# The lexical bound is a length bound, and says so.
# --------------------------------------------------------------------------------------------


def test_the_lexical_bound_is_about_length_and_not_about_capacity() -> None:
    """`MONEY_MAX_LEXICAL_SCALE` refuses long spellings, including of storable values.

    It is deliberately not a capacity rule and this test says so out loud: `0.` followed by 19
    zeros IS zero, which the column holds, and the door still refuses it as pathological input.
    The number 18 is not arbitrary - it is the declared maximum of `Equivalent.precision`
    (`ge=0, le=18`), and `to_money_str` pads to `precision`, so 18 is the widest fraction this
    repository's renderer can emit for a value the ledger can hold. It is also exactly the scale
    the door accepted before `T1201`, so nothing that used to be admitted became a 400 when the
    capacity rule moved onto the value.

    This is the bound that keeps `tests/integration/test_payments_amount_validation.py`'s
    `"0." + "0" * 30` case answering "Invalid amount format".
    """

    assert MONEY_MAX_LEXICAL_SCALE == 18
    assert MONEY_MAX_LEXICAL_SCALE > MONEY_MAX_SCALE, (
        "a lexical bound at or below the capacity bound would silently become the capacity "
        "rule again, which is the defect this whole module exists to prevent"
    )

    at_bound = "0." + "0" * MONEY_MAX_LEXICAL_SCALE
    past_bound = "0." + "0" * (MONEY_MAX_LEXICAL_SCALE + 1)

    assert _verdict(at_bound) is not None
    assert _verdict(past_bound) is None
    assert is_storable_money(Decimal(past_bound)) is True, (
        "the refusal above must be understood for what it is: a length bound refusing a "
        "storable value. If this ever becomes False the two rules have merged again."
    )
