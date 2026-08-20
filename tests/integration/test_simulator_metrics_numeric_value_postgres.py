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
* the **adaptive** clearing policy - the non-default branch - carries the cleared
  volume to the column without narrowing it either.
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
from app.core.simulator.adaptive_clearing_policy import AdaptiveClearingPolicyConfig
from app.core.simulator.metrics_bottlenecks import MetricsBottlenecks
from app.core.simulator.real_tick_clearing_coordinator import RealTickClearingCoordinator
from app.core.simulator.real_tick_metrics import RealTickMetrics
from app.db.models.simulator_storage import SimulatorRunMetric


pytestmark = pytest.mark.postgres


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


# --- adaptive clearing policy: the non-default path must be exact too --------


async def test_adaptive_clearing_volume_reaches_the_column_exactly(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adaptive policy path narrowed the cleared volume through float.

    The static policy is the default, so covering only it would leave
    `B-D1-002` closed on one branch and open on the other. This walks the
    adaptive branch end to end: coordinator -> tick metrics producer -> writer
    -> numeric(20, 8) column -> reader -> wire.
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)

    config = AdaptiveClearingPolicyConfig(
        window_ticks=5,
        # Cold-start fallback on every tick: makes the decision deterministic
        # without having to prime the rolling window.
        warmup_fallback_cadence=1,
        min_interval_ticks=1,
        inflight_threshold=0,
        queue_depth_threshold=0,
    )
    coordinator = RealTickClearingCoordinator(
        lock=threading.RLock(),
        logger=logging.getLogger("tests.simulator.t715.adaptive"),
        clearing_every_n_ticks=1,
        real_clearing_time_budget_ms=250,
        clearing_policy="adaptive",
        adaptive_config=config,
    )
    assert coordinator._adaptive_policy is not None  # the adaptive branch is live

    run = SimpleNamespace(
        run_id=_RUN_ID,
        tick_index=1,
        sim_time_ms=2_000,
        queue_depth=0,
        _real_in_flight=0,
        _edges_by_equivalent={"UAH": []},
        _real_total_debt_by_eq={},
        _real_total_debt_tick=0,
    )

    async def _run_clearing_for_eq(eq: str, **_kwargs: Any) -> dict[str, Decimal]:
        return {str(eq): _TOO_PRECISE_FOR_FLOAT}

    async def _unexpected_static_clearing() -> dict[str, Decimal]:
        raise AssertionError("the static clearing runner must not be used here")

    clearing_volume_by_eq = await coordinator.maybe_run_clearing(
        session=db_session,
        run_id=_RUN_ID,
        run=run,
        equivalents=["UAH"],
        planned_len=0,
        tick_t0=0.0,
        clearing_enabled=True,
        safe_int_env=lambda _name, default: default,
        run_clearing=_unexpected_static_clearing,
        run_clearing_for_eq=_run_clearing_for_eq,
        payments_result=None,
    )

    # Stage 1: out of the coordinator.
    assert clearing_volume_by_eq["UAH"] == _TOO_PRECISE_FOR_FLOAT
    assert isinstance(clearing_volume_by_eq["UAH"], Decimal)

    # Stage 2: through the tick metrics producer. `real_db_metrics_every_n_ticks`
    # is 5 and the tick index is 1, so the throttled total_debt snapshot is
    # skipped and this tick measures only the clearing volume.
    per_eq_metric_values: dict[str, dict[str, Any]] = {"UAH": {}}
    await RealTickMetrics(
        lock=threading.RLock(),
        logger=logging.getLogger("tests.simulator.t715.adaptive"),
        real_db_metrics_every_n_ticks=5,
    ).populate_per_eq_metric_values(
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
        t_ms=2_000,
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
                & (SimulatorRunMetric.t_ms == 2_000)
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
        run_id=_RUN_ID, equivalent="UAH", from_ms=2_000, to_ms=2_000, step_ms=1_000
    )
    assert _values(resp, "clearing_volume") == ["12345678901.12345678"]


# --- p007_t715: the new overflow class must not damage the caller ------------


# Numeric(20, 8) holds 12 integer digits. float8 accepted this yesterday.
_ABOVE_THE_NUMERIC_CEILING = Decimal("1E+13")


async def test_overflow_rolls_back_only_the_metrics_write(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`numeric field overflow` is a failure class this slice introduced.

    The real-mode tick passes its own session with `commit=False`. Before the
    SAVEPOINT the writer answered any failure with `await session.rollback()`
    on that session and then swallowed it, so a run above the ceiling silently
    threw away everything the tick had staged, every tick, while the reader
    carried the last good measurement forward as a healthy flat line. Sibling of
    finding B-A2b-001 (registry 008).
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    caplog.set_level(logging.ERROR, logger=simulator_storage.logger.name)

    # Work the caller staged in its own transaction.
    db_session.add(
        SimulatorRunMetric(
            run_id=_RUN_ID,
            equivalent_code="UAH",
            key="total_debt",
            t_ms=100,
            value=Decimal("1.25"),
        )
    )
    await db_session.flush()

    await simulator_storage.write_tick_metrics(
        run_id=_RUN_ID,
        t_ms=200,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={"UAH": {"total_debt": _ABOVE_THE_NUMERIC_CEILING}},
        session=db_session,
        commit=False,
    )

    # The overflow really happened and was reported, not swallowed in silence.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "the overflow must reach the log"
    assert any("write_tick_metrics_failed" in r.getMessage() for r in errors)

    # The caller's staged row survived - this is what the old rollback destroyed.
    survived = (
        await db_session.execute(
            select(SimulatorRunMetric.value).where(
                (SimulatorRunMetric.run_id == _RUN_ID)
                & (SimulatorRunMetric.t_ms == 100)
            )
        )
    ).scalar_one()
    assert survived == Decimal("1.25")

    # Nothing from the failed batch leaked in.
    leaked = (
        await db_session.execute(
            select(SimulatorRunMetric.key).where(
                (SimulatorRunMetric.run_id == _RUN_ID)
                & (SimulatorRunMetric.t_ms == 200)
            )
        )
    ).all()
    assert leaked == []

    # And the session is still usable afterwards: the caller can keep working.
    await db_session.execute(select(SimulatorRunMetric.key).limit(1))
