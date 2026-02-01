import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _scenario_minimal() -> dict:
    return {
        "scenario_id": "unit-test-scenario",
        "equivalents": ["HOUR"],
        "participants": [
            {"participant_id": "A"},
            {"participant_id": "B"},
        ],
        # TrustLine direction is creditor->debtor, so payments go debtor->creditor.
        "trustlines": [
            {"equivalent": "HOUR", "from": "A", "to": "B", "limit": "10", "status": "active"},
            {"equivalent": "HOUR", "from": "B", "to": "A", "limit": "10", "status": "active"},
        ],
    }


def test_real_planner_is_deterministic_and_prefix_stable() -> None:
    scenario = _scenario_minimal()

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: (_ for _ in ()).throw(AssertionError("get_run should not be called")),
        get_scenario_raw=lambda _scenario_id: (_ for _ in ()).throw(AssertionError("get_scenario_raw should not be called")),
        sse=None,  # not used by _plan_real_payments
        artifacts=None,  # not used by _plan_real_payments
        utc_now=_utc_now,
        publish_run_status=lambda _run_id: None,
        db_enabled=lambda: False,
        actions_per_tick_max=20,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )

    run = RunRecord(
        run_id="r",
        scenario_id=scenario["scenario_id"],
        mode="real",
        state="running",
    )
    run.seed = 123456
    run.tick_index = 7

    run.intensity_percent = 50
    planned_a1 = runner._plan_real_payments(run, scenario)
    planned_a2 = runner._plan_real_payments(run, scenario)
    assert planned_a1 == planned_a2
    assert len(planned_a1) > 0

    run.intensity_percent = 100
    planned_b = runner._plan_real_payments(run, scenario)
    assert planned_b[: len(planned_a1)] == planned_a1

    run.tick_index = 8
    planned_c = runner._plan_real_payments(run, scenario)
    assert planned_c != planned_b


def test_real_planner_seq_is_contiguous_per_tick() -> None:
    scenario = _scenario_minimal()

    runner = RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: (_ for _ in ()).throw(AssertionError("get_run should not be called")),
        get_scenario_raw=lambda _scenario_id: (_ for _ in ()).throw(AssertionError("get_scenario_raw should not be called")),
        sse=None,  # not used by _plan_real_payments
        artifacts=None,  # not used by _plan_real_payments
        utc_now=_utc_now,
        publish_run_status=lambda _run_id: None,
        db_enabled=lambda: False,
        actions_per_tick_max=30,
        clearing_every_n_ticks=25,
        real_max_consec_tick_failures_default=3,
        real_max_timeouts_per_tick_default=3,
        real_max_errors_total_default=10,
        logger=logging.getLogger(__name__),
    )

    run = RunRecord(
        run_id="r",
        scenario_id=scenario["scenario_id"],
        mode="real",
        state="running",
    )
    run.seed = 123456
    run.tick_index = 7
    run.intensity_percent = 80

    planned = runner._plan_real_payments(run, scenario)
    assert len(planned) > 0
    assert [a.seq for a in planned] == list(range(len(planned)))
