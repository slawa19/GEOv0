"""PostgreSQL evidence for spec 007 / T715: metric values are exact decimals.

Prerequisites: a disposable PostgreSQL test database
(``TEST_DATABASE_URL=postgresql+asyncpg://.../geov0_test_*``) and
``GEO_TEST_ALLOW_DB_RESET=1``. SQLite cannot carry this evidence: its Numeric
support round-trips through binary floating point, which is exactly the
narrowing this slice removes.

Covered:

* ``simulator_run_metrics.value`` really is ``numeric(20, 8)`` in the database;
* a money amount no float can represent survives writer -> column -> reader ->
  wire without changing;
* ``null`` ("not measured") is still distinguishable from a measured zero;
* the static clearing coordinator carries the cleared volume to the column
  without narrowing it either (until 2026-09-28 the same walk also ran on the
  adaptive policy, removed by programme 021 stage 3).
"""

from __future__ import annotations

import logging
import threading
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, text

import app.db.session as db_session_module
from app.config import settings
from app.core.simulator import storage as simulator_storage
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.db.models.simulator_storage import SimulatorRunMetric
from tests.p019_support import require_target
from tests.simulator_tick_stand import unit_tick



# 19 significant digits. binary64 holds ~17, so any float stage changes it.
_TOO_PRECISE_FOR_FLOAT = Decimal("12345678901.12345678")

_RUN_ID = "t715-pg-run"
_SCENARIO_RAW: dict[str, Any] = {"equivalent": "UAH", "participants": []}


