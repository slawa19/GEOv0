"""Real-mode honesty contract for simulator analytics (spec 007, F-007-1).

Most tests here are pure unit tests: the DB boundary is replaced by an
in-process fake session, so no database is required. The tests named
``*_end_to_end`` are DB-backed (shared sqlite ``db_session`` fixture) and walk
the whole chain writer -> simulator_run_metrics -> reader -> API payload.

Covered:

* real mode never returns synthetic data, whatever the storage does;
* an empty bottlenecks table is a normal state and yields an empty result;
* "not measured" (``null``) is distinguishable from "measured zero" at every
  stage: the tick producer, the writer, the DB row and the API response;
* explicitly synthetic mode (``run.mode != "real"``) keeps synthesising;
* the metric key set is one set on all four sides — canonical OpenAPI, pydantic
  model, reader and writer (spec 007, T713);
* answering ``GET bottlenecks`` writes nothing to the database (spec 007, T714).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, get_args

import pytest
import yaml
from sqlalchemy import func, select

import app.core.simulator.metrics_bottlenecks as metrics_bottlenecks_module
import app.db.session as db_session
from app.config import settings
from app.core.simulator import storage as simulator_storage
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.core.simulator.real_tick_metrics import RealTickMetrics
from app.db.models.simulator_storage import SimulatorRunBottleneck, SimulatorRunMetric
from app.schemas.simulator import MetricSeriesKey, metric_point_value
from app.utils.exceptions import GeoException


LOGGER_NAME = "tests.simulator.metrics_bottlenecks"


# --- DB boundary fakes -------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeResult:
    def __init__(self, *, rows: Optional[list[Any]] = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _SharedSession:
    """Async context manager handing out an already-open session (DB tests)."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _FakeSession:
    """Returns queued results in order; a queued Exception is raised instead."""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.execute_calls = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def execute(self, _query: Any) -> Any:
        if self.execute_calls >= len(self._results):
            raise AssertionError("unexpected extra session.execute() call")
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        if isinstance(result, Exception):
            raise result
        return result


class _ExplodingSessionFactory:
    """Fails when the session itself cannot be opened (connection refused)."""

    def __call__(self) -> Any:
        raise RuntimeError("connection refused")


def _install_session(monkeypatch: pytest.MonkeyPatch, factory: Any) -> None:
    monkeypatch.setattr(db_session, "AsyncSessionLocal", factory, raising=False)


# --- Fixtures under test -----------------------------------------------------


_SCENARIO_RAW: dict[str, Any] = {
    "equivalent": "UAH",
    "trustlines": [
        {"from": "alice", "to": "bob", "limit": "100"},
        {"from": "bob", "to": "carol", "limit": "100"},
    ],
}


# 2026-08-20 / p007_t715: the column is Numeric(20, 8), so a row handed back by
# SQLAlchemy carries a Decimal quantized to eight fractional digits. The fake
# rows below mirror that exactly, otherwise the expectations here would describe
# a shape the database never produces.
_COLUMN_QUANT = Decimal("0.00000001")


def _metric_row(
    key: str, t_ms: int, value: Optional[Decimal | float | str]
) -> SimpleNamespace:
    stored = (
        None
        if value is None
        else Decimal(str(value)).quantize(_COLUMN_QUANT)
    )
    return SimpleNamespace(key=key, t_ms=t_ms, value=stored)


def _bottleneck_row(
    *,
    target_id: str,
    score: float,
    reason_code: str = "LOW_AVAILABLE",
    details: Optional[dict[str, Any]] = None,
    target_type: str = "edge",
) -> SimpleNamespace:
    return SimpleNamespace(
        target_type=target_type,
        target_id=target_id,
        score=score,
        reason_code=reason_code,
        details=details if details is not None else {},
    )


def _build(
    *,
    mode: str = "real",
    db_enabled: bool = True,
    logger: Optional[logging.Logger] = None,
) -> MetricsBottlenecks:
    run = SimpleNamespace(
        run_id="run-1",
        scenario_id="scn-1",
        mode=mode,
        state="running",
        sim_time_ms=5_000,
        intensity_percent=50,
        _edges_by_equivalent={"UAH": [("alice", "bob"), ("bob", "carol")]},
        _scenario_raw=_SCENARIO_RAW,
    )
    scenario = SimpleNamespace(scenario_id="scn-1", raw=_SCENARIO_RAW)
    return MetricsBottlenecks(
        lock=threading.RLock(),
        runs={"run-1": run},
        scenarios={"scn-1": scenario},
        utc_now=lambda: None,
        db_enabled=lambda: db_enabled,
        logger=logger or logging.getLogger(LOGGER_NAME),
    )


