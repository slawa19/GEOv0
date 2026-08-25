from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


# 012/T1202: moved to `app/utils/money.py` so the money core can reach it without importing
# `app.core.simulator`, whose package `__init__` pulls in the whole runtime. Re-exported here
# because this is the name T1207 published.
from app.utils.money import to_money_str  # noqa: F401  (re-export)


def net_decimal_to_atoms(net: Decimal, *, precision: int) -> int:
    """Convert a Decimal net balance into integer 'atoms' using equivalent precision.

    Uses ROUND_HALF_UP to match existing simulator snapshot + SSE patch semantics, with one
    exception recorded below: a net that is not zero never becomes zero atoms.

    WHY THE EXCEPTION (012 / `T1210` finding 6).  One participant's net leaves this package in
    TWO encodings, side by side in the same payload (`snapshot_builder.py:274-278`,
    `viz_patch_helper.py:288-296`):

      * `net_balance` - the amount, rendered by `to_money_str`, whose rule is that
        `Equivalent.precision` sets the MINIMUM number of fraction digits and never the
        maximum, because `precision` is a DISPLAY parameter and the ledger column
        (`Numeric(20, 8)`) faithfully stores values finer than it (`RT-012-2`);
      * `net_balance_atoms` + `net_sign` - an integer count of `10**-precision` units, from
        which `viz_color_key` (debt vs. not, and which debt bin) and `viz_size` (percentile of
        magnitude) are then derived.

    They answer different questions.  `net_balance` answers *how much*; the atoms answer *how
    much, at the coarsest resolution the equivalent declares*, so that a drawing layer can
    compare magnitudes with integers.  A difference of RESOLUTION between them is legitimate:
    at `precision: 1` a net of `0.14` is `"0.14"` in the card and one atom (`0.1`) in the
    ranking, and neither is lying.

    What is NOT legitimate is a difference of FACT.  Before this change, a net of `0.04` at
    `precision: 1` produced `net_balance: "0.04"` and `atoms: 0`, `net_sign: 0` - so
    `NodeCardOverlay` (which prefers `net_balance`) read `0.04` on a node the graph painted
    with the neutral `person`/`business` colour and sized as a zero.  That is exactly the
    `RT-012-2` defect - a real, stored, sub-quantum obligation erased because a display
    parameter was treated as the ledger's quantum - surviving in the atoms channel after
    `T1207` fixed it in the string channel.  ROUND_HALF_UP maps the whole open interval
    `(-q/2, q/2)` onto zero, and that interval contains money.

    So the rounding keeps NEAREST everywhere it is a summary, and becomes AWAY-FROM-ZERO in
    the one interval where nearest destroys a fact rather than a digit: strictly inside half a
    quantum of zero.  Stated as an invariant, and pinned in
    `tests/integration/test_p012_t1210_net_balance_agrees_with_its_atoms.py`:

        sign(atoms) == sign(net)      and      atoms == 0  if and only if  net == 0
        abs(atoms * 10**-precision - net) < 10**-precision

    THIS MOVES NOTHING THAT WAS ALREADY RIGHT, and that is provable rather than hoped: the
    exception can only fire when `0 < abs(net) < q/2`, whereas any non-zero value that IS
    exactly representable at `precision` has `abs(net) >= q`.  Every such value keeps the
    atoms it had, byte for byte, at every precision 0..18.

    TWO ALTERNATIVES REJECTED, both measured rather than argued:

      1. *Make the atoms carry the full value.*  They cannot: the consumer reconstructs money
         as `atoms * 10**-precision` (`NodeCardOverlay.netText`), so carrying `0.04` at
         `precision: 1` would require putting a second scale on the wire - a schema change
         across `app/schemas/`, the OpenAPI canon and both front ends - and would destroy the
         one property the atoms exist for, being small integers a drawing layer can bucket.
      2. *Quantise `net_balance` back to `precision`.*  This re-opens `RT-012-2` (a stored
         `0.05 HOUR` renders `"0.0"` again), and MEASUREMENT SHOWS IT WOULD NOT EVEN BUY
         AGREEMENT: the string quantises ROUND_DOWN and the atoms ROUND_HALF_UP, so `0.05` at
         `precision: 1` would be `"0.0"` against `1` atom, and `0.6` at `precision: 0` `"0"`
         against `1` atom.  Those two disagreements are pre-`T1207` behaviour and are the
         reason the brief's claim "before `T1207` the two always agreed" does not hold.
    """

    p = int(precision)
    if p < 0:
        p = 0
    scale10 = Decimal(10) ** p
    atoms = int((net * scale10).to_integral_value(rounding=ROUND_HALF_UP))
    if atoms == 0 and net != 0:
        # Sub-quantum, and therefore invisible to `precision` - but not absent.  Report the
        # smallest magnitude the encoding has, in the direction the ledger actually says.
        return 1 if net > 0 else -1
    return atoms


def atoms_to_net_sign(atoms: int) -> int:
    """The sign of the net, read off the atoms.

    Safe to read off the atoms only because `net_decimal_to_atoms` guarantees the two share a
    sign and a zero (see its docstring).  Before that guarantee this function turned a real
    sub-quantum debt into `net_sign: 0`.
    """

    if atoms < 0:
        return -1
    if atoms > 0:
        return 1
    return 0
