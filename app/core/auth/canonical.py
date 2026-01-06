from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID


def _format_decimal(value: Decimal) -> str:
    # Normalize to remove exponent and trailing zeros.
    q = value.normalize()
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s == "-0":
        s = "0"
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
        # Avoid float JSON instability; represent as normalized string.
        return _format_decimal(Decimal(repr(value)))

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