def _series(response: Any, key: str) -> Any:
    for item in response.series:
        if item.key == key:
            return item
    raise AssertionError(f"series {key} missing from response")


def _values(response: Any, key: str) -> list[Optional[str]]:
    """`MetricPoint.v` is a decimal string since p007_t715, or None."""

    return [p.v for p in _series(response, key).points]


def _canonical_metric_series_keys() -> list[str]:
    """The `MetricSeriesKey` enum as declared in the canonical OpenAPI file."""

    canon = Path(__file__).resolve().parents[2] / "api" / "openapi.yaml"
    doc = yaml.safe_load(canon.read_text(encoding="utf-8"))
    return list(doc["components"]["schemas"]["MetricSeriesKey"]["enum"])


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING]


# --- metrics: DB-backed success path ----------------------------------------


async def test_real_mode_metrics_serves_persisted_points_with_carry_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [
                _FakeResult(
                    rows=[
                        _metric_row("success_rate", 2_000, 50.0),
                        _metric_row("success_rate", 4_000, 75.0),
                    ]
                )
            ]
        ),
    )

    resp = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=4_000, step_ms=1_000
    )

    # Before the first measurement there is no value to report; afterwards the
    # last measurement is carried forward until the next tick.
    assert _values(resp, "success_rate") == [
        None,
        None,
        "50.00000000",
        "50.00000000",
        "75.00000000",
    ]
    assert resp.run_id == "run-1"
    assert resp.equivalent == "UAH"


async def test_real_mode_metrics_distinguish_missing_measurement_from_measured_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Central criterion: null (no measurement) != 0 (measured zero)."""

    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [
                _FakeResult(
                    rows=[
                        # A genuine zero measurement at t=2000.
                        _metric_row("total_debt", 2_000, 0.0),
                        # A persisted NULL must not count as a measurement.
                        _metric_row("clearing_volume", 1_000, None),
                    ]
                )
            ]
        ),
    )

    resp = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=3_000, step_ms=1_000
    )

    total_debt = _values(resp, "total_debt")
    assert total_debt[0] is None and total_debt[1] is None
    # A measured zero is the string "0.00000000"; it is not None and it is not
    # dropped. `None` and a zero measurement stay two different things.
    assert total_debt[2] == "0.00000000" and total_debt[3] == "0.00000000"

    # A series with no usable measurement at all is null everywhere, not zeros.
    assert _values(resp, "clearing_volume") == [None, None, None, None]
    assert _values(resp, "bottlenecks_score") == [None, None, None, None]

    # The distinction must survive to the wire, not only in Python objects.
    wire = resp.model_dump(mode="json")
    wire_debt = next(s for s in wire["series"] if s["key"] == "total_debt")["points"]
    assert [p["v"] for p in wire_debt] == [None, None, "0.00000000", "0.00000000"]


# --- metrics: failure paths --------------------------------------------------


async def test_real_mode_metrics_db_failure_raises_instead_of_synthesising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_session(
        monkeypatch,
        lambda: _FakeSession([RuntimeError("db exploded")]),
    )
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with pytest.raises(GeoException) as excinfo:
        await _build().build_metrics(
            run_id="run-1", equivalent="UAH", from_ms=0, to_ms=4_000, step_ms=1_000
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.details["run_id"] == "run-1"
    assert excinfo.value.details["equivalent"] == "UAH"
    assert excinfo.value.code == "E010"
    assert isinstance(excinfo.value.__cause__, RuntimeError)

    # The exception class is diagnostic: log only, never the public body.
    assert "error_class" not in excinfo.value.details
    assert "RuntimeError" not in str(excinfo.value.to_dict())

    records = _warnings(caplog)
    assert len(records) == 1
    assert "simulator.metrics.real_mode_db_read_failed" in records[0].getMessage()
    assert "run_id=run-1" in records[0].getMessage()
    assert "error_class=RuntimeError" in records[0].getMessage()


async def test_real_mode_metrics_session_open_failure_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_session(monkeypatch, _ExplodingSessionFactory())
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with pytest.raises(GeoException):
        await _build().build_metrics(
            run_id="run-1", equivalent="UAH", from_ms=0, to_ms=1_000, step_ms=1_000
        )

    assert len(_warnings(caplog)) == 1


async def test_real_mode_metrics_without_storage_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Storage disabled in real mode is a failure, not a licence to synthesise."""

    _install_session(monkeypatch, lambda: _FakeSession([]))
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with pytest.raises(GeoException) as excinfo:
        await _build(db_enabled=False).build_metrics(
            run_id="run-1", equivalent="UAH", from_ms=0, to_ms=1_000, step_ms=1_000
        )

    assert excinfo.value.details["reason"] == "storage_disabled"
    # No read was attempted, so the message must not claim a failed read.
    assert "could not be read" not in excinfo.value.message
    assert "persistence is disabled" in excinfo.value.message
    assert len(_warnings(caplog)) == 1


