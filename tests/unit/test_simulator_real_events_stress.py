import logging
import threading
from datetime import datetime, timezone

from app.core.simulator.real_runner import RealRunner


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _runner() -> RealRunner:
    return RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: (_ for _ in ()).throw(AssertionError("get_run should not be called")),
        get_scenario_raw=lambda _scenario_id: (_ for _ in ()).throw(AssertionError("get_scenario_raw should not be called")),
        sse=None,  # not used
        artifacts=None,  # not used
        utc_now=_utc_now,
        publish_run_status=lambda _run_id: None,
        db_enabled=lambda: False,
        actions_per_tick_max=50,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )


def test_stress_multipliers_by_scope() -> None:
    runner = _runner()

    events = [
        {
            "time": 1000,
            "type": "stress",
            "effects": [{"op": "mult", "field": "tx_rate", "value": 2.0, "scope": "all"}],
            "metadata": {"duration_ms": 2000},
        },
        {
            "time": 1500,
            "type": "stress",
            "effects": [{"op": "mult", "field": "tx_rate", "value": 0.5, "scope": "group:retail"}],
            "metadata": {"duration_ms": 1000},
        },
        {
            "time": 1500,
            "type": "stress",
            "effects": [{"op": "mult", "field": "tx_rate", "value": 3.0, "scope": "profile:household"}],
            "metadata": {"duration_ms": 1000},
        },
    ]

    mult_all, by_group, by_profile = runner._compute_stress_multipliers(events=events, sim_time_ms=1600)

    assert mult_all == 2.0
    assert by_group["retail"] == 0.5
    assert by_profile["household"] == 3.0


def test_stress_outside_window_is_ignored() -> None:
    runner = _runner()
    events = [
        {
            "time": 1000,
            "type": "stress",
            "effects": [{"op": "mult", "field": "tx_rate", "value": 2.0, "scope": "all"}],
            "metadata": {"duration_ms": 500},
        }
    ]

    mult_all, by_group, by_profile = runner._compute_stress_multipliers(events=events, sim_time_ms=2000)

    assert mult_all == 1.0
    assert by_group == {}
    assert by_profile == {}
