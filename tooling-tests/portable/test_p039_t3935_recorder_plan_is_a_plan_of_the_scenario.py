"""The payment plan of `scripts/record_workbench_playlists.py` is a plan of the scenario it records (039 `T3935`).

The recorder drives a live run by a fixed plan of Interact payments (participant ordinals and amounts). A plan that
names a participant the scenario lacks, a suspended one, an amount finer than the precision, or a direct payment
without its trust line would make the run refuse a payment, and the recorder then writes nothing - so this is checked
before anyone starts a backend for it.

What this checks: `check_plan` finds no problem with the shipped scenario, and does find each kind of problem on a
mutated copy (anti-vacuum, AGENTS.md section 9). What it does NOT check: that the payments route as hoped (the engine
chooses the routes; the recorder refuses a multi-route answer) or anything about the money of a recording (that is the
consistency guard `simulator-ui/v2/src/composables/usePlaylistWorkbench.test.ts`).
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "record_workbench_playlists.py"


def _recorder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_workbench_playlists", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_plan_names_active_participants_and_the_lines_of_its_direct_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _recorder()
    scenario = json.loads(rec.SCENARIO_PATH.read_text(encoding="utf-8"))
    assert rec.check_plan(scenario) == []
    assert len(rec.PLAN) == 20 and rec.DIRECT, "the plan judged is the recording's: 20 payments, some direct"

    ids = [p["id"] for p in scenario["participants"]]
    a, b = rec.DIRECT[0]
    no_line = copy.deepcopy(scenario)
    no_line["trustlines"] = [t for t in no_line["trustlines"] if (t["from"], t["to"]) != (ids[b - 1], ids[a - 1])]
    assert any(f"no line {b}->{a}" in p for p in rec.check_plan(no_line))

    suspended = copy.deepcopy(scenario)
    suspended["participants"][rec.PLAN[0][0] - 1]["status"] = "frozen"
    assert any("is frozen" in p for p in rec.check_plan(suspended))

    fewer = copy.deepcopy(scenario)
    fewer["participants"] = fewer["participants"][:10]
    assert any("is not in the scenario" in p for p in rec.check_plan(fewer))

    # The kinds that depend on the plan itself: an amount finer than the precision, a direct payment not in the plan.
    monkeypatch.setattr(rec, "PLAN", ((rec.PLAN[0][0], rec.PLAN[0][1], "1.005"),) + tuple(rec.PLAN[1:]))
    assert any("is not at precision 2" in p for p in rec.check_plan(scenario))
    monkeypatch.setattr(rec, "PLAN", tuple(x for x in rec.PLAN if (x[0], x[1]) != rec.DIRECT[0]))
    assert any(f"DIRECT {a}->{b} is not a payment of the plan" in p for p in rec.check_plan(scenario))
