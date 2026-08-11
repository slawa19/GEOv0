from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]


def test_container_smoke_runs_on_schedule_and_manual_dispatch() -> None:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
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
    assert job["name"] == "Production-like container and schema smoke (scheduled/manual)"
    assert job["if"] == (
        "github.event_name == 'schedule' || "
        "github.event_name == 'workflow_dispatch'"
    )
