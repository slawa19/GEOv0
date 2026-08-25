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
from decimal import Decimal
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
