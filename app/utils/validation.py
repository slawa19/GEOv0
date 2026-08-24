import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.utils.exceptions import BadRequestException
from app.schemas.equivalent import normalize_equivalent_metadata


_EQUIVALENT_CODE_RE = re.compile(r"^[A-Z0-9_]{1,16}$")


def validate_equivalent_code(code: str) -> None:
    if not isinstance(code, str) or not code or not _EQUIVALENT_CODE_RE.fullmatch(code):
        raise BadRequestException("Invalid equivalent code")


def validate_equivalent_precision(precision: int) -> int:
    if (
        not isinstance(precision, int)
        or isinstance(precision, bool)
        or precision < 0
        or precision > 18
    ):
        raise BadRequestException("Invalid equivalent precision")
    return precision


def validate_equivalent_metadata(metadata: Any) -> dict | None:
    """Validate and normalize Equivalent.metadata.

    - metadata.type must be one of: fiat, time, commodity, custom
    - iso_code is optional; if provided it must be 3 uppercase letters and only for type=fiat

    Returns normalized dict (or None).
    """
    try:
        return normalize_equivalent_metadata(metadata)
    except Exception as exc:
        raise BadRequestException(f"Invalid equivalent metadata: {exc}")


def validate_idempotency_key(key: str) -> str:
    if not isinstance(key, str):
        raise BadRequestException("Invalid Idempotency-Key")

    normalized = key.strip()
    if not normalized:
        raise BadRequestException("Invalid Idempotency-Key")
    if len(normalized) > 128:
        raise BadRequestException("Idempotency-Key too long")

    return normalized


_TX_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def validate_tx_id(tx_id: str) -> str:
    """Validate client-generated tx_id (idempotency key).

    MVP constraints:
    - required non-empty string
    - max length 64 (matches DB column)
    - limited charset to avoid whitespace/control chars
    """

    if not isinstance(tx_id, str):
        raise BadRequestException("Invalid tx_id")

    normalized = tx_id.strip()
    if not normalized:
        raise BadRequestException("Invalid tx_id")
    if len(normalized) > 64:
        raise BadRequestException("tx_id too long")
    if not _TX_ID_RE.fullmatch(normalized):
        raise BadRequestException("Invalid tx_id")

    return normalized


# --- Amount validation ---
#
# Canonical JSON constraints for numeric values (protocol) require us to be strict about
# special values and representation:
# - forbid NaN/Infinity
# - forbid exponent notation (e/E)
#
# Additionally we enforce conservative scale/precision bounds to avoid pathological inputs
# (e.g. extremely long fractions).

# 012/T1201: this was 18 while every money column is Numeric(20, 8), which is the whole of
# `F-012-1` - a participant signed one number and the ledger kept another, larger one. It is now
# the storage scale, so the generic default is the safe one and a money path that reaches for
# `parse_amount_decimal` without arguments cannot re-open the hole. Widening it re-opens F-012-1;
# `test_p012_rt1_...` demonstrates exactly that flip rather than describing it.
DEFAULT_MAX_AMOUNT_SCALE = 8
DEFAULT_MAX_AMOUNT_PRECISION = 50  # total digits in the decimal string (excluding sign and '.')

# --- Money: the storage-capacity door (012 / F-012-1, T1201) ---
#
# `Debt.amount` and `TrustLine.limit` are `Numeric(20, 8)` (`app/db/models/debt.py`,
# `app/db/models/trustline.py`).  PostgreSQL does NOT truncate what does not fit: it rounds,
# in both directions, and it rounds silently.  Measured on PostgreSQL 16.9:
#
#   0.123456789          -> 0.12345679   (the ledger keeps MORE than was signed)
#   0.000000001          -> 0            (the value disappears; the positivity CHECK then
#                                         fires and escapes as HTTP 500 E010)
#   1000000000000        -> NumericValueOutOfRangeError, i.e. a 500 as well
#
# Both bounds below are therefore load-bearing, and they are two different escapes of the
# same class.  `MONEY_MAX_SCALE` is the fraction the column can hold; `MONEY_MAX_INTEGER_DIGITS`
# is what is left of `precision 20` once the scale is spent, i.e. `abs(value) < 10**12`.
# Narrowing the scale alone still admits `12345678901234567890`, which PostgreSQL refuses.
#
# These are facts about the COLUMN, not about `Equivalent.precision`.  Rejecting on precision
# is a separate, deferred product decision (variant B): precision is declared `ge=0, le=18`,
# so it does not even close this finding, and an admin editing it would retroactively
# invalidate stored rows.
MONEY_MAX_SCALE = 8
MONEY_MAX_INTEGER_DIGITS = 12