async def test_real_mode_failure_traceback_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Every failure warns; only the traceback is throttled (polling clients)."""

    _install_session(
        monkeypatch, lambda: _FakeSession([RuntimeError("db exploded")])
    )
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    runtime = _build()

    for _ in range(3):
        with pytest.raises(GeoException):
            await runtime.build_metrics(
                run_id="run-1", equivalent="UAH", from_ms=0, to_ms=1_000, step_ms=1_000
            )

    records = _warnings(caplog)
    assert len(records) == 3
    assert [r.exc_info is not None for r in records] == [True, False, False]

    # Re-arms once the interval elapses.
    caplog.clear()
    monkeypatch.setattr(
        metrics_bottlenecks_module, "TRACEBACK_MIN_INTERVAL_S", 0.0, raising=False
    )
    with pytest.raises(GeoException):
        await runtime.build_metrics(
            run_id="run-1", equivalent="UAH", from_ms=0, to_ms=1_000, step_ms=1_000
        )
    assert [r.exc_info is not None for r in _warnings(caplog)] == [True]


async def test_fixtures_mode_metrics_still_synthesise_numeric_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter-check: the synthetic generator is alive for explicitly synthetic runs.

    Without this, the real-mode assertions above could pass simply because
    nothing is ever produced anywhere.
    """

    _install_session(monkeypatch, lambda: _FakeSession([]))

    resp = await _build(mode="fixtures", db_enabled=False).build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=3_000, step_ms=1_000
    )

    values = _values(resp, "success_rate")
    assert len(values) == 4
    assert all(v is not None for v in values)


# --- bottlenecks: DB-backed paths -------------------------------------------


async def test_real_mode_bottlenecks_serve_persisted_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [
                _FakeResult(scalar="2026-08-20T10:00:00"),
                _FakeResult(
                    rows=[
                        _bottleneck_row(
                            target_id="alice->bob",
                            score=0.9,
                            details={
                                "label": "Low available capacity",
                                "suggested_action": "Increase trust limit",
                            },
                        ),
                        _bottleneck_row(
                            target_id="bob->carol",
                            score=0.2,
                            reason_code="HIGH_USED",
                        ),
                        # Non-edge and malformed targets are skipped.
                        _bottleneck_row(
                            target_id="alice", score=0.99, target_type="node"
                        ),
                        _bottleneck_row(target_id="broken", score=0.98),
                    ]
                ),
            ]
        ),
    )

    resp = await _build().build_bottlenecks(
        run_id="run-1", equivalent="UAH", limit=20, min_score=None
    )

    assert [(i.target.from_, i.target.to, i.score) for i in resp.items] == [
        ("alice", "bob", 0.9),
        ("bob", "carol", 0.2),
    ]
    assert resp.items[0].label == "Low available capacity"
    assert resp.items[0].reason_code == "LOW_AVAILABLE"
    assert resp.items[1].label is None


async def test_real_mode_bottlenecks_apply_min_score_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [
                _FakeResult(scalar="2026-08-20T10:00:00"),
                _FakeResult(
                    rows=[
                        _bottleneck_row(target_id="alice->bob", score=0.9),
                        _bottleneck_row(target_id="bob->carol", score=0.5),
                        _bottleneck_row(target_id="carol->dave", score=0.1),
                    ]
                ),
            ]
        ),
    )

    resp = await _build().build_bottlenecks(
        run_id="run-1", equivalent="UAH", limit=1, min_score=0.4
    )

    assert [(i.target.from_, i.score) for i in resp.items] == [("alice", 0.9)]


