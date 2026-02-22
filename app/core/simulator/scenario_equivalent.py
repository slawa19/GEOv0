from __future__ import annotations

from typing import Any, Mapping


def scenario_default_equivalent(scenario: Mapping[str, Any]) -> str:
    """Return scenario default equivalent code.

    JSON schema uses `baseEquivalent` as the shorthand.
    We also accept legacy `equivalent` for backward compatibility.
    """

    return str(scenario.get("baseEquivalent") or scenario.get("equivalent") or "").strip().upper()


def effective_equivalent(scenario: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """Resolve equivalent for a trustline/event payload.

    If payload doesn't specify `equivalent`, fall back to scenario default.
    """

    eq = str(payload.get("equivalent") or "").strip().upper()
    return eq if eq else scenario_default_equivalent(scenario)
