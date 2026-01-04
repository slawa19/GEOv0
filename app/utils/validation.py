import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.utils.exceptions import BadRequestException


_EQUIVALENT_CODE_RE = re.compile(r"^[A-Z0-9_]{1,16}$")


def validate_equivalent_code(code: str) -> None:
    if not isinstance(code, str) or not code or not _EQUIVALENT_CODE_RE.fullmatch(code):
        raise BadRequestException("Invalid equivalent code")


def validate_idempotency_key(key: str) -> str:
    if not isinstance(key, str):
        raise BadRequestException("Invalid Idempotency-Key")

    normalized = key.strip()
    if not normalized:
        raise BadRequestException("Invalid Idempotency-Key")
    if len(normalized) > 128:
        raise BadRequestException("Idempotency-Key too long")

    return normalized


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