class _SharedSession:
    """Hands the reader the already-open test session."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


def _reader() -> MetricsBottlenecks:
    run = SimpleNamespace(
        run_id=_RUN_ID,
        scenario_id="scn-1",
        mode="real",
        state="running",
        sim_time_ms=2_000,
        intensity_percent=50,
        _edges_by_equivalent={"UAH": []},
        _scenario_raw=_SCENARIO_RAW,
    )
    return MetricsBottlenecks(
        lock=threading.RLock(),
        runs={_RUN_ID: run},
        scenarios={"scn-1": SimpleNamespace(scenario_id="scn-1", raw=_SCENARIO_RAW)},
        utc_now=lambda: None,
        db_enabled=lambda: True,
        logger=logging.getLogger("tests.simulator.t715"),
    )


def _values(response: Any, key: str) -> list[Any]:
    series = next(item for item in response.series if item.key == key)
    return [point.v for point in series.points]


def test_probe_value_is_beyond_float() -> None:
    """Anti-vacuum: the discriminator must actually discriminate."""

    assert Decimal(str(float(_TOO_PRECISE_FOR_FLOAT))) != _TOO_PRECISE_FOR_FLOAT


async def test_metric_value_column_is_numeric_20_8(db_session: Any) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT data_type, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'simulator_run_metrics' AND column_name = 'value'"
            )
        )
    ).one()

    assert row[0] == "numeric"
    assert (int(row[1]), int(row[2])) == (20, 8)


async def test_money_metric_survives_the_whole_chain_exactly(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)

    await simulator_storage.write_tick_metrics(
        run_id=_RUN_ID,
        t_ms=1_000,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={
            "UAH": {
                "total_debt": _TOO_PRECISE_FOR_FLOAT,
                # A measured zero, not a missing measurement.
                "clearing_volume": Decimal("0"),
                # avg_route_length absent: not measured this tick.
            }
        },
        session=db_session,
    )

    stored = {
        str(key): value
        for (key, value) in (
            await db_session.execute(
                select(SimulatorRunMetric.key, SimulatorRunMetric.value).where(
                    (SimulatorRunMetric.run_id == _RUN_ID)
                    & (SimulatorRunMetric.t_ms == 1_000)
                )
            )
        ).all()
    }

    assert stored["total_debt"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(stored["total_debt"], Decimal)
    assert stored["clearing_volume"] == Decimal("0")
    # "Not measured" is still NULL and still different from the measured zero.
    assert stored["avg_route_length"] is None

    monkeypatch.setattr(
        db_session_module,
        "AsyncSessionLocal",
        lambda: _SharedSession(db_session),
        raising=False,
    )

    resp = await _reader().build_metrics(
        run_id=_RUN_ID, equivalent="UAH", from_ms=1_000, to_ms=1_000, step_ms=1_000
    )

    # Decimal string on the wire, plain notation, digits intact.
    assert _values(resp, "total_debt") == ["12345678901.12345678"]
    assert _values(resp, "clearing_volume") == ["0.00000000"]
    assert _values(resp, "avg_route_length") == [None]

    wire = resp.model_dump(mode="json")
    wire_debt = next(s for s in wire["series"] if s["key"] == "total_debt")["points"]
    assert [point["v"] for point in wire_debt] == ["12345678901.12345678"]


# --- static clearing: the cleared volume reaches the column exactly ----------


async def test_static_clearing_volume_reaches_the_column_exactly(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Static form of `test_adaptive_clearing_volume_reaches_the_column_exactly` (021 stage 3, `T2104`).

    The same end-to-end walk - coordinator -> tick metrics producer -> writer -> numeric(20, 8) column ->
    reader -> wire - on the static branch, the only one left once the adaptive mode is removed. The static
    coordinator runs the clearing as a task under its hard timeout (`_execute_clearing_with_timeout`), so this
    also covers that the committed volume is handed back unchanged. Since 021 stage 4 the coordinator and the
    metrics producer are `RealTick` (`tick.py`), and the volume is what the clearing reported as committed at
    the tick's one call point (`RealTick._run_clearing`), not the clearing task's return value.
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)

    coordinator = unit_tick(
        _logger=logging.getLogger("tests.simulator.t715.static"),
        _clearing_every_n_ticks=1,
        _real_clearing_time_budget_ms=250,
        _real_db_metrics_every_n_ticks=5,
    )

    run = SimpleNamespace(
        run_id=_RUN_ID,
        tick_index=1,
        sim_time_ms=3_000,
        queue_depth=0,
        current_phase=None,
        _real_in_flight=0,
        _real_clearing_task=None,
        _edges_by_equivalent={"UAH": []},
        _real_total_debt_by_eq={},
        _real_total_debt_tick=0,
    )

    async def _run_clearing(*, committed, **_kwargs) -> None:
        committed["UAH"] += _TOO_PRECISE_FOR_FLOAT

    coordinator._run_clearing = _run_clearing

    clearing_volume_by_eq = await coordinator.maybe_run_clearing(
        session=db_session,
        run_id=_RUN_ID,
        run=run,
        equivalents=["UAH"],
        planned_len=0,
        tick_t0=0.0,
        payments_result=None,
    )

    # Stage 1: out of the coordinator, and the clearing task is not left behind.
    assert clearing_volume_by_eq["UAH"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(clearing_volume_by_eq["UAH"], Decimal)
    assert run._real_clearing_task is None

    # Stage 2: through the tick metrics producer (tick 1 is off the total_debt throttle of 5).
    per_eq_metric_values: dict[str, dict[str, Any]] = {"UAH": {}}
    await coordinator.populate_per_eq_metric_values(
        session=db_session,
        run=run,
        scenario={"participants": []},
        equivalents=["UAH"],
        per_eq_route={},
        clearing_volume_by_eq=clearing_volume_by_eq,
        per_eq_metric_values=per_eq_metric_values,
    )
    assert per_eq_metric_values["UAH"]["clearing_volume"] == _TOO_PRECISE_FOR_FLOAT
    assert "total_debt" not in per_eq_metric_values["UAH"]

    # Stage 3: writer -> numeric(20, 8) column.
    await simulator_storage.write_tick_metrics(
        run_id=_RUN_ID,
        t_ms=3_000,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq=per_eq_metric_values,
        session=db_session,
    )

    stored = (
        await db_session.execute(
            select(SimulatorRunMetric.value).where(
                (SimulatorRunMetric.run_id == _RUN_ID)
                & (SimulatorRunMetric.key == "clearing_volume")
                & (SimulatorRunMetric.t_ms == 3_000)
            )
        )
    ).scalar_one()
    assert stored == _TOO_PRECISE_FOR_FLOAT

    # Stage 4: reader -> wire.
    monkeypatch.setattr(
        db_session_module,
        "AsyncSessionLocal",
        lambda: _SharedSession(db_session),
        raising=False,
    )
    resp = await _reader().build_metrics(
        run_id=_RUN_ID, equivalent="UAH", from_ms=3_000, to_ms=3_000, step_ms=1_000
    )
    assert _values(resp, "clearing_volume") == ["12345678901.12345678"]


# --- 028 E1 / F-028-7: an unrepresentable value costs its own point, not the tick --------------------------------

# Two debts at the money door's ceiling sum past what Numeric(20, 8) holds (BACKLOG 007 §1).
_EPISODE = [(1_000, Decimal("999999999999.99999999")), (2_000, Decimal(10) ** 12), (3_000, Decimal("500"))]
_OTHERS = {"avg_route_length": Decimal("2"), "clearing_volume": Decimal("3"),
           "active_participants": Decimal("4"), "active_trustlines": Decimal("5")}


async def test_unrepresentable_total_debt_is_a_gap_not_a_lost_tick(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Replaces `test_overflow_rolls_back_only_the_metrics_write` (p007_t715), whose overflow now no longer fails
    the write: the caller's staged row still survives, and the tick above the ceiling keeps its other six keys
    while `total_debt` is stored as NULL and read back as `null` - not as a repeat of the previous point. The
    savepoint for a write the database does reject stays covered by
    `tests/unit/test_simulator_write_tick_metrics_upsert.py::test_failed_metrics_write_does_not_roll_back_the_caller`."""

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    caplog.set_level(logging.WARNING, logger=simulator_storage.logger.name)
    db_session.add(SimulatorRunMetric(run_id=_RUN_ID, equivalent_code="UAH", key="total_debt", t_ms=100,
                                      value=Decimal("1.25")))  # work the caller staged in its own transaction
    await db_session.flush()
    written = [await simulator_storage.write_tick_metrics(
        run_id=_RUN_ID, t_ms=t_ms, per_equivalent={"UAH": {"committed": 1}},
        metric_values_by_eq={"UAH": {"total_debt": debt, **_OTHERS}}, session=db_session, commit=False)
        for t_ms, debt in _EPISODE]

    rows = (await db_session.execute(select(SimulatorRunMetric.t_ms, SimulatorRunMetric.key, SimulatorRunMetric.value)
                                     .where(SimulatorRunMetric.run_id == _RUN_ID))).all()
    nulls = sorted((t, k) for t, k, v in rows if v is None)
    per_tick = {t: sum(1 for row in rows if row[0] == t) for t, _ in _EPISODE}
    assert (100, "total_debt", Decimal("1.25")) in rows, "the caller's staged row survived"
    logged = any("unrepresentable" in r.getMessage() for r in caplog.records)  # the dropped point is not silent

    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", lambda: _SharedSession(db_session), raising=False)
    resp = await _reader().build_metrics(run_id=_RUN_ID, equivalent="UAH", from_ms=1_000, to_ms=3_000, step_ms=1_000)
    assert _values(resp, "avg_route_length") == ["2.00000000"] * 3
    require_target(
        written == [True] * 3 and logged and per_tick == {1_000: 7, 2_000: 7, 3_000: 7} and nulls == [(2_000, "total_debt")]
        and _values(resp, "total_debt") == ["999999999999.99999999", None, "500.00000000"],
        f"written {written}, logged {logged}, rows per tick {per_tick}, NULLs {nulls}, total_debt read {_values(resp, 'total_debt')}")
