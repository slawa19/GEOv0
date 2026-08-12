from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]


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
