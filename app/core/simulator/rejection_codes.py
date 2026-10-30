from __future__ import annotations

from typing import Any

from app.core.money_boundary import MoneyBoundary

#: 054-A-7a review (owner, 2026-10-30: "if the automatic payments do not go through, the scenario is exhausted"): the
#: refusals that are the SCENARIO's own - no route, no capacity, a trust line's limit, state or policy (a policy
#: refusal is a `RoutingException` with `details.reason = "policy"`, so `ROUTING_*`). A tick with nothing committed and
#: no error extends the run's stall when at least one of its refusals is one of these (`_real_consec_all_rejected_ticks`,
#: `is_scenario_refusal`). `TRUSTLINE_*` is a mapping with no producer in the application (`TrustLineException` is not
#: raised under `app/`; capacity and limits refuse as `RoutingException`), kept because it is harmless. `CONFLICT` is
#: not here: in the payment path it is a money stop, a participant out of circulation, a held payment lock (`busy`) or
#: a reused `tx_id` - none of them is the line's policy.
#: A scripted payment whose sender the story has not introduced yet (036 B1) is the story's own refusal too and counts as
#: it did before the review: its `details.reason` is `SCRIPTED_SENDER_NOT_FOUND_REASON` with no exception class.
SCENARIO_REFUSAL_CODES = frozenset({
    "ROUTING_NO_CAPACITY",
    "ROUTING_NO_ROUTE",
    "ROUTING_REJECTED",
    "TRUSTLINE_LIMIT_EXCEEDED",
    "TRUSTLINE_NOT_ACTIVE",
    "TRUSTLINE_REJECTED",
})

#: `details.reason` of a scripted payment refused because its sender is not in the run (`real_payments_executor.py`,
#: `_do_one`); the same reason of a PLANNED payment is an error of the run, never a refusal.
SCRIPTED_SENDER_NOT_FOUND_REASON = "SENDER_NOT_FOUND"


def is_scenario_refusal(rejection_code: str, err_details: Any) -> bool:
    """054-A-7a review: the refusal is the scenario's own (`SCENARIO_REFUSAL_CODES`) and not a money stop - one such
    refusal in a tick with nothing committed and no error makes the tick count toward the stall.

    A money stop - an equivalent the operator stopped or one under an integrity hold - is recognised by its structured
    `details.reason` (`MoneyBoundary.MONEY_STOP_REASONS`, the refusal's `details` as `_classify_refusal` records them),
    never by its message. It maps to `CONFLICT` and is outside the set already; the reason is checked as well, so a
    later mapping change cannot make a stopped equivalent look like an exhausted scenario.
    """

    if not isinstance(err_details, dict):
        return False
    inner = err_details.get("details")
    if isinstance(inner, dict) and inner.get("reason") in MoneyBoundary.MONEY_STOP_REASONS:
        return False
    if not err_details.get("exc") and err_details.get("reason") == SCRIPTED_SENDER_NOT_FOUND_REASON:
        return True
    return str(rejection_code) in SCENARIO_REFUSAL_CODES


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
