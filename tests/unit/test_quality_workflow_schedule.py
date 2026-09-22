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
    for job_id in scheduled_only:
        commands = " ".join(_step_commands(jobs[job_id]))
        assert "-BackendMarker postgres" not in commands, (
            f"Job '{job_id}' runs the PostgreSQL marker tier but only on schedule/dispatch; "
            "stage 1 of programme 017 moved that tier onto every pull request."
        )
