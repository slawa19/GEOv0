import copy
from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]

_SCHEDULE_ONLY_IF = (
    "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
)


def _workflow() -> dict:
    return yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    )


def _step_commands(job: dict) -> list[str]:
    return [
        " ".join(str(step.get("run", "")).split())
        for step in job.get("steps", [])
        if isinstance(step, dict) and step.get("run")
    ]


def conditional_or_tolerated_steps(job: dict) -> list[str]:
    """Every `run:` step of `job` that is conditional or allowed to fail, named with its reason.

    THIS FUNCTION EXISTS BECAUSE EVERY CHECK IN THIS FILE READ THE JOB AND NONE READ ITS STEPS
    (2026-09-22, Codex external review of `e2e1380..37fec08`). `test_both_halves_of_the_required_gate
    _run_on_every_pull_request` asserts `"if" not in backend`, which is a fact about the job and
    says nothing about what happens inside it: hanging `if: github.event_name == 'schedule'` on the
    migration preflight and on both PostgreSQL steps left that assertion true, left the service
    container and the commands where they were, and took PostgreSQL off every pull request.
    `continue-on-error: true` does the same to the failure, not to the run. `AGENTS.md` §15: the
    question is not whether a guard is there, it is whether there is a way around it.

    Factored out rather than asserted inline so the counter-check below can feed it a workflow that
    HAS those attributes and see it complain (§9, anti-vacuum).
    """

    findings: list[str] = []
    for step in job.get("steps", []):
        if not isinstance(step, dict) or not step.get("run"):
            continue
        name = step.get("name") or _step_commands({"steps": [step]})[0][:60]
        if step.get("if") is not None:
            findings.append(f"{name}: if: {step['if']}")
        if step.get("continue-on-error") in (True, "true"):
            findings.append(f"{name}: continue-on-error: true")
    return findings


def test_container_smoke_runs_on_schedule_and_manual_dispatch() -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    )

    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    schedule = triggers.get("schedule")
    assert isinstance(schedule, list)
    assert any(
        isinstance(item, dict) and str(item.get("cron", "")).strip()
        for item in schedule
    )

    job = workflow["jobs"]["container-smoke"]
    assert (
        job["name"] == "Production-like container and schema smoke (scheduled/manual)"
    )
    assert job["if"] == (
        "github.event_name == 'schedule' || " "github.event_name == 'workflow_dispatch'"
    )


def test_simulator_visual_e2e_installs_fixture_generator_dependencies() -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    )

    steps = workflow["jobs"]["simulator-visual-e2e"]["steps"]
    step_names = [step.get("name") for step in steps]
    backend_install = step_names.index("Install backend dependencies")
    visual_e2e = step_names.index("Run Simulator UI v2 E2E")

    assert backend_install < visual_e2e
    assert steps[backend_install]["run"] == (
        "python -m pip install -r requirements.txt -r requirements-dev.txt"
    )


# WHAT FOLLOWS IS NEW WORK, NOT A REPAIR (2026-09-21, T1701). Until today this module asserted
# nothing about the required gate at all: it pinned `container-smoke` and the step order of
# `simulator-visual-e2e`, and the one job every pull request depends on went undescribed. Stage 1 of
# programme 017 split it in two - `required-backend` on ubuntu with a PostgreSQL service,
# `required-ui` on Windows - and the split has a part that is easy to lose: the single-Alembic-head
# check used to live inside the UI half of `scripts/verify_local.ps1`, so without moving it the
# required gate would have stopped checking migrations on the job that owns them.


def test_both_halves_of_the_required_gate_run_on_every_pull_request() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert isinstance(triggers, dict) and "pull_request" in triggers

    backend = workflow["jobs"]["required-backend"]
    ui = workflow["jobs"]["required-ui"]

    # No `if:` at all - the two halves are unconditional, unlike the scheduled jobs below them.
    assert "if" not in backend
    assert "if" not in ui
    assert backend["runs-on"] == "ubuntu-latest"
    assert ui["runs-on"] == "windows-latest"

    # The pair must stay a partition: one half runs the backend, the other the UI, and neither
    # silently runs both or neither.
    backend_commands = [
        command
        for command in _step_commands(backend)
        if "verify_local.ps1" in command
    ]
    ui_commands = [
        command for command in _step_commands(ui) if "verify_local.ps1" in command
    ]

    assert backend_commands
    assert all("-BackendOnly" in command for command in backend_commands)
    assert all("-UiOnly" not in command for command in backend_commands)
    assert len(ui_commands) == 1
    assert "-UiOnly" in ui_commands[0]
    assert "-BackendOnly" not in ui_commands[0]


