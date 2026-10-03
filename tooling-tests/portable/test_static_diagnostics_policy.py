import copy
from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]


def test_ruff_blocks_ci_while_black_remains_diagnostic() -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = {
        step["name"]: step
        for step in workflow["jobs"]["static-diagnostics"]["steps"]
        if isinstance(step, dict) and "name" in step
    }

    ruff = steps["Ruff diagnostics"]
    black = steps["Black diagnostics"]

    assert ruff.get("continue-on-error") is not True
    assert ruff["run"] == "python -m ruff check app migrations --no-cache"
    assert black.get("continue-on-error") is True
    assert black["run"] == "python -m black --check app migrations"


# THE TOOLING TIER'S CI BINDING (025 T2502.2, 2026-10-03). The portable partition runs as a step of
# this job, next to the one step that is allowed to fail; the check lives in `tooling-tests/conftest.py`
# and is called from both partitions, so removing either step is noticed by the other one.
def _workflow_and_runner() -> tuple[dict, str]:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    )
    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    return workflow, runner


def test_both_tooling_partitions_are_blocking_ci_steps(tooling_ci_binding) -> None:
    workflow, runner = _workflow_and_runner()
    assert tooling_ci_binding(workflow, runner) == []


def _tooling_step(workflow: dict, job_id: str) -> dict:
    steps = [
        step
        for step in workflow["jobs"][job_id]["steps"]
        if "-ToolingOnly" in str(step.get("run", ""))
    ]
    assert len(steps) == 1, (job_id, steps)
    return steps[0]


def _drop_tooling_step(workflow: dict, job_id: str) -> None:
    job = workflow["jobs"][job_id]
    job["steps"] = [s for s in job["steps"] if "-ToolingOnly" not in str(s.get("run", ""))]


def test_the_binding_check_notices_each_way_around_it(tooling_ci_binding) -> None:
    """COUNTER-CHECK with the committed workflow and runner as its control (AGENTS.md §9)."""

    workflow, runner = _workflow_and_runner()
    assert tooling_ci_binding(workflow, runner) == []

    workflow_mutations = {
        "portable step removed": lambda w: _drop_tooling_step(w, "static-diagnostics"),
        "powershell step removed": lambda w: _drop_tooling_step(w, "required-ui"),
        "portable step allowed to fail like Black": lambda w: _tooling_step(
            w, "static-diagnostics"
        ).__setitem__("continue-on-error", True),
        "powershell step made schedule-only": lambda w: _tooling_step(w, "required-ui").__setitem__(
            "if", "github.event_name == 'schedule'"
        ),
        "whole job allowed to fail": lambda w: w["jobs"]["static-diagnostics"].__setitem__(
            "continue-on-error", True
        ),
        "partition renamed away": lambda w: _tooling_step(w, "static-diagnostics").__setitem__(
            "run", _tooling_step(w, "static-diagnostics")["run"].replace("portable", "all")
        ),
    }
    for name, mutate in workflow_mutations.items():
        mutated = copy.deepcopy(workflow)
        mutate(mutated)
        assert tooling_ci_binding(mutated, runner), f"not noticed: {name}"

    assert tooling_ci_binding(workflow, runner.replace("'--tooling-partition', ", ""))
    assert tooling_ci_binding(workflow, runner.replace("if ($runTooling) {", "if ($false) {"))
    assert tooling_ci_binding(workflow, runner.replace("'-q',", "'-q', '--noconftest',", 1))
