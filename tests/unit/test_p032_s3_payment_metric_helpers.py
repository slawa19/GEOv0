"""032 `S3` (P-8): the two metric helpers of `app/core/payments/service.py`.

Observable contract, not wiring: a counter that fails never reaches the payment (the call returns), the
failure leaves a debug record with its cause, and the counter is looked up on `app.utils.metrics` when the
helper is called, so a test (or a deployment) that replaces the module attribute receives the increment.

Not covered: the 20 call sites each pick the right `(event, result)` pair - that is held by the payment
suites that read the counters (`test_p019_direct_execution_effects_postgres.py`,
`test_payment_staged_post_commit.py`, `test_p029_s2_wire_contracts.py`).
"""

from __future__ import annotations

import logging

import pytest

from app.core.payments import service as payment_service


class _Counter:
    def __init__(self) -> None:
        self.seen: list[dict[str, str]] = []

    def labels(self, **labels: str) -> "_Counter":
        self.seen.append(labels)
        return self

    def inc(self) -> None:
        return None


class _BrokenCounter:
    def labels(self, **labels: str) -> "_BrokenCounter":
        raise RuntimeError("metrics backend is down")


def test_payment_counter_replaced_on_the_module_receives_the_increment(monkeypatch) -> None:
    counter = _Counter()
    monkeypatch.setattr("app.utils.metrics.PAYMENT_EVENTS_TOTAL", counter)
    payment_service._count_payment("create", "success")
    assert counter.seen == [{"event": "create", "result": "success"}]


def test_routing_failure_counter_replaced_on_the_module_receives_the_increment(monkeypatch) -> None:
    counter = _Counter()
    monkeypatch.setattr("app.utils.metrics.ROUTING_FAILURES_TOTAL", counter)
    payment_service._count_routing_failure("no_route")
    assert counter.seen == [{"reason": "no_route"}]


@pytest.mark.parametrize(
    ("attribute", "call", "marker"),
    [
        ("PAYMENT_EVENTS_TOTAL", lambda: payment_service._count_payment("create", "conflict"), "event=create result=conflict"),
        ("ROUTING_FAILURES_TOTAL", lambda: payment_service._count_routing_failure("timeout_search"), "timeout_search"),
    ],
)
def test_a_failing_counter_does_not_reach_the_payment_and_is_logged_at_debug(
    monkeypatch, caplog, attribute, call, marker
) -> None:
    monkeypatch.setattr(f"app.utils.metrics.{attribute}", _BrokenCounter())
    with caplog.at_level(logging.DEBUG, logger=payment_service.logger.name):
        call()  # must return
    records = [r for r in caplog.records if r.name == payment_service.logger.name and "payment.metric_failed" in r.getMessage()]
    assert len(records) == 1, [r.getMessage() for r in caplog.records]
    assert marker in records[0].getMessage()
    assert records[0].levelno == logging.DEBUG
    assert records[0].exc_info and "metrics backend is down" in str(records[0].exc_info[1])
