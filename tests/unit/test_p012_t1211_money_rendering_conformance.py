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

    assert len(_CASES) > 10, f"The shared table at {TABLE_PATH} carries too few cases to prove anything."
    assert "minimum" in _TABLE["rule"]


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


WRONG_IMPLEMENTATIONS = [
    ("precision as a maximum, half-up (the one T1211 removed)", _as_maximum_half_up),
    ("precision as a maximum, truncating", _as_maximum_truncating),
    ("pads up but never strips padding zeros", _pads_but_never_strips),
    ("ignores precision, always renders at storage scale", _always_storage_scale),
    ("ignores precision, echoes the input", _echoes_the_input),
    ("strips every trailing zero, including declared ones", _strips_every_trailing_zero),
    ("right digits, lost sign", _loses_the_sign),
    ("routes the amount through a float", _goes_through_a_float),
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