async def test_real_mode_bottlenecks_empty_table_returns_empty_not_synthetic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression: `latest is None` used to raise UnboundLocalError and fall
    through to synthetic items derived from scenario trustlines."""

    _install_session(
        monkeypatch,
        lambda: _FakeSession([_FakeResult(scalar=None)]),
    )
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    resp = await _build().build_bottlenecks(
        run_id="run-1", equivalent="UAH", limit=20, min_score=None
    )

    assert resp.items == []
    assert _warnings(caplog) == []


async def test_fixtures_mode_bottlenecks_still_synthesise_from_trustlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter-check for the empty-table test: the same scenario does produce
    synthetic items when the mode is explicitly synthetic."""

    _install_session(monkeypatch, lambda: _FakeSession([]))

    resp = await _build(mode="fixtures", db_enabled=False).build_bottlenecks(
        run_id="run-1", equivalent="UAH", limit=20, min_score=None
    )

    assert {(i.target.from_, i.target.to) for i in resp.items} == {
        ("alice", "bob"),
        ("bob", "carol"),
    }


# --- bottlenecks: failure paths ---------------------------------------------


async def test_real_mode_bottlenecks_db_failure_raises_instead_of_synthesising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_session(
        monkeypatch,
        lambda: _FakeSession([RuntimeError("db exploded")]),
    )
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with pytest.raises(GeoException) as excinfo:
        await _build().build_bottlenecks(
            run_id="run-1", equivalent="UAH", limit=20, min_score=None
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.details["run_id"] == "run-1"
    assert excinfo.value.code == "E010"
    assert "error_class" not in excinfo.value.details
    assert isinstance(excinfo.value.__cause__, RuntimeError)

    records = _warnings(caplog)
    assert len(records) == 1
    assert "simulator.bottlenecks.real_mode_db_read_failed" in records[0].getMessage()
    assert "run_id=run-1" in records[0].getMessage()
    assert "error_class=RuntimeError" in records[0].getMessage()


async def test_real_mode_bottlenecks_second_query_failure_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [_FakeResult(scalar="2026-08-20T10:00:00"), RuntimeError("row read failed")]
        ),
    )
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with pytest.raises(GeoException):
        await _build().build_bottlenecks(
            run_id="run-1", equivalent="UAH", limit=20, min_score=None
        )

    assert len(_warnings(caplog)) == 1


async def test_real_mode_bottlenecks_without_storage_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_session(monkeypatch, lambda: _FakeSession([]))
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    with pytest.raises(GeoException) as excinfo:
        await _build(db_enabled=False).build_bottlenecks(
            run_id="run-1", equivalent="UAH", limit=20, min_score=None
        )

    assert excinfo.value.details["reason"] == "storage_disabled"
    assert len(_warnings(caplog)) == 1


# --- writer stage: the tick producer must not invent measurements -----------


class _RaisingSession:
    def __init__(self) -> None:
        self.execute_calls = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        self.execute_calls += 1
        raise RuntimeError("snapshot failed")


class _DebtSession:
    """First execute() resolves equivalents, the next ones return debt sums.

    `SUM(debts.amount)` over a Numeric(20, 8) column yields a Decimal, which is
    what the fake returns since p007_t715.
    """

    def __init__(self, total: Decimal) -> None:
        self.execute_calls = 0
        self._total = total

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _FakeResult(rows=[(1, "UAH")])
        return _FakeResult(scalar=self._total)


def _tick_metrics(every_n: int = 1) -> RealTickMetrics:
    return RealTickMetrics(
        lock=threading.RLock(),
        logger=logging.getLogger(LOGGER_NAME),
        real_db_metrics_every_n_ticks=every_n,
    )


def _tick_run(tick_index: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run-1",
        tick_index=tick_index,
        _real_total_debt_by_eq={"UAH": Decimal("42")},
        _real_total_debt_tick=0,
        _edges_by_equivalent={"UAH": [("alice", "bob")]},
    )


