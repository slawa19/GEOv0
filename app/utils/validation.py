import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.utils.exceptions import BadRequestException
from app.schemas.equivalent import normalize_equivalent_metadata


_EQUIVALENT_CODE_RE = re.compile(r"^[A-Z0-9_]{1,16}$")


def validate_equivalent_code(code: str) -> None:
    if not isinstance(code, str) or not code or not _EQUIVALENT_CODE_RE.fullmatch(code):
        raise BadRequestException("Invalid equivalent code")


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

DEFAULT_MAX_AMOUNT_SCALE = 18
DEFAULT_MAX_AMOUNT_PRECISION = 50  # total digits in the decimal string (excluding sign and '.')

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
    require_positive: bool = False,
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
    """

    if amount is None:
        raise BadRequestException("Invalid amount format")

    amount_str = amount if isinstance(amount, str) else str(amount)

    if not amount_str:
        raise BadRequestException("Invalid amount format")

    # No implicit normalization: reject any surrounding whitespace.
    if amount_str != amount_str.strip():
        raise BadRequestException("Invalid amount format")

    lowered = amount_str.lower()

    # Explicitly forbid special float-like literals even if some callers pass them as strings.
    if lowered in _FORBIDDEN_AMOUNT_LITERALS:
        raise BadRequestException("Invalid amount format")

    # Exponent notation is forbidden (canonical JSON recommendation and DoS hardening).
    if "e" in lowered:
        raise BadRequestException("Invalid amount format")

    # Strict decimal string: optional '-' then digits, optional fraction with at least 1 digit.
    # Note: '+' is intentionally NOT supported.
    if _AMOUNT_STR_RE.fullmatch(amount_str) is None:
        raise BadRequestException("Invalid amount format")

    unsigned = amount_str[1:] if amount_str.startswith("-") else amount_str
    if "." in unsigned:
        int_part, frac_part = unsigned.split(".", 1)
        scale = len(frac_part)
    else:
        int_part, frac_part = unsigned, ""
        scale = 0

    if max_scale is not None and scale > max_scale:
        raise BadRequestException("Invalid amount format")

    if max_precision is not None and (len(int_part) + len(frac_part)) > max_precision:
        raise BadRequestException("Invalid amount format")

    try:
        as_decimal = Decimal(amount_str)
    except (InvalidOperation, ValueError):
        raise BadRequestException("Invalid amount format")

    # Decimal() could still theoretically produce non-finite values for special inputs,
    # so keep an explicit guard.
    if not as_decimal.is_finite():
        raise BadRequestException("Invalid amount format")

    if require_positive and as_decimal <= 0:
        raise BadRequestException("Amount must be positive")

    return as_decimal


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
