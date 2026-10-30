import copy
from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]


def _static_steps(workflow: dict) -> dict:
    return {
        step["name"]: step
        for step in workflow["jobs"]["static-diagnostics"]["steps"]
        if isinstance(step, dict) and "name" in step
    }


def static_policy_violations(workflow: dict) -> list[str]:
    """Ruff and Black both gate `static-diagnostics` (Black since 2026-10-30, 054 T5411: one mechanical
    reformat of app/, ratchet closed at zero). Black's area is app/ only: applied migrations are read-only by
    AGENTS.md §3 and excluded in pyproject. A step is blocking only if it is present, carries no `if:` and no
    `continue-on-error` AT ALL (an expression such as `${{ true }}` loads as a string, so `is not True` would
    pass it - Codex review of PR #378) and runs exactly the pinned command. This guard checks the workflow's
    shape, not that the tree is clean - the job does."""
    steps = _static_steps(workflow)
    expected = {
        "Ruff diagnostics": "python -m ruff check app migrations --no-cache",
        "Black diagnostics": "python -m black --check app",
    }
    violations: list[str] = []
    for name, run in expected.items():
        step = steps.get(name)
        if step is None:
            violations.append(f"step {name!r} is missing")
            continue
        if "if" in step:
            violations.append(f"step {name!r} carries if:")
        if "continue-on-error" in step:
            violations.append(f"step {name!r} carries continue-on-error")
        if step.get("run") != run:
            violations.append(f"step {name!r} runs {step.get('run')!r}, not {run!r}")
    job = workflow["jobs"]["static-diagnostics"]
    if "if" in job or "continue-on-error" in job:
        violations.append("the static-diagnostics job carries if: or continue-on-error")
    return violations


def test_ruff_and_black_block_ci() -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
    )
    assert static_policy_violations(workflow) == []


def test_the_static_policy_check_notices_each_way_around_it() -> None:
    """COUNTER-CHECK with the committed workflow as its control (AGENTS.md §9): every way of taking
    Black or Ruff out of the gate that keeps the step's text plausible must be a violation."""

    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    )
    assert static_policy_violations(workflow) == []

    def black(w: dict) -> dict:
        return _static_steps(w)["Black diagnostics"]

    mutations = {
        "Black allowed to fail": lambda w: black(w).__setitem__("continue-on-error", True),
        "Black allowed to fail by expression": lambda w: black(w).__setitem__(
            "continue-on-error", "${{ true }}"
        ),
        "Black skipped by condition": lambda w: black(w).__setitem__("if", "${{ false }}"),
        "Black area widened to migrations": lambda w: black(w).__setitem__(
            "run", "python -m black --check app migrations"
        ),
        "Black step removed": lambda w: w["jobs"]["static-diagnostics"].__setitem__(
            "steps", [s for s in w["jobs"]["static-diagnostics"]["steps"] if s.get("name") != "Black diagnostics"]
        ),
        "Ruff allowed to fail": lambda w: _static_steps(w)["Ruff diagnostics"].__setitem__(
            "continue-on-error", True
        ),
        "whole job skipped": lambda w: w["jobs"]["static-diagnostics"].__setitem__("if", "false"),
    }
    for label, mutate in mutations.items():
        mutant = copy.deepcopy(workflow)
        mutate(mutant)
        assert static_policy_violations(mutant) != [], label


# THE TOOLING TIER'S CI BINDING (025 T2502.2, 2026-10-03). The portable partition runs as a step of
# this job, where no step is allowed to fail; the check lives in `tooling-tests/conftest.py`
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
        "portable step allowed to fail": lambda w: _tooling_step(
            w, "static-diagnostics"
        ).__setitem__("continue-on-error", True),
        "powershell step made schedule-only": lambda w: _tooling_step(w, "required-ui").__setitem__(
            "if", "github.event_name == 'schedule'"
        ),
        "whole job allowed to fail": lambda w: w["jobs"]["static-diagnostics"].__setitem__(
            "continue-on-error", True
        ),
        "job if: false": lambda w: w["jobs"]["static-diagnostics"].__setitem__("if", False),
        "job if: always()": lambda w: w["jobs"]["required-ui"].__setitem__("if", "always()"),
        "step if: false": lambda w: _tooling_step(w, "static-diagnostics").__setitem__("if", False),
        "PYTEST_ADDOPTS on the step": lambda w: _tooling_step(w, "required-ui").__setitem__(
            "env", {"PYTEST_ADDOPTS": "--collect-only"}
        ),
        "PYTEST_ADDOPTS on the workflow": lambda w: w.__setitem__(
            "env", {"PYTEST_ADDOPTS": "--collect-only"}
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
    demoted = runner.replace(
        'Invoke-RequiredStep -Name "Tooling tests', 'Invoke-DiagnosticStep -Name "Tooling tests'
    )
    assert demoted != runner
    assert tooling_ci_binding(workflow, demoted), "not noticed: tooling step made diagnostic"
    assert tooling_ci_binding(workflow, runner.replace("'-q',", "'-q', '--noconftest',", 1))
