"""031 (BACKLOG item 18): a stored old scenario with the removed `inject_debt` op names why it does nothing.

WHAT IS WRONG (on `05d6e812`). 030 S3 removed the `inject_debt` effect, and the scenario schema refuses it on
upload. A `scenario.json` stored before that is still loaded (`ScenarioRegistry.load_uploaded_scenarios` ->
`scenario_to_record`, no schema check), and the inject executor counts the effect as `skipped` with no reason
(`inject_executor.py`, the fall-through after the known ops). No debt is written - no money harm - but the operator
sees an inject that "applied nothing" without being told why.

THE TARGET. The event's note names the op it does not support: `skipped_reasons` carries `unsupported_op:<op>`.
Control: a supported effect of the same event still applies.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

import pytest

from app.core.simulator.scenario_registry import ScenarioRegistry
from tests.integration.test_p021_trust_drift_is_audited_postgres import factory, world  # noqa: F401 - fixture
from tests.integration.test_p028_e3_freeze_boundary_postgres import _inject, _line


@pytest.mark.asyncio
async def test_a_stored_scenario_with_inject_debt_names_the_removed_op(factory, tmp_path) -> None:  # noqa: F811
    eq, p = await world(factory, ["A", "B", "C", "D"], [], [])
    removed = {"op": "inject_debt", "from": p["A"].pid, "to": p["B"].pid, "equivalent": eq.code, "amount": "5.00"}
    stored = {"scenario_id": "old-inject-debt", "equivalents": [eq.code], "participants": [], "trustlines": [],
              "events": [{"type": "inject", "time": 0, "effects": [removed, _line(eq, p["C"], p["D"])]}]}
    (tmp_path / "scenarios" / "old-inject-debt").mkdir(parents=True)
    (tmp_path / "scenarios" / "old-inject-debt" / "scenario.json").write_text(json.dumps(stored), encoding="utf-8")
    scenarios: dict = {}
    ScenarioRegistry(lock=threading.RLock(), scenarios=scenarios, fixtures_dir=tmp_path / "none",
                     schema_path=tmp_path / "none.json", local_state_dir=tmp_path,
                     utc_now=lambda: datetime.now(timezone.utc),
                     logger=logging.getLogger("tests.p031.registry")).load_uploaded_scenarios()
    # Premise: the stored scenario is loaded as it is, the removed op included.
    loaded_effects = scenarios["old-inject-debt"].raw["events"][0]["effects"]
    assert [e["op"] for e in loaded_effects] == ["inject_debt", "create_trustline"], loaded_effects

    runner, run, scenario, artifacts = _inject(eq, p, loaded_effects)
    async with factory() as s:
        await runner._apply_due_scenario_events(s, run_id=run.run_id, run=run, scenario=scenario)

    stats = [e["scenario"]["stats"] for e in artifacts.events if e.get("type") == "note"]
    assert stats and stats[-1]["applied"] == 1, stats  # control: the supported effect of the same event applied
    assert (stats[-1]["skipped"], stats[-1].get("skipped_reasons")) == (1, {"unsupported_op:inject_debt": 1}), (
        f"the removed op is skipped without naming it: {stats[-1]}"
    )
