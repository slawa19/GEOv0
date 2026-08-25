"""012 / ``T1211`` - the backend's third of the shared money-rendering contract.

One rule - ``Equivalent.precision`` is the MINIMUM number of fraction digits a money string
carries, never the maximum - is implemented three times, in three languages, in three
projects that share no module: :func:`app.utils.money.to_money_str`,
``simulator-ui/v2/src/utils/money.ts``, and ``admin-ui/src/utils/decimal.ts``.

The duplication was the MECHANISM of a P1, not a cosmetic concern.  Two of the three copies
were right while ``admin-ui`` quantized to exactly ``precision`` with ROUND_HALF_UP, so
``0.05`` of the shipped ``HOUR`` (``precision: 1``) - a value the door accepts and
``Numeric(20, 8)`` stores exactly - reached operators as ``0.1``.  Each project's own suite
was green.  Nothing in the tree compared the copies to each other, so nothing could notice.

``api/money-rendering-conformance.json`` is that comparison.  This module is one of its three
readers; the other two are ``simulator-ui/v2/src/utils/money.conformance.test.ts`` and
``admin-ui/src/utils/decimal.conformance.test.ts``.  A case added to the table is answered by
all three at once, and a copy that drifts fails in its own project.
"""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from app.utils.money import to_money_str

TABLE_PATH = Path(__file__).resolve().parents[2] / "api" / "money-rendering-conformance.json"

_TABLE = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
_CASES = _TABLE["cases"]


def test_the_shared_table_is_actually_read() -> None:
    """Without this, an unreadable or emptied table would make every case below vacuous."""

    assert _CASES, f"The shared table at {TABLE_PATH} is empty or unreadable."
    assert "minimum" in _TABLE["rule"]


#: What the table must COVER, not how many rows it has.
#:
#: T1211's repeat review broke the first version of this guard by pointing out that
#: `len(cases) > 10` lets the table shrink from 21 rows to 11 without a word - and that the 21
#: rows sampled only precisions 0, 1, 2 and 8 out of the contract's 0..18, with negatives only at
#: 1 and 2.  Three plausible wrong implementations walked through untouched: capping display
#: precision at the storage scale 8, supporting only the sampled precisions, and losing the sign
#: only at precision 0.
#:
#: A count cannot catch that; a count is satisfied by twenty copies of one case.  So the table is
#: held to CLASSES it must contain.  Deleting rows now costs a class, and a class failure names
#: what went missing.
def _scale_of(value: str) -> int:
    _, _, fraction = value.partition(".")
    return len(fraction)


REQUIRED_COVERAGE = {
    "precision 0 is declared, not absent":
        lambda cs: any(c["precision"] == 0 for c in cs),
    "a precision between the historically sampled ones":
        lambda cs: any(3 <= c["precision"] <= 7 for c in cs),
    "a precision past the storage scale 8":
        lambda cs: any(c["precision"] > 8 for c in cs),
    "the contract's widest precision, 18":
        lambda cs: any(c["precision"] == 18 for c in cs),
    "a negative at precision 0":
        lambda cs: any(c["value"].startswith("-") and c["precision"] == 0 for c in cs),
    "a negative past the storage scale":
        lambda cs: any(c["value"].startswith("-") and c["precision"] > 8 for c in cs),
    "a negative that is stripped rather than padded":
        lambda cs: any(
            c["value"].startswith("-") and _scale_of(c["value"]) > c["precision"] for c in cs
        ),
    "a value finer than its precision":
        lambda cs: any(_scale_of(c["value"]) > c["precision"] for c in cs),
    "a value exactly at its precision":
        lambda cs: any(_scale_of(c["value"]) == c["precision"] for c in cs),
    "a value coarser than its precision":
        lambda cs: any(_scale_of(c["value"]) < c["precision"] for c in cs),
    "trailing zeros stripped down to the precision":
        lambda cs: any(
            _scale_of(c["value"]) > c["precision"] and c["value"].rstrip("0") != c["value"]
            for c in cs
        ),
}


@pytest.mark.parametrize("requirement", sorted(REQUIRED_COVERAGE), ids=lambda r: r)
def test_the_table_covers_the_class_it_claims_to(requirement: str) -> None:
    assert REQUIRED_COVERAGE[requirement](_CASES), (
        f"The shared table no longer contains {requirement}. Every implementation reading it "
        f"would go green without ever being asked about this class - which is how a table of 21 "
        f"rows sampling four precisions let three wrong implementations through."
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda c: f"{c['value']}@{c['precision']}")
def test_to_money_str_conforms_to_the_shared_table(case: dict) -> None:
    rendered = to_money_str(Decimal(case["value"]), case["precision"])
    assert rendered == case["expected"], (
        f"Shared contract case: {case['why']}. If the backend is right and the table is wrong, "
        f"change the table - and then the two UI implementations answer for it too."
    )


#: Eight ways to get the rule wrong, each one something a person could plausibly write.
#:
#: The table below is evidence only if it can TELL THESE APART from the rule.  A sample that
#: every implementation satisfies proves nothing, and mutating production code cannot reveal
#: that - the blind element is the measurer.  This wave found five of its own proofs standing
#: on such samples, so the sample is asserted here rather than assumed.
#:
#: `_as_maximum_half_up` is the implementation T1211 actually removed from `admin-ui`; the
#: other seven are near misses that a reimplementation could land on.
def _as_maximum_half_up(value: str, precision: int) -> str:
    return format(Decimal(value).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP), "f")


def _as_maximum_truncating(value: str, precision: int) -> str:
    return format(Decimal(value).quantize(Decimal(1).scaleb(-precision), rounding=ROUND_DOWN), "f")