async def _populate(
    tick_metrics: RealTickMetrics, run: SimpleNamespace, session: Any
) -> dict[str, dict[str, Optional[Decimal | float]]]:
    values: dict[str, dict[str, Optional[Decimal | float]]] = {"UAH": {}}
    await tick_metrics.populate_per_eq_metric_values(
        session=session,
        run=run,
        scenario={"participants": [{"status": "active"}, {"status": "active"}]},
        equivalents=["UAH"],
        per_eq_route={},
        clearing_volume_by_eq={},
        per_eq_metric_values=values,
    )
    return values


async def test_measured_total_debt_is_recorded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    values = await _populate(
        _tick_metrics(), _tick_run(), _DebtSession(Decimal("12.5"))
    )

    assert values["UAH"]["total_debt"] == Decimal("12.5")
    # p007_t715: money leaves the producer as Decimal, not as float.
    assert isinstance(values["UAH"]["total_debt"], Decimal)
    assert _warnings(caplog) == []


async def test_failed_total_debt_snapshot_is_not_reported_as_measurement(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken DB during a run must not persist 0.0 (or a stale value) as data."""

    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    session = _RaisingSession()

    values = await _populate(_tick_metrics(), _tick_run(), session)

    assert session.execute_calls == 1
    assert "total_debt" not in values["UAH"]
    # No successful route this tick: the average is undefined, not zero.
    assert "avg_route_length" not in values["UAH"]
    # Genuine measurements of this tick stay numeric.
    assert values["UAH"]["clearing_volume"] == Decimal("0")
    assert values["UAH"]["active_trustlines"] == 1.0
    assert values["UAH"]["active_participants"] == 2.0

    records = _warnings(caplog)
    assert len(records) == 1
    assert "simulator.real.total_debt_snapshot_failed" in records[0].getMessage()
    assert "run_id=run-1" in records[0].getMessage()
    assert "error_class=RuntimeError" in records[0].getMessage()


async def test_throttled_tick_does_not_stamp_stale_total_debt() -> None:
    session = _RaisingSession()  # must never be touched on a throttled tick

    values = await _populate(_tick_metrics(every_n=5), _tick_run(tick_index=3), session)

    assert session.execute_calls == 0
    assert "total_debt" not in values["UAH"]


async def test_measured_route_length_is_recorded() -> None:
    """Counter-check: avg_route_length is omitted only when there is no route."""

    tick_metrics = _tick_metrics()
    values: dict[str, dict[str, Optional[Decimal | float]]] = {"UAH": {}}
    await tick_metrics.populate_per_eq_metric_values(
        session=_DebtSession(Decimal("0")),
        run=_tick_run(),
        scenario={},
        equivalents=["UAH"],
        per_eq_route={"UAH": {"route_len_n": 2.0, "route_len_sum": 7.0}},
        clearing_volume_by_eq={"UAH": Decimal("4")},
        per_eq_metric_values=values,
    )

    assert values["UAH"]["avg_route_length"] == 3.5
    assert values["UAH"]["clearing_volume"] == Decimal("4")


# --- end to end: producer -> writer -> DB row -> reader -> API payload -------


async def test_missing_measurement_stays_null_end_to_end(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB-backed (sqlite): proves the whole chain preserves "not measured".

    Both flavours appear in the same series: at t=1000 there were no payment
    attempts at all (success_rate is undefined), at t=2000 two payments were
    rejected (success_rate is a measured 0%).
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)

    await simulator_storage.write_tick_metrics(
        run_id="run-1",
        t_ms=1_000,
        per_equivalent={
            "UAH": {"committed": 0, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        # The producer measured total_debt (exactly zero) but had no route to
        # average, so avg_route_length is absent.
        metric_values_by_eq={
            "UAH": {"total_debt": Decimal("0"), "clearing_volume": Decimal("0")}
        },
        session=db_session,
    )
    await simulator_storage.write_tick_metrics(
        run_id="run-1",
        t_ms=2_000,
        per_equivalent={
            "UAH": {"committed": 0, "rejected": 2, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={
            "UAH": {"total_debt": Decimal("0"), "clearing_volume": Decimal("0")}
        },
        session=db_session,
    )

    stored = {
        (key, t_ms): value
        for (key, t_ms, value) in (
            await db_session.execute(
                select(
                    SimulatorRunMetric.key,
                    SimulatorRunMetric.t_ms,
                    SimulatorRunMetric.value,
                ).where(SimulatorRunMetric.run_id == "run-1")
            )
        ).all()
    }
    assert stored[("success_rate", 1_000)] is None  # no attempts: not measured
    assert stored[("success_rate", 2_000)] == Decimal("0")  # measured zero percent
    assert stored[("avg_route_length", 1_000)] is None
    assert stored[("total_debt", 1_000)] == Decimal("0")
    assert stored[("active_trustlines", 1_000)] is None

    _install_session(monkeypatch, lambda: _SharedSession(db_session))

    resp = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=3_000, step_ms=1_000
    )

    zero = "0.00000000"
    assert _values(resp, "success_rate") == [None, None, zero, zero]
    assert _values(resp, "avg_route_length") == [None, None, None, None]
    assert _values(resp, "total_debt") == [None, zero, zero, zero]

    wire = resp.model_dump(mode="json")
    wire_success = next(
        item for item in wire["series"] if item["key"] == "success_rate"
    )["points"]
    assert [point["v"] for point in wire_success] == [None, None, zero, zero]


# --- T715: money stays exact decimal from the source to the wire -------------


# A value float cannot hold: 19 significant digits against ~17 for binary64.
# Every assertion below would fail if any stage narrowed through float.
_TOO_PRECISE_FOR_FLOAT = Decimal("12345678901.12345678")


def test_the_probe_value_really_is_beyond_float() -> None:
    """Anti-vacuum: prove the discriminator discriminates.

    Without this, the exactness assertions could pass simply because the chosen
    value happens to be representable as a float.
    """

    assert Decimal(str(float(_TOO_PRECISE_FOR_FLOAT))) != _TOO_PRECISE_FOR_FLOAT


def test_metric_point_value_renders_one_canonical_decimal_string() -> None:
    """The single serialization point: fixed scale, plain notation, null kept."""

    assert metric_point_value(None) is None
    # A measured zero is a value, not a missing measurement.
    assert metric_point_value(Decimal("0")) == "0.00000000"
    # Exponential money strings break consumer parsers; never emit them.
    assert metric_point_value(Decimal("1E-8")) == "0.00000001"
    assert metric_point_value(Decimal("1E+11")) == "100000000000.00000000"
    assert metric_point_value(_TOO_PRECISE_FOR_FLOAT) == "12345678901.12345678"
    for probe in (Decimal("1E-8"), Decimal("1E+11"), Decimal("0")):
        rendered = metric_point_value(probe)
        assert rendered is not None and "E" not in rendered.upper()

    # One form, whatever the producer handed in: the same number written as a
    # float, as an int, as a str and as a Decimal renders identically.
    assert (
        metric_point_value(45.3)
        == metric_point_value("45.3")
        == metric_point_value(Decimal("45.3"))
        == "45.30000000"
    )
    assert metric_point_value(2) == metric_point_value(2.0) == "2.00000000"
    # Every rendering carries the column's scale, so string comparison and
    # de-duplication on `v` stay stable.
    for probe in (0, 2.0, 45.3, Decimal("50.00000000"), _TOO_PRECISE_FOR_FLOAT):
        rendered = metric_point_value(probe)
        assert rendered is not None
        assert len(rendered.split(".")[1]) == 8, rendered


async def test_real_and_synthetic_producers_emit_the_same_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One endpoint, one field, one shape - whichever branch produced it.

    Before this was pinned, the DB-backed reader emitted `"0.00000000"` while
    the synthetic generator emitted `"0.0"` and `"45.3"` for the same field.
    """

    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [_FakeResult(rows=[_metric_row("active_trustlines", 0, 2.0)])]
        ),
    )
    persisted = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=0, step_ms=1_000
    )

    _install_session(monkeypatch, lambda: _FakeSession([]))
    synthetic = await _build(mode="fixtures", db_enabled=False).build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=0, step_ms=1_000
    )

    # The same measured cardinality, produced by the two different branches.
    assert _values(persisted, "active_trustlines") == ["2.00000000"]
    assert _values(synthetic, "active_trustlines") == ["2.00000000"]

    # And no synthetic series slips out in another shape.
    for series in synthetic.series:
        for point in series.points:
            assert point.v is not None
            assert len(point.v.split(".")[1]) == 8, (series.key, point.v)


