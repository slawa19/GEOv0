"""The tooling tier's CI binding, checked from the PowerShell partition (025 T2502.2, 2026-10-03).

The same check runs in the portable partition (`test_static_diagnostics_policy.py`, with its
counter-check). It runs here as well because a check inside the partition whose step was removed
runs nowhere: each partition's job notices the other partition's step going missing. Form only.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]


def test_both_tooling_partitions_are_blocking_ci_steps_seen_from_windows(tooling_ci_binding) -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    )
    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    assert tooling_ci_binding(workflow, runner) == []
