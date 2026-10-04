"""028 F-028-41 (owner В-3): run scripts sum cleared money in Decimal within the run's equivalent.

Before: float over every `clearing.done`, and the first equivalent of the integrity report.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EVENTS = [
    {"type": "clearing.done", "equivalent": "UAH", "cleared_amount": "0.10", "cleared_cycles": 1},
    {"type": "clearing.done", "equivalent": "HOUR", "cleared_amount": "5", "cleared_cycles": 1},
    {"type": "clearing.done", "equivalent": "UAH", "cleared_amount": "0.20", "cleared_cycles": 1},
]


def _load(name: str):
    module_name = f"_p028_e5_{name}"
    spec = importlib.util.spec_from_file_location(module_name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def test_reference_run_sums_only_its_equivalent_exactly(tmp_path):
    events = tmp_path / "events.ndjson"
    events.write_text("\n".join(json.dumps(e) for e in _EVENTS), encoding="utf-8")

    result = _load("run_reference_simulator_run")._analyze_events(events, equivalent="UAH")

    # 0.1 + 0.2 in float is 0.30000000000000004, and HOUR would have made it 5.3.
    assert (result["cleared_amount_total"], result["cleared_amount_equivalent"]) == ("0.30", "UAH")
    assert result["clearing_done"] == 3


def test_reference_run_summarises_its_own_equivalent_of_the_integrity_report():
    details = {
        "equivalents": {
            "HOUR": {"invariants": {"trust_limits": {"details": {"violations": [{"violation_amount": "9"}]}}}},
            "UAH": {"invariants": {"trust_limits": {"details": {"violations": [{"violation_amount": "0.01"}]}}}},
        }
    }

    summary = _load("run_reference_simulator_run")._summarize_trust_limits_violations(details, equivalent="UAH")

    assert (summary["violations"], summary["max_violation_amount"]) == (1, "0.01")


def test_demo_run_sums_only_its_equivalent_exactly():
    raw = "\n".join(json.dumps({**e, "ts": "2026-10-04T00:00:01+00:00"}) for e in _EVENTS).encode("utf-8")

    result = _load("run_clearing_demo10_100ticks")._analyze_events_ndjson(
        raw, started_at=datetime(2026, 10, 4, tzinfo=timezone.utc), equivalent="UAH"
    )

    clearing = result["clearing"]
    assert (clearing["cleared_amount_total"], clearing["cleared_amount_equivalent"]) == ("0.30", "UAH")