_AMOUNT_STR_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_FORBIDDEN_AMOUNT_LITERALS = {
    "nan",
    "inf",
    "+inf",
    "-inf",
    "infinity",
    "+infinity",
    "-infinity",
}


def parse_amount_decimal(
    amount: Any,
    *,
    max_scale: int | None = DEFAULT_MAX_AMOUNT_SCALE,
    max_precision: int | None = DEFAULT_MAX_AMOUNT_PRECISION,
    max_integer_digits: int | None = None,
    require_positive: bool = False,
    field: str | None = None,
) -> Decimal:
    """Parse and validate an API amount as a strict decimal string.

    Accepts only plain decimal strings like: 0, 0.1, 10.00, 0001.23

    Rejects:
    - NaN / Infinity / -Infinity
    - exponent notation (e/E)
    - leading/trailing whitespace
    - empty / non-numeric strings
    - excessive scale/precision (conservative limits)

    If require_positive=True, enforces amount > 0.

    Raises BadRequestException("Invalid amount format") or
    BadRequestException("Amount must be positive").

    NOTE ON THE DEFAULTS.  These are generic parser bounds, not storage bounds: the default
    `max_scale` of 18 is wider than the `Numeric(20, 8)` money columns.  Anything that will be
    written to `Debt.amount` or `TrustLine.limit` must go through `parse_money_amount` instead,
    which supplies the capacity bounds.  See the `MONEY_MAX_*` note above.

    `field` is diagnostic only: when given, the raised `BadRequestException` carries
    `details` naming the offending field and the bound it broke.  The messages themselves are
    deliberately unchanged - they are asserted verbatim by
    `tests/integration/test_payments_amount_validation.py` and are part of the public 400/E009
    shape.
    """

    def _reject(message: str, **extra: Any) -> BadRequestException:
        if field is None:
            return BadRequestException(message)
        details: dict[str, Any] = {"field": field}
        details.update(extra)
        return BadRequestException(message, details=details)

    if amount is None:
        raise _reject("Invalid amount format")

    amount_str = amount if isinstance(amount, str) else str(amount)

    if not amount_str:
        raise _reject("Invalid amount format")

    # No implicit normalization: reject any surrounding whitespace.
    if amount_str != amount_str.strip():
        raise _reject("Invalid amount format")

    lowered = amount_str.lower()

    # Explicitly forbid special float-like literals even if some callers pass them as strings.
    if lowered in _FORBIDDEN_AMOUNT_LITERALS:
        raise _reject("Invalid amount format")

    # Exponent notation is forbidden (canonical JSON recommendation and DoS hardening).
    if "e" in lowered:
        raise _reject("Invalid amount format")

    # Strict decimal string: optional '-' then digits, optional fraction with at least 1 digit.
    # Note: '+' is intentionally NOT supported.
    if _AMOUNT_STR_RE.fullmatch(amount_str) is None:
        raise _reject("Invalid amount format")

    unsigned = amount_str[1:] if amount_str.startswith("-") else amount_str
    if "." in unsigned:
        int_part, frac_part = unsigned.split(".", 1)
        scale = len(frac_part)
    else:
        int_part, frac_part = unsigned, ""
        scale = 0

    if max_scale is not None and scale > max_scale:
        raise _reject("Invalid amount format", max_scale=max_scale)

    if max_precision is not None and (len(int_part) + len(frac_part)) > max_precision:
        raise _reject("Invalid amount format", max_precision=max_precision)

    # Magnitude, i.e. how many integer digits the target can hold.  Counted on the
    # SIGNIFICANT digits so that the long-standing tolerance for leading zeros ("0001.23")
    # is preserved: it is the VALUE that has to fit, not the spelling.
    if max_integer_digits is not None:
        significant_int_digits = len(int_part.lstrip("0"))
        if significant_int_digits > max_integer_digits:
            raise _reject(
                "Invalid amount format", max_integer_digits=max_integer_digits
            )

    try:
        as_decimal = Decimal(amount_str)
    except (InvalidOperation, ValueError):
        raise _reject("Invalid amount format")

    # Decimal() could still theoretically produce non-finite values for special inputs,
    # so keep an explicit guard.
    if not as_decimal.is_finite():
        raise _reject("Invalid amount format")

    if require_positive and as_decimal <= 0:
        raise _reject("Amount must be positive")

    return as_decimal


