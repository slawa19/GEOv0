import logging
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.simulator import storage as simulator_storage
from app.db.models.simulator_storage import SimulatorRunMetric


@pytest.mark.asyncio
async def test_write_tick_metrics_bulk_upsert_updates_without_duplicates(
    db_session, monkeypatch
):
    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)

    await simulator_storage.write_tick_metrics(
        run_id="r1",
        t_ms=1000,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 1, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={
            "UAH": {
                "avg_route_length": 2.0,
                "total_debt": 10.0,
                "clearing_volume": 3.0,
            }
        },
        session=db_session,
    )

    rows_1 = (
        await db_session.execute(
            select(SimulatorRunMetric.key, SimulatorRunMetric.value).where(
                (SimulatorRunMetric.run_id == "r1")
                & (SimulatorRunMetric.equivalent_code == "UAH")
                & (SimulatorRunMetric.t_ms == 1000)
            )
        )
    ).all()
    assert len(rows_1) == 7
    assert {k for (k, _v) in rows_1} == {
        "success_rate",
        "bottlenecks_score",
        "avg_route_length",
        "total_debt",
        "clearing_volume",
        "active_participants",
        "active_trustlines",
    }

    # Write the same tick again with different values: must update (upsert), not duplicate.
    await simulator_storage.write_tick_metrics(
        run_id="r1",
        t_ms=1000,
        per_equivalent={
            "UAH": {"committed": 2, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={
            "UAH": {
                "avg_route_length": 5.0,
                "total_debt": 20.0,
                "clearing_volume": 6.0,
            }
        },
        session=db_session,
    )

    rows_2 = (
        await db_session.execute(
            select(SimulatorRunMetric.key, SimulatorRunMetric.value).where(
                (SimulatorRunMetric.run_id == "r1")
                & (SimulatorRunMetric.equivalent_code == "UAH")
                & (SimulatorRunMetric.t_ms == 1000)
            )
        )
    ).all()
    assert len(rows_2) == 7

    val_by_key = {k: float(v or 0.0) for (k, v) in rows_2}
    # committed=2, rejected=0 -> success_rate=100
    assert val_by_key["success_rate"] == 100.0
    assert val_by_key["avg_route_length"] == 5.0
    assert val_by_key["total_debt"] == 20.0
    assert val_by_key["clearing_volume"] == 6.0


# --- p007_t715: announced SQLite limitation and caller-session isolation -----


# 19 significant digits: binary64 holds ~17, so SQLite cannot store it exactly.
_TOO_PRECISE_FOR_FLOAT = Decimal("12345678901.12345678")


def _warnings(caplog) -> list:
    return [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "sqlite_money_metrics_are_not_exact" in record.getMessage()
    ]


@pytest.fixture(autouse=True)
def _reset_sqlite_money_warning_state():
    simulator_storage._SQLITE_MONEY_WARNED_RUN_IDS.clear()
    yield
    simulator_storage._SQLITE_MONEY_WARNED_RUN_IDS.clear()


@pytest.mark.asyncio
async def test_sqlite_money_metrics_are_lossy_and_say_so_once_per_run(
    db_session, monkeypatch, caplog
):
    """KNOWN LIMITATION, pinned as a limitation - not as correctness.

    SQLite has no native decimal, so `Numeric(20, 8)` round-trips through binary
    floating point. The value below comes back changed. That is documented in
    `docs/ru/simulator/backend/run-storage.md` and announced at WARNING level
    once per run; PostgreSQL is the only backend where the money series are
    exact (see `tests/integration/test_simulator_metrics_numeric_value_postgres.py`).

    If this test ever starts failing because the value survives intact, the
    limitation is gone: delete the warning, the docs paragraph and this test.
    """

    bind = db_session.get_bind()
    if bind.dialect.name != "sqlite":
        pytest.skip("This pins SQLite-specific behaviour")

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    caplog.set_level(logging.WARNING, logger=simulator_storage.logger.name)

    async def _write(t_ms: int) -> None:
        await simulator_storage.write_tick_metrics(
            run_id="r-money",
            t_ms=t_ms,
            per_equivalent={
                "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
            },
            metric_values_by_eq={"UAH": {"total_debt": _TOO_PRECISE_FOR_FLOAT}},
            session=db_session,
        )

    await _write(1_000)

    stored = (
        await db_session.execute(
            select(SimulatorRunMetric.value).where(
                (SimulatorRunMetric.run_id == "r-money")
                & (SimulatorRunMetric.key == "total_debt")
                & (SimulatorRunMetric.t_ms == 1_000)
            )
        )
    ).scalar_one()

    # The limitation itself: the money value did NOT survive.
    assert stored != _TOO_PRECISE_FOR_FLOAT
    assert isinstance(stored, Decimal)

    # It is announced, once, with the run id and the affected series.
    records = _warnings(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert "run_id=r-money" in message
    assert "keys=total_debt" in message
    assert "NOT exact" in message

    # A second tick of the same run must not re-flood the log.
    caplog.clear()
    await _write(2_000)
    assert _warnings(caplog) == []

    # Counter-check: a different run is announced again (the guard is per-run,
    # not a global once-per-process latch that would hide later runs).
    caplog.clear()
    await simulator_storage.write_tick_metrics(
        run_id="r-money-2",
        t_ms=1_000,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={"UAH": {"clearing_volume": _TOO_PRECISE_FOR_FLOAT}},
        session=db_session,
    )
    assert len(_warnings(caplog)) == 1
    assert "keys=clearing_volume" in _warnings(caplog)[0].getMessage()


@pytest.mark.asyncio
async def test_no_money_measurement_means_no_precision_warning(
    db_session, monkeypatch, caplog
):
    """Anti-vacuum: the warning fires on measured money, not on every write.

    A guard that fires unconditionally teaches the reader nothing and trains
    them to ignore it.
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    caplog.set_level(logging.WARNING, logger=simulator_storage.logger.name)

    await simulator_storage.write_tick_metrics(
        run_id="r-no-money",
        t_ms=1_000,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        # Both money series unmeasured this tick; the rest are still persisted.
        metric_values_by_eq={"UAH": {"avg_route_length": 2.0}},
        session=db_session,
    )

    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_failed_metrics_write_does_not_roll_back_the_caller(
    db_session, monkeypatch, caplog
):
    """A best-effort writer must not discard the caller's staged work.

    The real-mode tick hands its own session to `write_tick_metrics` with
    `commit=False`. Before p007_t715 any failure - and `Numeric(20, 8)` created
    a brand new one, `numeric field overflow` above 10^12 - triggered
    `await session.rollback()` on that session and then swallowed the error, so
    the tick silently lost everything it had staged. Sibling of finding
    B-A2b-001 (registry 008).
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    caplog.set_level(logging.ERROR, logger=simulator_storage.logger.name)

    # Work the caller staged in its own transaction, not yet committed.
    db_session.add(
        SimulatorRunMetric(
            run_id="r-caller",
            equivalent_code="UAH",
            key="total_debt",
            t_ms=500,
            value=Decimal("1.25"),
        )
    )
    await db_session.flush()

    # Now make our own write fail *inside* the caller's transaction, at the
    # database level: t_ms = -1 violates chk_simulator_run_metrics_t_ms, so the
    # INSERT really is executed and really is rejected. Nothing is stubbed out.
    await simulator_storage.write_tick_metrics(
        run_id="r-caller",
        t_ms=-1,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={"UAH": {"total_debt": Decimal("7")}},
        session=db_session,
        commit=False,
    )

    # Best-effort: the tick is not knocked over...
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "the failure must still be logged, not swallowed silently"
    )

    # ...and the caller's staged row is still there. This is the assertion the
    # old `await session.rollback()` could not satisfy.
    survived = (
        await db_session.execute(
            select(SimulatorRunMetric.value).where(
                (SimulatorRunMetric.run_id == "r-caller")
                & (SimulatorRunMetric.t_ms == 500)
            )
        )
    ).scalar_one()
    assert survived == Decimal("1.25")

    # And nothing from the failed write leaked in.
    leaked = (
        await db_session.execute(
            select(SimulatorRunMetric.key).where(
                (SimulatorRunMetric.run_id == "r-caller")
                & (SimulatorRunMetric.t_ms == -1)
            )
        )
    ).all()
    assert leaked == []


@pytest.mark.asyncio
async def test_failed_delegated_commit_leaves_the_session_usable(
    db_session, monkeypatch, caplog
):
    """`commit=True` delegates the commit to us, so we must not abandon it broken.

    The reachable caller is `real_tick_persistence.py`: it opens a session,
    hands it to `write_tick_metrics` without `commit=` (so the default
    `commit=True` applies) and then reuses that same session for
    `write_tick_bottlenecks`. If the delegated commit fails and the session is
    left in pending-rollback, the next write dies of `PendingRollbackError` -
    an error with no relationship to the real cause.

    This is the mirror image of the SAVEPOINT case, not a relapse of it: there
    the caller kept ownership (`commit=False`) and never asked us to end the
    transaction.
    """

    monkeypatch.setattr(settings, "SIMULATOR_DB_ENABLED", True, raising=False)
    caplog.set_level(logging.ERROR, logger=simulator_storage.logger.name)

    async def _failing_commit(*_args, **_kwargs):
        # Fail the way a real commit fails, not merely by raising: `commit()`
        # flushes first, and a failed flush is exactly what puts a SQLAlchemy
        # session into pending-rollback. Without reproducing that state the
        # test could not tell the fix from its absence.
        db_session.add(
            SimulatorRunMetric(
                run_id="r-delegated",
                equivalent_code="UAH",
                key="total_debt",
                t_ms=-1,  # violates chk_simulator_run_metrics_t_ms
                value=Decimal("1"),
            )
        )
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _failing_commit, raising=True)

    await simulator_storage.write_tick_metrics(
        run_id="r-delegated",
        t_ms=1_000,
        per_equivalent={
            "UAH": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        metric_values_by_eq={"UAH": {"total_debt": Decimal("7")}},
        session=db_session,
        # commit defaults to True: the delegation the caller actually performs.
    )

    # Best-effort, so the failure is reported rather than raised...
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]

    # ...and the subject of this test: the caller's very next statement - the
    # bottlenecks write, in production - still works. Nothing is rolled back
    # here on purpose; if the test had to repair the session itself, it would
    # prove nothing about the writer.
    await db_session.execute(select(SimulatorRunMetric.key).limit(1))