async def test_money_series_reach_the_wire_without_losing_precision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reader stage: a Decimal from the column reaches the response intact."""

    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [
                _FakeResult(
                    rows=[
                        _metric_row("total_debt", 1_000, _TOO_PRECISE_FOR_FLOAT),
                        _metric_row("clearing_volume", 1_000, Decimal("0.00000001")),
                    ]
                )
            ]
        ),
    )

    resp = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=1_000, to_ms=1_000, step_ms=1_000
    )

    assert _values(resp, "total_debt") == ["12345678901.12345678"]
    assert _values(resp, "clearing_volume") == ["0.00000001"]

    # And on the wire, not only in Python objects.
    wire = resp.model_dump(mode="json")
    wire_debt = next(s for s in wire["series"] if s["key"] == "total_debt")["points"]
    assert [point["v"] for point in wire_debt] == ["12345678901.12345678"]


async def test_total_debt_snapshot_leaves_the_producer_as_exact_decimal() -> None:
    """Source stage: `float(total)` over SUM(debts.amount) is gone."""

    values = await _populate(
        _tick_metrics(), _tick_run(), _DebtSession(_TOO_PRECISE_FOR_FLOAT)
    )

    assert values["UAH"]["total_debt"] == _TOO_PRECISE_FOR_FLOAT


async def test_clearing_volume_leaves_the_producer_as_exact_decimal() -> None:
    """Source stage: `float(cleared_amount_dec)` is gone from the clearing path.

    The engine hands the tick a Decimal per equivalent; the metrics producer
    must forward it untouched.
    """

    values: dict[str, dict[str, Optional[Decimal | float]]] = {"UAH": {}}
    await _tick_metrics().populate_per_eq_metric_values(
        session=_DebtSession(Decimal("0")),
        run=_tick_run(),
        scenario={},
        equivalents=["UAH"],
        per_eq_route={},
        clearing_volume_by_eq={"UAH": _TOO_PRECISE_FOR_FLOAT},
        per_eq_metric_values=values,
    )

    assert values["UAH"]["clearing_volume"] == _TOO_PRECISE_FOR_FLOAT


# --- T713: one metric key set on all four sides ------------------------------


async def test_metric_series_keys_agree_across_canon_pydantic_and_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canon, pydantic model and reader must describe the same set.

    Before T713 they did not: the canonical enum had five values, the pydantic
    Literal seven, and the reader emitted the same five as the canon, so
    ``active_participants``/``active_trustlines`` were measured and persisted but
    never reached a client.
    """

    _install_session(monkeypatch, lambda: _FakeSession([_FakeResult(rows=[])]))

    resp = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=0, step_ms=1_000
    )
    served = [item.key for item in resp.series]

    assert len(served) == len(set(served))
    assert sorted(served) == sorted(_canonical_metric_series_keys())
    assert sorted(served) == sorted(get_args(MetricSeriesKey))
    # Named explicitly so the assertions above cannot be satisfied by all three
    # sides shrinking back to the old five.
    assert {"active_participants", "active_trustlines"}.issubset(served)


