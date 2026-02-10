from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.utils.exceptions import BadRequestException


def _format_decimal(value: Decimal) -> str:
    # Normalize to remove exponent and trailing zeros.
    q = value.normalize()
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s == "-0":
        s = "0"
    # Canonical JSON must never emit exponent notation.
    # (format(..., "f") should already guarantee this, but keep a hard check.)
    if "e" in s.lower():
        raise BadRequestException(
            "Exponent notation is not allowed in canonical JSON",
            details={"value": str(value)},
        )
    return s


def _normalize(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, dict):
        # Normalize keys to strings and sort deterministically.
        items: list[tuple[str, Any]] = []
        for k, v in value.items():
            items.append((str(k), _normalize(v)))
        return {k: v for k, v in sorted(items, key=lambda kv: kv[0])}

    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]

    if isinstance(value, Decimal):
        # Disallow special decimal values (NaN / +/-Infinity).
        # Even though project payloads usually carry amounts as strings,
        # canonical_json() can be used in other contexts.
        if not value.is_finite():
            raise BadRequestException(
                "Non-finite Decimal is not allowed in canonical JSON",
                details={"value": str(value)},
            )
        # Disallow exponent notation (e.g. Decimal('1E+3')).
        # This avoids ambiguous numeric spellings and potential DoS from huge expansions.
        if "e" in str(value).lower():
            raise BadRequestException(
                "Exponent notation is not allowed in canonical JSON",
                details={"value": str(value)},
            )
        return _format_decimal(value)

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    if isinstance(value, (int, str, bool)):
        return value

    if isinstance(value, float):
        # Avoid float JSON instability and JSON exponent notation.
        # Float must never appear in canonical payload.
        raise BadRequestException(
            "Float is not allowed in canonical JSON",
            details={"value": repr(value)},
        )

    return str(value)


def canonical_json(payload: dict) -> bytes:
    """Deterministic canonical JSON for signing.

    Rules (protocol Appendix A inspired):
    - keys sorted alphabetically
    - no extra whitespace
    - UTF-8
    - stable normalization for Decimal/UUID/datetime
    """
    normalized = _normalize(payload)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
