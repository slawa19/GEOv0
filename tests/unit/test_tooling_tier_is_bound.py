"""The tooling tier's CI binding, checked from the backend tier (025 T2502.2, 2026-10-03).

The third witness. Each tooling partition checks the other partition's CI step, so removing one
step is caught by the other one - but a pull request that removes BOTH steps would leave no tooling
session to notice. `required-backend` always runs this tier, so the same check runs here too.

It reuses `tooling_ci_binding_violations` from `tooling-tests/conftest.py`, loaded by path (the
directory name is not a package); that module has no import-time side effects for this reason. It
reads two files and opens no database. Form only, like its siblings: the job log shows the steps ran.
The counter-check of the function lives in `tooling-tests/portable/test_static_diagnostics_policy.py`.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]


def test_both_tooling_partitions_are_blocking_ci_steps_seen_from_the_backend_tier() -> None:
    spec = importlib.util.spec_from_file_location(
        "_tooling_tier_conftest", _ROOT / "tooling-tests" / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    )
    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    assert module.tooling_ci_binding_violations(workflow, runner) == []

    # §15 2026-10-03 (P1): `if: false` on a job was accepted. The witness itself must see it.
    for job_id, value in (("static-diagnostics", False), ("required-ui", "always()")):
        mutated = copy.deepcopy(workflow)
        mutated["jobs"][job_id]["if"] = value
        assert module.tooling_ci_binding_violations(mutated, runner), (job_id, value)
