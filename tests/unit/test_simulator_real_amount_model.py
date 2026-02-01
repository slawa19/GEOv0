import logging
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _runner() -> RealRunner:
    return RealRunner(
        lock=threading.RLock(),
        get_run=lambda _run_id: (_ for _ in ()).throw(AssertionError("get_run should not be called")),
        get_scenario_raw=lambda _scenario_id: (_ for _ in ()).throw(AssertionError("get_scenario_raw should not be called")),
        sse=None,  # not used by _plan_real_payments
        artifacts=None,  # not used by _plan_real_payments
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


def test_real_amount_cap_default_is_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIMULATOR_REAL_AMOUNT_CAP", raising=False)

    runner = _runner()
    scenario = {
        "scenario_id": "s",
        "equivalents": ["UAH"],
        "participants": [
            {"id": "A", "type": "person", "groupId": "households", "behaviorProfileId": "household"},
            {"id": "B", "type": "person", "groupId": "retail", "behaviorProfileId": "retail"},
        ],
        "behaviorProfiles": [
            {"id": "household", "props": {"tx_rate": 1.0, "equivalent_weights": {"UAH": 1.0}}},
            {"id": "retail", "props": {"tx_rate": 1.0, "equivalent_weights": {"UAH": 1.0}}},
        ],
        # creditor->debtor: B gives A a 10 UAH limit (payment direction A->B)
        "trustlines": [{"equivalent": "UAH", "from": "B", "to": "A", "limit": "10", "status": "active"}],
    }

    run = RunRecord(run_id="r", scenario_id="s", mode="real", state="running")
    run.seed = 1
    run.tick_index = 1
    run.intensity_percent = 100

    planned = runner._plan_real_payments(run, scenario)
    assert planned
    for a in planned:
        assert Decimal(a.amount) <= Decimal("3.00")


def test_real_amount_model_is_respected_with_env_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULATOR_REAL_AMOUNT_CAP", "500")

    runner = _runner()
    scenario = {
        "scenario_id": "s",
        "equivalents": ["UAH"],
        "participants": [
            {"id": "A", "type": "person", "groupId": "households", "behaviorProfileId": "household"},
            {"id": "B", "type": "person", "groupId": "retail", "behaviorProfileId": "retail"},
        ],
        "behaviorProfiles": [
            {
                "id": "household",
                "props": {
                    "tx_rate": 1.0,
                    "equivalent_weights": {"UAH": 1.0},
                    "amount_model": {"UAH": {"min": 20, "max": 200, "p50": 100}},
                },
            },
            {"id": "retail", "props": {"tx_rate": 1.0, "equivalent_weights": {"UAH": 1.0}}},
        ],
        # creditor->debtor: B gives A a big limit so amount_model bounds are visible
        "trustlines": [{"equivalent": "UAH", "from": "B", "to": "A", "limit": "1000", "status": "active"}],
    }

    run = RunRecord(run_id="r", scenario_id="s", mode="real", state="running")
    run.seed = 2
    run.tick_index = 5
    run.intensity_percent = 100

    planned = runner._plan_real_payments(run, scenario)
    assert planned

    for a in planned:
        amt = Decimal(a.amount)
        assert amt >= Decimal("20.00")
        assert amt <= Decimal("200.00")

    # Prefix-stability check: lower intensity yields a prefix of the higher-intensity planned list.
    run2 = RunRecord(run_id="r", scenario_id="s", mode="real", state="running")
    run2.seed = run.seed
    run2.tick_index = run.tick_index
    run2.intensity_percent = 40

    planned_low = runner._plan_real_payments(run2, scenario)
    assert planned[: len(planned_low)] == planned_low
