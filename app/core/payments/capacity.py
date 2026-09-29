"""How much one participant can pay another directly: ONE rule for the router, the core and `/balance`.

Protocol §6.3.1, owner decision 2026-09-29 (programme 024, П1, variant (b)): a payment payer -> payee may
go up to `limit(payee -> payer) - debt[payer -> payee] + debt[payee -> payer]`. The payee's debt to the
payer is offset first and needs no line from the payee; new debt of the payer needs the payee's ACTIVE
line, and a missing, frozen or closed line counts as limit 0. The result may be zero or negative: the
router drops such an edge and the core refuses the segment; `/balance` sums it over the peers.
"""

from __future__ import annotations

from decimal import Decimal


def pair_capacity(*, line_limit: Decimal | None, payer_owes: Decimal, payee_owes: Decimal) -> Decimal:
    """`line_limit` is the payee's active line to the payer (None when there is none)."""

    return (line_limit if line_limit is not None else Decimal("0")) - payer_owes + payee_owes