def test_the_required_backend_job_owns_the_postgres_service_and_the_alembic_head_check() -> None:
    workflow = _workflow()
    backend = workflow["jobs"]["required-backend"]

    service = backend["services"]["postgres"]
    assert service["image"] == "postgres:16"
    assert "5432:5432" in service["ports"]

    # The head check runs through the backend half of the runner script, which is the whole point
    # of moving it out of the UI block; the job also keeps the production migration entrypoint.
    verifier = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    backend_half = verifier.split("if (-not $UiOnly) {", 1)[1].split(
        "if (-not $BackendOnly) {", 1
    )[0]
    assert "scripts/check_alembic_heads.py" in backend_half
    assert "scripts/check_alembic_heads.py" in "\n".join(_step_commands(backend))
    assert "bash docker/docker-entrypoint.sh true" in "\n".join(
        _step_commands(backend)
    )


def test_no_step_of_either_required_half_is_conditional_or_allowed_to_fail() -> None:
    """The job being unconditional is not the same as its work being unconditional.

    A required gate is only required where its steps are: the migration preflight and the backend
    tier on PostgreSQL (one session since 017 stage 2c; it was three - the concurrency matrix, the
    marker tier and the default tier) all have to run on a pull request and all have to be able to
    fail it. This is the check that `"if" not in backend` above was mistaken
    for.
    """

    workflow = _workflow()
    for job_id in ("required-backend", "required-ui"):
        findings = conditional_or_tolerated_steps(workflow["jobs"][job_id])
        assert not findings, (
            f"Job '{job_id}' has steps that are conditional or allowed to fail: {findings}. The "
            "job itself carries no `if:`, so nothing else in this file would notice."
        )


def test_the_step_level_check_notices_both_ways_around_it() -> None:
    """COUNTER-CHECK for the function above, with the committed workflow as its control.

    Control first (§9): on the unmutated workflow the finder must be silent, otherwise the two
    refusals below would be evidence about a finder that complains at everything.
    """

    workflow = _workflow()
    backend = workflow["jobs"]["required-backend"]
    assert conditional_or_tolerated_steps(backend) == []

    scheduled_preflight = copy.deepcopy(backend)
    for step in scheduled_preflight["steps"]:
        if isinstance(step, dict) and "check_alembic_heads.py" in str(step.get("run", "")):
            step["if"] = "github.event_name == 'schedule'"
    findings = conditional_or_tolerated_steps(scheduled_preflight)
    assert findings and any("schedule" in finding for finding in findings), findings

    tolerated_postgres = copy.deepcopy(backend)
    mutated = 0
    for step in tolerated_postgres["steps"]:
        run = str(step.get("run", "")) if isinstance(step, dict) else ""
        if "verify_local.ps1" in run and "-BackendOnly" in run:
            step["continue-on-error"] = True
            mutated += 1
    assert mutated == 1, (
        f"expected the one backend-tier step to mutate, found {mutated}; the mutation would "
        "otherwise prove nothing"
    )
    findings = conditional_or_tolerated_steps(tolerated_postgres)
    assert len(findings) == 1 and all("continue-on-error" in f for f in findings), findings


def test_postgresql_is_no_longer_a_schedule_only_job() -> None:
    """The deleted `postgres` job must not come back, in name or in shape."""

    workflow = _workflow()
    jobs = workflow["jobs"]

    assert "postgres" not in jobs
    assert "required-quality" not in jobs

    scheduled_only = {
        job_id
        for job_id, job in jobs.items()
        if isinstance(job, dict) and job.get("if") == _SCHEDULE_ONLY_IF
    }
    # THE WHOLE BACKEND TIER RUNS IN `required-backend` AND NOWHERE ELSE (reworded 017 stage 2c).
    # Until then this asserted that no scheduled job asked for `-BackendMarker postgres`; the
    # parameter is gone, and a scheduled job would now take PostgreSQL off pull requests by running
    # the WHOLE tier - `-BackendOnly` without a selector - instead. A scheduled job may still run one
    # named selector (the simulator super-smoke does).
    whole_tier_on_schedule = sorted(
        job_id
        for job_id in scheduled_only
        for command in _step_commands(jobs[job_id])
        if "verify_local.ps1" in command
        and "-BackendOnly" in command
        and "-BackendSelector" not in command
    )
    assert not whole_tier_on_schedule, (
        f"Job(s) {whole_tier_on_schedule} run the whole backend tier on schedule/dispatch only; "
        "stage 1 of programme 017 moved it onto every pull request."
    )
    scheduled_selector_runs = [
        command
        for job_id in scheduled_only
        for command in _step_commands(jobs[job_id])
        if "verify_local.ps1" in command and "-BackendOnly" in command
    ]
    assert scheduled_selector_runs, (
        "no scheduled job runs verify_local.ps1 -BackendOnly at all, so the check above ran over "
        "nothing (anti-vacuum, AGENTS.md section 9); if the super-smoke moved, update this test"
    )
