"""054-C-5: an INTERNAL real-mode payment failure does not leak its raw text or class to the simulator stream.

NARROW unit tests, named as such: no real internal failure of the money path is reachable without injecting one
(spec 054, 054-C-5: "нужен реальный внутренний сбой; инъекция не принимается"), so these drive the one function that
forms the public details (`_classify_refusal`) and the real publication of its result - a `DeferredRealPaymentEffects`
buffer resolved onto a real `SseBroadcast` subscription and the run's `last_error`. They do not show that every
internal failure reaches `_classify_refusal`; the three call-sites in `real_payments_executor.py` are read, not run.

The rule is the clearing routes' (`app/api/v1/clearing.py::_is_internal`): an internal failure is a non-`GeoException`
or a `GeoException` with the internal code E010. Its text and class stay in the log, beside an error id that the
public side carries (AGENTS.md §12). The counter-checks keep the application's own refusals (a 4xx, a timeout) as
they were: their message is our text and `map_rejection_code` reads their class.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_payments_executor import (
    DeferredRealPaymentEffects,
    _PaymentObservation,
    _classify_refusal,
)
from app.core.simulator.rejection_codes import map_rejection_code
from app.core.simulator.sse_broadcast import SseBroadcast, SseEventEmitter
from app.utils.exceptions import GeoException, RoutingException, TimeoutException

_SECRET = 'relation "pg_secret_table" does not exist at D:\\private\\path password=hunter2'
# The module's fallback logger: the executor passes its own, a direct call logs here.
_LOGGER = "app.core.simulator.real_payments_executor"


def _assert_sanitised(details: dict, *, raw: str) -> str:
    error_id = details.get("error_id")
    assert isinstance(error_id, str) and len(error_id) == 32, details
    assert raw not in repr(details), details
    assert "exc" not in details, details
    assert error_id in str(details.get("message")), details
    return error_id


def _logged(caplog: pytest.LogCaptureFixture, error_id: str) -> logging.LogRecord:
    records = [r for r in caplog.records if r.name == _LOGGER and error_id in r.getMessage()]
    assert len(records) == 1, [r.getMessage() for r in caplog.records]
    return records[0]


@pytest.mark.parametrize(
    "cause, error_type",
    [
        (RuntimeError(_SECRET), "RuntimeError"),
        # A GeoException with the internal code E010 (the default of the base class) - its message may be private.
        (GeoException(_SECRET), "GeoException"),
    ],
    ids=["non-geo", "geo-e010"],
)
def test_an_internal_failure_publishes_an_error_id_and_logs_the_cause(caplog, cause, error_type):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    status, code, details = _classify_refusal(cause)

    assert (status, code) == (None, "INTERNAL_ERROR")
    error_id = _assert_sanitised(details, raw=_SECRET)
    assert type(cause).__name__ not in repr(details), details
    record = _logged(caplog, error_id)
    # The log line is where the cause lives: its class and its traceback (the exception itself), same id.
    assert f"error_type={error_type}" in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is cause


def test_two_internal_failures_get_two_error_ids(caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    ids = {_classify_refusal(RuntimeError(_SECRET))[2].get("error_id") for _ in range(2)}

    assert len(ids) == 2 and None not in ids, ids


def test_counter_check_an_application_refusal_keeps_its_own_text_and_class():
    """Anti-vacuum: the sanitiser must not swallow the application's own refusals."""

    refusal = RoutingException("No route with enough capacity", insufficient_capacity=True, details={"reason": "x"})
    status, code, details = _classify_refusal(refusal)
    assert (status, code) == ("REJECTED", None)
    assert details["message"] == "No route with enough capacity" and details["geo_code"] == "E002", details
    assert map_rejection_code(details) == "ROUTING_NO_CAPACITY", details

    status, code, details = _classify_refusal(TimeoutException("Payment timed out"))
    assert (status, code) == (None, "PAYMENT_TIMEOUT")
    assert details["message"] == "Payment timed out", details


async def test_the_published_tx_failed_and_last_error_carry_only_the_sanitised_text(caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    logger = logging.getLogger(_LOGGER)
    lock = threading.RLock()
    run = RunRecord(run_id="run_c5", scenario_id="scenario", mode="real", state="running")
    sse = SseBroadcast(
        lock=lock,
        runs={run.run_id: run},
        get_event_buffer_max=lambda: 16,
        get_event_buffer_ttl_sec=lambda: 0,
        get_sub_queue_max=lambda: 16,
        enqueue_event_artifact=lambda _run_id, _payload: None,
        logger=logger,
    )
    utc_now = lambda: datetime.now(timezone.utc)  # noqa: E731
    emitter = SseEventEmitter(sse=sse, utc_now=utc_now, logger=logger)

    sub = await sse.subscribe(run.run_id, equivalent="UAH")
    _status, code, details = _classify_refusal(RuntimeError(_SECRET))
    buffer = DeferredRealPaymentEffects(
        lock=lock,
        emitter=emitter,
        logger=logger,
        utc_now=utc_now,
        run_id=run.run_id,
        run=run,
        items=[
            _PaymentObservation(
                seq=0,
                outcome="error",
                equivalent="UAH",
                sender_pid="A",
                receiver_pid="B",
                amount="1.00",
                edges=[{"from": "A", "to": "B"}],
                error_code=str(code),
                error_details=details,
            )
        ],
    )

    assert buffer.apply_after_rollback() is True

    event = sub.queue.get_nowait()
    assert event["type"] == "tx.failed", event
    error_id = _assert_sanitised(event["error"]["details"], raw=_SECRET)
    assert event["error"]["code"] == "INTERNAL_ERROR", event
    assert _SECRET not in event["error"]["message"] and error_id in event["error"]["message"], event
    assert "RuntimeError" not in repr(event), event
    assert run.last_error is not None and run.last_error["code"] == "INTERNAL_ERROR", run.last_error
    assert _SECRET not in run.last_error["message"] and error_id in run.last_error["message"], run.last_error
    _logged(caplog, error_id)
