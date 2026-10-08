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


BACKGROUND_JOB_EVENTS_TOTAL = Counter(
    "geo_background_job_events_total",
    "Background job lifecycle events",
    ["job", "event"],
)


# 034 `F-034-8`: simulator events the run's artifact writer (`events.ndjson`) could not record. A drop used to
# leave no trace, so "nothing was lost" and "nobody counted" read the same. `reason`: `queue_full`, `write_failed`.
SIMULATOR_ARTIFACT_EVENTS_DROPPED_TOTAL = Counter(
    "geo_simulator_artifact_events_dropped_total",
    "Simulator events not recorded in a run's events.ndjson artifact",
    ["reason"],
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