async def test_real_mode_serves_persisted_network_cardinalities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two formerly persist-only series reach the response as counts."""

    _install_session(
        monkeypatch,
        lambda: _FakeSession(
            [
                _FakeResult(
                    rows=[
                        _metric_row("active_participants", 1_000, 4.0),
                        _metric_row("active_trustlines", 1_000, 6.0),
                        # An edge was frozen between the two ticks.
                        _metric_row("active_trustlines", 3_000, 5.0),
                    ]
                )
            ]
        ),
    )

    resp = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=3_000, step_ms=1_000
    )

    # Cardinalities, not amounts and not percentages.
    assert _series(resp, "active_participants").unit == "count"
    assert _series(resp, "active_trustlines").unit == "count"

    # T711 rule holds for the new series too: before the first measurement the
    # value is null, never an invented 0.0.
    assert _values(resp, "active_participants") == [
        None,
        "4.00000000",
        "4.00000000",
        "4.00000000",
    ]
    assert _values(resp, "active_trustlines") == [
        None,
        "6.00000000",
        "6.00000000",
        "5.00000000",
    ]


async def test_synthetic_mode_counts_cardinalities_instead_of_inventing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter-check: the synthetic path also produces the two new series.

    Without this the synthetic branch would fall through to the
    ``bottlenecks_score`` formula and label a percentage as a count.
    """

    _install_session(monkeypatch, lambda: _FakeSession([]))
    runtime = _build(mode="fixtures", db_enabled=False)
    runtime._scenarios["scn-1"] = SimpleNamespace(
        scenario_id="scn-1",
        raw={
            **_SCENARIO_RAW,
            "participants": [
                {"id": "alice"},
                {"id": "bob", "status": "active"},
                {"id": "carol", "status": "blocked"},
            ],
        },
    )
    runtime._runs["run-1"]._scenario_raw = runtime._scenarios["scn-1"].raw

    resp = await runtime.build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=1_000, step_ms=1_000
    )

    # alice (status omitted, defaults to active) + bob; carol is blocked.
    assert _values(resp, "active_participants") == ["2.00000000", "2.00000000"]
    # Two edges in the run's UAH edge cache.
    assert _values(resp, "active_trustlines") == ["2.00000000", "2.00000000"]


