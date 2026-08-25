"""012 / ``T1211`` - the one money-rendering divergence, pinned as unreachable rather than argued.

``api/money-rendering-conformance.json`` deliberately leaves one case out of the shared table:
``to_money_str(Decimal("-0.00"), 2)`` renders ``"-0.00"`` while both UI implementations render
``"0.00"`` for the same input, on the ground that a zero amount is not a debt and the sign would
read as one.  The table is a contract all three must meet, and here they do not, so it is
recorded as a known difference instead of levelled.

Recording it is only honest if "unreachable" is measured rather than asserted.  The repeat
external review made exactly that objection: SQLite normalisation had been shown, PostgreSQL had
not, and the reviewer had no test database to check it.  PostgreSQL is the ledger's real storage
and the one whose rounding behaviour 012 exists because of, so leaving it on an argument would
put the whole record on the weaker half of the evidence.

This is that measurement, kept as a test so it stays true: every route a negative zero could take
into a ``Numeric(20, 8)`` column comes back unsigned.  If a future PostgreSQL, driver or column
type ever hands back a signed zero, this fails and the divergence stops being unreachable - at
which point the record in the conformance table has to be revisited, not the other way round.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


#: Every way a negative zero can be put into the column: written literally, written as a bare
#: signed zero, and produced by arithmetic that cancels.  The last is the one that matters -
#: it is what a repaid debt actually looks like.
NEGATIVE_ZERO_ROUTES = [
    ("a literal negative zero at column scale", "-0.00000000"),
    ("a literal negative zero at display scale", "-0.00"),
    ("a bare signed zero", "-0"),
    ("arithmetic that cancels at the storage quantum", "(-0.00000001 + 0.00000001)"),
    ("a debt repaid in full", "(5.00 - 5.00)"),
]


@pytest.mark.parametrize("label,expression", NEGATIVE_ZERO_ROUTES, ids=[r[0] for r in NEGATIVE_ZERO_ROUTES])
async def test_a_negative_zero_cannot_come_back_from_a_numeric_column(db_session, label: str, expression: str) -> None:
    await db_session.execute(text("CREATE TEMP TABLE IF NOT EXISTS p012_negative_zero (v numeric(20,8))"))
    await db_session.execute(text("DELETE FROM p012_negative_zero"))
    await db_session.execute(text(f"INSERT INTO p012_negative_zero VALUES ({expression})"))

    stored = (await db_session.execute(text("SELECT v FROM p012_negative_zero"))).scalar_one()

    assert isinstance(stored, Decimal), f"{label} came back as {type(stored).__name__}, not Decimal"
    assert stored == 0, f"{label} was expected to be zero, got {stored!r}"
    assert stored.as_tuple().sign == 0, (
        f"{label} came back as a SIGNED zero ({stored!r}). The conformance table records the "
        f"backend/UI disagreement over `-0.00` as unreachable through the ledger, and that record "
        f"just became false: `to_money_str` would print `-0.00` where both UIs print `0.00`, on a "
        f"value the ledger actually holds. Revisit "
        f"`api/money-rendering-conformance.json` before touching this test."
    )


async def test_the_renderer_disagreement_this_pins_is_still_real(db_session) -> None:
    """The pin is worth nothing if the divergence it guards has quietly gone away.

    If `to_money_str` ever stops printing `-0.00`, the difference no longer exists, the record in
    the conformance table is stale, and this whole module should go with it. Asserted rather than
    assumed, so the record dies with the divergence instead of outliving it.
    """

    from app.utils.money import to_money_str

    assert to_money_str(Decimal("-0.00"), 2) == "-0.00", (
        "The backend no longer renders a negative zero with its sign. The divergence recorded in "
        "`api/money-rendering-conformance.json` is gone - remove the record and this module."
    )
