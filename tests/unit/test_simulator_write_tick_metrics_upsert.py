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
    assert len(rows_1) == 5
    assert {k for (k, _v) in rows_1} == {
        "success_rate",
        "bottlenecks_score",
        "avg_route_length",
        "total_debt",
        "clearing_volume",
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
    assert len(rows_2) == 5

    val_by_key = {k: float(v or 0.0) for (k, v) in rows_2}
    # committed=2, rejected=0 -> success_rate=100
    assert val_by_key["success_rate"] == 100.0
    assert val_by_key["avg_route_length"] == 5.0
    assert val_by_key["total_debt"] == 20.0
    assert val_by_key["clearing_volume"] == 6.0
