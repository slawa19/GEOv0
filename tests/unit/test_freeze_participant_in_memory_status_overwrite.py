import logging

from app.core.simulator.models import RunRecord
from app.core.simulator.real_runner_impl import RealRunnerImpl


def test_freeze_participant_does_not_overwrite_non_active_trustline_status_in_scenario() -> None:
    runner = RealRunnerImpl.__new__(RealRunnerImpl)
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

    # 028 `F-028-29` (INTENTIONAL change): no line status is written any more - the participant is suspended and
    # its edges leave the planner's adjacency; until then the active lines became `frozen` here.
    tls = scenario["trustlines"]
    assert [tl.get("status") for tl in tls] == ["active", "deleted", None]
    assert scenario["participants"][0]["status"] == "suspended"
    assert run._edges_by_equivalent == {"EUR": [("A", "B")]}
