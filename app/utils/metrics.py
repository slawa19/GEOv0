from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest


HTTP_REQUESTS_TOTAL = Counter(
    "geo_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "geo_http_request_duration_seconds",
    "HTTP request duration (seconds)",
    ["method", "path"],
)


ROUTING_FAILURES_TOTAL = Counter(
    "geo_routing_failures_total",
    "Routing failures",
    ["reason"],
)

PAYMENT_EVENTS_TOTAL = Counter(
    "geo_payment_events_total",
    "Payment events",
    ["event", "result"],
)

CLEARING_EVENTS_TOTAL = Counter(
    "geo_clearing_events_total",
    "Clearing events",
    ["event", "result"],
)


RECOVERY_EVENTS_TOTAL = Counter(
    "geo_recovery_events_total",
    "Recovery/maintenance events",
    ["event", "result"],
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
