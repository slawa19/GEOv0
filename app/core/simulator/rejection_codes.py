from __future__ import annotations

from typing import Any


def map_rejection_code(err_details: Any) -> str:
    """Map structured error details to a stable simulator rejection code.

    This is intentionally a best-effort mapping for UI/analytics.
    Full diagnostics remain available in tx.failed.error.details.
    """

    default = "PAYMENT_REJECTED"
    if not isinstance(err_details, dict):
        return default

    exc_name = str(err_details.get("exc") or "")
    geo_code = str(err_details.get("geo_code") or "")
    msg = str(err_details.get("message") or "")

    if exc_name == "RoutingException":
        # E002 = insufficient capacity, E001 = generic routing not-found.
        if geo_code == "E002":
            return "ROUTING_NO_CAPACITY"
        if geo_code == "E001":
            return "ROUTING_NO_ROUTE"
        return "ROUTING_REJECTED"

    if exc_name == "TrustLineException":
        # E003 = limit exceeded, E004 = trust line inactive.
        if geo_code == "E003":
            return "TRUSTLINE_LIMIT_EXCEEDED"
        if geo_code == "E004":
            return "TRUSTLINE_NOT_ACTIVE"
        return "TRUSTLINE_REJECTED"

    # Generic HTTP-ish application errors (GeoException subclasses)
    # Mapped to stable UI/analytics labels.
    if exc_name == "NotFoundException":
        # Best-effort parsing by message. Keep this intentionally simple and
        # stable (tests rely on these exact outcomes).
        m = msg.lower()
        if "equivalent" in m:
            return "EQUIVALENT_NOT_FOUND"
        if "participants not found" in m or "participant" in m:
            return "PARTICIPANT_NOT_FOUND"
        if "transaction" in m or "tx" in m:
            return "TX_NOT_FOUND"
        return default

    if exc_name == "BadRequestException":
        # E009 = validation error
        if geo_code == "E009":
            return "INVALID_INPUT"
        return "INVALID_INPUT"

    if exc_name == "ConflictException":
        return "CONFLICT"

    if exc_name == "UnauthorizedException":
        return "UNAUTHORIZED"

    if exc_name == "ForbiddenException":
        return "FORBIDDEN"

    if exc_name in {"InvalidSignatureException", "CryptoException"}:
        return "INVALID_SIGNATURE"

    return default