async def test_writer_and_reader_agree_on_the_key_set_end_to_end(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB-backed (sqlite): what the writer persists is what the reader serves."""

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)

    await simulator_storage.write_tick_metrics(
        run_id="run-1",
        t_ms=1_000,
        per_equivalent={
            "UAH": {"committed": 3, "rejected": 1, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={
            "UAH": {
                "avg_route_length": 2.0,
                "total_debt": Decimal("7"),
                "clearing_volume": Decimal("0"),
                "active_participants": 4.0,
                "active_trustlines": 6.0,
            }
        },
        session=db_session,
    )

    persisted_keys = {
        str(key)
        for (key,) in (
            await db_session.execute(
                select(SimulatorRunMetric.key)
                .where(SimulatorRunMetric.run_id == "run-1")
                .distinct()
            )
        ).all()
    }

    _install_session(monkeypatch, lambda: _SharedSession(db_session))

    resp = await _build().build_metrics(
        run_id="run-1", equivalent="UAH", from_ms=0, to_ms=1_000, step_ms=1_000
    )

    assert persisted_keys == {item.key for item in resp.series}
    assert _values(resp, "active_participants") == [None, "4.00000000"]
    assert _values(resp, "active_trustlines") == [None, "6.00000000"]


# --- T714: answering a GET must not write to the database --------------------


async def test_synthetic_bottlenecks_get_writes_nothing_end_to_end(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """DB-backed (sqlite): polling the endpoint no longer grows the table.

    The synthetic branch used to append one row per item on every request, with
    no deduplication, no row limit and a silent ``except Exception: pass``. The
    rows were unreachable: only the real-mode branch reads that table.
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    _install_session(monkeypatch, lambda: _SharedSession(db_session))
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)

    async def _row_count() -> int:
        return int(
            (
                await db_session.execute(
                    select(func.count()).select_from(SimulatorRunBottleneck)
                )
            ).scalar_one()
        )

    synthetic = _build(mode="fixtures", db_enabled=True)
    for _ in range(3):
        resp = await synthetic.build_bottlenecks(
            run_id="run-1", equivalent="UAH", limit=20, min_score=None
        )

    # The endpoint still answers with synthetic items; only the write is gone.
    assert {(i.target.from_, i.target.to) for i in resp.items} == {
        ("alice", "bob"),
        ("bob", "carol"),
    }
    assert await _row_count() == 0
    assert _warnings(caplog) == []

    # Counter-check: the table is writable through this very session, so the
    # zero above means "the GET wrote nothing", not "nothing can be written".
    await simulator_storage.write_tick_bottlenecks(
        run_id="run-1",
        equivalent="UAH",
        computed_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        edge_stats={
            ("alice", "bob"): {
                "attempts": 4,
                "committed": 2,
                "rejected": 0,
                "errors": 2,
                "timeouts": 0,
            }
        },
        session=db_session,
        commit=False,
    )
    assert await _row_count() == 1

    # And the real-mode reader serves exactly what the runner wrote.
    real = await _build().build_bottlenecks(
        run_id="run-1", equivalent="UAH", limit=20, min_score=None
    )
    assert [(i.target.from_, i.target.to, i.score) for i in real.items] == [
        ("alice", "bob", 0.5)
    ]
