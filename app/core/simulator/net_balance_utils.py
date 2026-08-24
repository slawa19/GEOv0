from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


# 012/T1202: moved to `app/utils/money.py` so the money core can reach it without importing
# `app.core.simulator`, whose package `__init__` pulls in the whole runtime. Re-exported here
# because this is the name T1207 published.
from app.utils.money import to_money_str  # noqa: F401  (re-export)


def net_decimal_to_atoms(net: Decimal, *, precision: int) -> int:
    """Convert a Decimal net balance into integer 'atoms' using equivalent precision.

    Uses ROUND_HALF_UP to match existing simulator snapshot + SSE patch semantics.
    """

    p = int(precision)
    if p < 0:
        p = 0
    scale10 = Decimal(10) ** p
    return int((net * scale10).to_integral_value(rounding=ROUND_HALF_UP))


def atoms_to_net_sign(atoms: int) -> int:
    if atoms < 0:
        return -1
    if atoms > 0:
        return 1
    return 0
