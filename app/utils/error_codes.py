from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Standard GEO error codes (spec section 9.4)."""

    E001 = "E001"  # Routing: Route not found
    E002 = "E002"  # Routing: Insufficient capacity
    E003 = "E003"  # TrustLine: Limit exceeded
    E004 = "E004"  # TrustLine: Trust line not active
    E005 = "E005"  # Auth: Invalid signature
    E006 = "E006"  # Auth: Insufficient permissions
    E007 = "E007"  # Timeout: Operation timeout
    E008 = "E008"  # Conflict: State conflict
    E009 = "E009"  # Validation: Invalid input
    E010 = "E010"  # Internal: Internal error


ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.E001: "Route not found",
    ErrorCode.E002: "Insufficient capacity",
    ErrorCode.E003: "Trust line limit exceeded",
    ErrorCode.E004: "Trust line not active",
    ErrorCode.E005: "Invalid signature",
    ErrorCode.E006: "Insufficient permissions",
    ErrorCode.E007: "Operation timeout",
    ErrorCode.E008: "State conflict",
    ErrorCode.E009: "Validation error",
    ErrorCode.E010: "Internal server error",
}
