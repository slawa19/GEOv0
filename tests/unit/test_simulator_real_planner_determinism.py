import pytest

from app.core.simulator.runtime import RunRecord, SimulatorRuntime


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
    runtime = SimulatorRuntime()
    scenario = _scenario_minimal()

    run = RunRecord(
        run_id="r",
        scenario_id=scenario["scenario_id"],
        mode="real",
        state="running",
    )
    run.seed = 123456
    run.tick_index = 7

    run.intensity_percent = 50
    planned_a1 = runtime._plan_real_payments(run, scenario)
    planned_a2 = runtime._plan_real_payments(run, scenario)
    assert planned_a1 == planned_a2
    assert len(planned_a1) > 0

    run.intensity_percent = 100
    planned_b = runtime._plan_real_payments(run, scenario)
    assert planned_b[: len(planned_a1)] == planned_a1

    run.tick_index = 8
    planned_c = runtime._plan_real_payments(run, scenario)
    assert planned_c != planned_b