def _pads_but_never_strips(value: str, precision: int) -> str:
    parsed = Decimal(value)
    if -parsed.as_tuple().exponent >= precision:
        return format(parsed, "f")
    return format(parsed.quantize(Decimal(1).scaleb(-precision)), "f")


def _always_storage_scale(value: str, precision: int) -> str:
    return format(Decimal(value).quantize(Decimal("1E-8")), "f")


def _echoes_the_input(value: str, precision: int) -> str:
    return value


def _strips_every_trailing_zero(value: str, precision: int) -> str:
    text = format(Decimal(value), "f")
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".") or "0"


def _loses_the_sign(value: str, precision: int) -> str:
    magnitude = abs(Decimal(value))
    scale = -magnitude.as_tuple().exponent
    if scale < precision:
        return format(magnitude.quantize(Decimal(1).scaleb(-precision)), "f")
    text = format(magnitude, "f")
    return text.rstrip("0").rstrip(".") if scale > precision else text


def _goes_through_a_float(value: str, precision: int) -> str:
    return f"{float(value):.{precision}f}"


# The three below were found by T1211's repeat review passing the FIRST version of this table.
# They are the reason the coverage requirements above exist, and they are kept here so the table
# is measured against them on every run rather than against a story about them.
def _caps_display_precision_at_the_storage_scale(value: str, precision: int) -> str:
    """Confuses the display precision with `Numeric(20, 8)`'s scale. Plausible: 8 is everywhere."""
    return to_money_str(Decimal(value), min(precision, 8))


def _supports_only_the_historically_sampled_precisions(value: str, precision: int) -> str:
    """Handles 0, 1, 2, 8 and silently defaults the rest - what a table of four precisions invites."""
    return to_money_str(Decimal(value), precision if precision in (0, 1, 2, 8) else 2)


def _loses_the_sign_only_at_precision_zero(value: str, precision: int) -> str:
    """A sign bug narrow enough to survive a sample whose negatives all sat at precision 1 and 2."""
    rendered = to_money_str(Decimal(value), precision)
    return rendered.lstrip("-") if precision == 0 else rendered


def _reads_precision_zero_as_absent(value: str, precision: int) -> str:
    """Not hypothetical: `int(getattr(eq, "precision", 2) or 2)` is live in five simulator
    producers, and `or 2` turns a declared 0 into 2. Recorded by T1210 and deliberately unfixed
    there; the table must at least be able to SEE it."""
    return to_money_str(Decimal(value), precision or 2)


def _pads_one_digit_short(value: str, precision: int) -> str:
    """An off-by-one in the padding loop - the cheapest possible slip in this rule."""
    return to_money_str(Decimal(value), max(0, precision - 1))


WRONG_IMPLEMENTATIONS = [
    ("precision as a maximum, half-up (the one T1211 removed)", _as_maximum_half_up),
    ("precision as a maximum, truncating", _as_maximum_truncating),
    ("pads up but never strips padding zeros", _pads_but_never_strips),
    ("ignores precision, always renders at storage scale", _always_storage_scale),
    ("ignores precision, echoes the input", _echoes_the_input),
    ("strips every trailing zero, including declared ones", _strips_every_trailing_zero),
    ("right digits, lost sign", _loses_the_sign),
    ("routes the amount through a float", _goes_through_a_float),
    ("caps display precision at the storage scale 8", _caps_display_precision_at_the_storage_scale),
    ("supports only the historically sampled precisions", _supports_only_the_historically_sampled_precisions),
    ("loses the sign only at precision 0", _loses_the_sign_only_at_precision_zero),
    ("reads a declared precision 0 as absent and defaults to 2", _reads_precision_zero_as_absent),
    ("pads one digit short", _pads_one_digit_short),
]


@pytest.mark.parametrize("label,wrong", WRONG_IMPLEMENTATIONS, ids=[w[0] for w in WRONG_IMPLEMENTATIONS])
def test_the_table_can_tell_a_wrong_implementation_apart(label: str, wrong) -> None:
    """The sample is asserted, not assumed: every wrong variant must fail at least one case."""

    disagreements = []
    for case in _CASES:
        try:
            rendered = wrong(case["value"], case["precision"])
        except Exception as exc:  # noqa: BLE001 - a variant that raises is caught too
            rendered = f"<raised {type(exc).__name__}>"
        if rendered != case["expected"]:
            disagreements.append(f"{case['value']}@{case['precision']}")

    assert disagreements, (
        f"The table cannot distinguish the rule from '{label}'. Every case is satisfied by BOTH, "
        f"so all three readers would pass against this wrong implementation and the table would "
        f"be evidence of nothing. Add a case that separates them."
    )


def test_negative_zero_is_the_one_place_the_three_copies_disagree() -> None:
    """Recorded rather than levelled, because levelling it would need a reachability argument.

    ``to_money_str(Decimal("-0.00"), 2)`` renders ``"-0.00"``; both UI implementations render
    ``"0.00"`` for the same input, on the deliberate ground that a zero amount is not a debt
    and the sign would read as one.  This is not in the shared table because the table is a
    contract the three MUST meet, and here they do not.

    It is left alone because no measured path produces a negative zero in the backend:
    ``Numeric`` reads back an unsigned zero, and ``Decimal("5.00") - Decimal("5.00")`` is
    ``Decimal("0.00")``, not ``Decimal("-0.00")``.  The divergence is real and unreachable,
    so this test pins it as a known difference: if a future change makes the backend agree
    with the UIs, this test fails and the divergence gets removed from the record with it.
    """

    assert to_money_str(Decimal("-0.00"), 2) == "-0.00"
    assert to_money_str(Decimal("5.00") - Decimal("5.00"), 2) == "0.00"
