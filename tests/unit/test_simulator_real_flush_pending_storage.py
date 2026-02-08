import logging
import threading
from datetime import datetime, timezone

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _DummySession:
    async def rollback(self) -> None:  # pragma: no cover
        return


class _DummySessionCtx:
    async def __aenter__(self) -> _DummySession:
        return _DummySession()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


async def test_real_flush_pending_storage_writes_once(monkeypatch) -> None:
    # Arrange: runner with db enabled + a run containing cached last tick payload.
    run = RunRecord(
        run_id="r1",
        scenario_id="s1",
        mode="real",
        state="running",
    )
    run._real_last_tick_storage_payload = {
        "run_id": "r1",
        "tick_index": 2,
        "t_ms": 2000,
        "per_equivalent": {
            "HOUR": {"committed": 1, "rejected": 0, "errors": 0, "timeouts": 0}
        },
        "metric_values_by_eq": {"HOUR": {"avg_route_length": 1.0}},
        "bottlenecks": {
            "computed_at": _utc_now(),
            "equivalents": ["HOUR"],
            "edge_stats_by_eq": {"HOUR": {"top": []}},
        },
    }
    run._real_last_tick_storage_flushed_tick = -1

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: run,
        get_scenario_raw=lambda _scenario_id: {},
        sse=None,
        artifacts=None,
        utc_now=_utc_now,
        publish_run_status=lambda _run_id: None,
        db_enabled=lambda: True,
        actions_per_tick_max=20,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )

    calls: list[tuple[str, dict]] = []

    async def _write_tick_metrics(**kwargs):
        calls.append(("metrics", kwargs))

    async def _write_tick_bottlenecks(**kwargs):
        calls.append(("bottlenecks", kwargs))

    # Patch DB session factory and storage writers to avoid real DB.
    import app.core.simulator.real_runner as real_runner_mod

    monkeypatch.setattr(
        real_runner_mod.db_session, "AsyncSessionLocal", lambda: _DummySessionCtx()
    )
    monkeypatch.setattr(
        real_runner_mod.simulator_storage, "write_tick_metrics", _write_tick_metrics
    )
    monkeypatch.setattr(
        real_runner_mod.simulator_storage,
        "write_tick_bottlenecks",
        _write_tick_bottlenecks,
    )

    # Act: first flush should write; second flush should be deduplicated.
    await runner.flush_pending_storage("r1")
    await runner.flush_pending_storage("r1")

    # Assert
    assert run._real_last_tick_storage_flushed_tick == 2
    assert [c[0] for c in calls] == ["metrics", "bottlenecks"]
    assert calls[0][1]["run_id"] == "r1"
    assert calls[0][1]["t_ms"] == 2000
    assert calls[1][1]["equivalent"] == "HOUR"