def parse_money_amount(
    amount: Any,
    *,
    field: str = "amount",
    require_positive: bool = False,
) -> Decimal:
    """The money door: parse an amount the ledger columns can hold exactly, or refuse it.

    Everything `parse_amount_decimal` refuses is refused here too (float literals, exponent
    notation, whitespace, NaN/Infinity), plus the two storage-capacity bounds:

    * at most `MONEY_MAX_SCALE` (8) fraction digits, and
    * at most `MONEY_MAX_INTEGER_DIGITS` (12) significant integer digits, i.e.
      `abs(value) < 10**12`.

    Refusal is `BadRequestException` -> HTTP 400 / `E009`, the existing classification for a
    scale overflow.  No new error code: callers that own a different envelope (the simulator
    Interact API) translate it themselves.

    WHY BOTH BOUNDS.  `Numeric(20, 8)` is 20 significant digits of which 8 are the fraction.
    Values that overflow either half are lost differently and both losses are silent to the
    caller: too many fraction digits are ROUNDED by PostgreSQL (in both directions, so the
    ledger can end up holding more than was signed), while too large a magnitude raises
    `numeric field overflow` deep inside the commit and escapes as HTTP 500.  Narrowing only
    the scale leaves the second escape open.

    WHY NOT `Equivalent.precision`.  Deferred by product decision: precision is a
    representation parameter declared `ge=0, le=18`, so it neither bounds the column nor closes
    this finding, and an admin lowering it would retroactively invalidate stored rows.
    """

    return parse_amount_decimal(
        amount,
        max_scale=MONEY_MAX_SCALE,
        max_precision=DEFAULT_MAX_AMOUNT_PRECISION,
        max_integer_digits=MONEY_MAX_INTEGER_DIGITS,
        require_positive=require_positive,
        field=field,
    )


def is_storable_money(value: Any) -> bool:
    """True when `value` fits `Numeric(20, 8)` exactly - no rounding, no overflow.

    The predicate form of `parse_money_amount`, for the writers that do not answer an HTTP
    request and therefore cannot raise a 400: scenario seeding and inject execution build
    `Decimal`s out of arbitrary config and drop rows they cannot use.  They need the same
    capacity rule, applied with their own (skip-and-log) blast radius.
    """

    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return False
    if not value.is_finite():
        return False

    # Magnitude first: `quantize` below raises on operands too large for the arithmetic
    # context, so asking about the fraction before the magnitude would fail for exactly the
    # values this predicate exists to reject.
    if abs(value) >= Decimal(10) ** MONEY_MAX_INTEGER_DIGITS:
        return False

    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= -MONEY_MAX_SCALE:
        return True
    # More fraction digits than the column declares - storable only if they are all zero,
    # because PostgreSQL would otherwise round rather than truncate.
    try:
        return value == value.quantize(Decimal("1E-%d" % MONEY_MAX_SCALE))
    except InvalidOperation:
        return False


_ALLOWED_TRUSTLINE_POLICY_KEYS = {
    "auto_clearing",
    "can_be_intermediate",
    "max_hop_usage",
    "daily_limit",
    "blocked_participants",
}


def validate_trustline_policy(policy: dict[str, Any]) -> None:
    if not isinstance(policy, dict):
        raise BadRequestException("Invalid trustline policy")

    unknown = set(policy.keys()) - _ALLOWED_TRUSTLINE_POLICY_KEYS
    if unknown:
        raise BadRequestException(f"Unknown trustline policy keys: {sorted(unknown)}")

    if "auto_clearing" in policy and policy["auto_clearing"] is not None and not isinstance(policy["auto_clearing"], bool):
        raise BadRequestException("trustline.policy.auto_clearing must be boolean")

    if "can_be_intermediate" in policy and policy["can_be_intermediate"] is not None and not isinstance(policy["can_be_intermediate"], bool):
        raise BadRequestException("trustline.policy.can_be_intermediate must be boolean")

    for key in ("max_hop_usage", "daily_limit"):
        if key not in policy or policy[key] is None:
            continue
        value = policy[key]
        if key == "daily_limit":
            # `daily_limit` IS a money quantity by the protocol - informational-only in the
            # MVP, but a bound that will one day be compared against amounts.  It therefore
            # uses the money grammar rather than "whatever `Decimal()` will swallow": no
            # float, no exponent, and the same storage capacity, so the day it is enforced
            # the comparison does not need a representation change.  `max_hop_usage` is a hop
            # count, not money, and keeps the looser rule.
            try:
                as_decimal = parse_money_amount(value, field=f"policy.{key}")
            except BadRequestException:
                raise BadRequestException(f"trustline.policy.{key} must be a number")
        else:
            try:
                as_decimal = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise BadRequestException(f"trustline.policy.{key} must be a number")
        if as_decimal < 0:
            raise BadRequestException(f"trustline.policy.{key} must be >= 0")

    if "blocked_participants" in policy and policy["blocked_participants"] is not None:
        value = policy["blocked_participants"]
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise BadRequestException("trustline.policy.blocked_participants must be a list of strings")
