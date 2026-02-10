import logging

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner import RealRunner


def test_freeze_participant_does_not_overwrite_non_active_trustline_status_in_scenario() -> None:
    runner = RealRunner.__new__(RealRunner)
    runner._logger = logging.getLogger(__name__)

    run = RunRecord(run_id="r1", scenario_id="s1", mode="real", state="running")
    run._edges_by_equivalent = {
        "EUR": [("FROZEN", "B"), ("FROZEN", "C"), ("A", "B")],
    }

    scenario = {
        "participants": [
            {"id": "FROZEN", "status": "active"},
            {"id": "B", "status": "active"},
            {"id": "C", "status": "active"},
        ],
        "trustlines": [
            {"from": "FROZEN", "to": "B", "equivalent": "EUR", "status": "active"},
            {"from": "FROZEN", "to": "C", "equivalent": "EUR", "status": "deleted"},
            # missing status should be treated as active
            {"from": "B", "to": "FROZEN", "equivalent": "EUR"},
        ],
    }

    runner._invalidate_caches_after_inject(
        run=run,
        scenario=scenario,
        affected_equivalents=set(),
        new_participants=[],
        new_participants_scenario=[],
        new_trustlines_scenario=[],
        frozen_pids=["FROZEN"],
    )

    tls = scenario["trustlines"]
    assert tls[0]["status"] == "frozen"
    assert tls[1]["status"] == "deleted"  # must not be overwritten
    assert tls[2]["status"] == "frozen"  # missing -> active -> frozen
