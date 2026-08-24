from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP


def to_money_str(value: Decimal, precision: int) -> str:
    """Render a money value as a plain decimal string that never loses it.

    THE RULE: at least `precision` fraction digits, and never fewer digits than the value
    actually needs.  Exponent notation is impossible by construction.

    WHY NOT A PLAIN `quantize(..., ROUND_DOWN)` (012 / `RT-012-2`).  That is what every money
    producer in `app/core/simulator/` did, and `Equivalent.precision` is a DISPLAY parameter,
    not the ledger's quantum: the door accepts, and `Numeric(20, 8)` faithfully stores, values
    finer than `precision`.  The shipped `HOUR` has `precision: 1`, so a real, committed,
    stored debt of `0.05 HOUR` was floored to `"0.0"` -- the obligation exists and the graph
    says it does not, in the one direction nobody audits.  Rejecting `0.05` at the door instead
    was considered and deliberately rejected: precision is editable by an admin, so it would
    retroactively invalidate rows that are already in the ledger.

    So `precision` keeps its job of setting the MINIMUM number of digits shown -- a
    precision-2 equivalent still renders `0.05` as `"0.05"` and zero as `"0.00"`, byte for
    byte as before -- and loses only its power to erase what does not divide by it.

    It also fixes the producers that used bare `str(Decimal)` and put literal `1E-8` (and, from
    a scenario-supplied `"1e3"`, literal `1E+3`) on the wire.

    WHERE THIS LIVES AND WHY (012 / `T1207`).  `T1201` put this function in
    `edge_patch_builder.py`, which imports `viz_patch_helper`; `viz_patch_helper` is one of the
    producers that has to call it, so leaving it there would have forced an import cycle.  This
    module is the leaf the simulator's other money-representation helper
    (`net_decimal_to_atoms`) already lives in and imports nothing but `decimal`, so every
    producer can reach it.  `edge_patch_builder.to_money_str` is kept as a re-export because
    that is the name `T1201` published.
    """

    try:
        precision = int(precision)
    except (TypeError, ValueError):
        precision = 2
    if precision < 0:
        precision = 0

    if not isinstance(value, Decimal):
        # `str()` first: going through `float` here would reintroduce, in the renderer, the
        # binary rounding this whole change exists to remove.
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return "0"
    if not value.is_finite():
        return "0"

    quantum = Decimal(1).scaleb(-precision)
    try:
        quantized = value.quantize(quantum, rounding=ROUND_DOWN)
    except InvalidOperation:
        quantized = None

    if quantized is not None and quantized == value:
        return format(quantized, "f")

    # The value carries more than `precision` can express.  Show all of it rather than any
    # of it: `format(..., "f")` is plain-decimal by definition, and the trailing zeros the
    # column pads to scale 8 are noise, not information.
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


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
